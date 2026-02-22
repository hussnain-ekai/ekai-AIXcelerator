"""Langfuse business-logic scoring for agent pipeline quality.

Tracks measurable metrics at phase transitions to monitor pipeline health.
All scoring is observational — it does not affect agent behavior.

Scores tracked:
- discovery_column_coverage: % columns profiled
- discovery_fk_confidence_avg: average FK confidence
- brd_section_count: BRD completeness (0-7 sections)
- yaml_validation_first_pass: passed without retry (0/1)
- yaml_retry_count: retries needed (0-3)
- safety_net_activations: should trend to 0 after merge
- verification_issues_found: caught by verification tools
- pipeline_duration_seconds: total wall time
"""

import json
import logging
import time
from typing import Any

from config import get_effective_settings

logger = logging.getLogger(__name__)

# Module-level singleton — lazy init
_langfuse_client: Any = None
_langfuse_init_failed: bool = False


def _get_langfuse() -> Any | None:
    """Return shared Langfuse client, or None if not configured."""
    global _langfuse_client, _langfuse_init_failed

    if _langfuse_init_failed:
        return None
    if _langfuse_client is not None:
        return _langfuse_client

    settings = get_effective_settings()
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        _langfuse_init_failed = True
        return None

    try:
        from langfuse import Langfuse

        _langfuse_client = Langfuse()
        logger.info("Langfuse scoring client initialized")
        return _langfuse_client
    except Exception as e:
        logger.warning("Failed to initialize Langfuse scoring client: %s", e)
        _langfuse_init_failed = True
        return None


def _safe_score(trace_id: str, name: str, value: float | int, comment: str = "") -> None:
    """Score a trace, swallowing all errors."""
    client = _get_langfuse()
    if not client:
        return
    try:
        client.score(
            trace_id=trace_id,
            name=name,
            value=float(value),
            comment=comment or None,
        )
    except Exception as e:
        logger.debug("Langfuse score failed (name=%s): %s", name, e)


def score_discovery_quality(
    trace_id: str,
    pipeline_results: dict,
) -> None:
    """Score discovery phase quality metrics."""
    if not trace_id:
        return

    # Column coverage: % of columns that were profiled
    metadata = pipeline_results.get("metadata", [])
    profiles = pipeline_results.get("profiles", [])

    total_cols = sum(len(t.get("columns", [])) for t in metadata)
    profiled_cols = sum(len(p.get("columns", [])) for p in profiles)
    coverage = profiled_cols / total_cols if total_cols > 0 else 0.0

    _safe_score(trace_id, "discovery_column_coverage", coverage,
                f"{profiled_cols}/{total_cols} columns profiled")

    # FK confidence average
    relationships = pipeline_results.get("relationships", [])
    if relationships:
        confidences = [r.get("confidence", 0) for r in relationships]
        avg_conf = sum(confidences) / len(confidences)
        _safe_score(trace_id, "discovery_fk_confidence_avg", avg_conf,
                    f"{len(relationships)} relationships")

    # Table count
    _safe_score(trace_id, "discovery_table_count", len(metadata))

    logger.info("Scored discovery quality: coverage=%.2f, tables=%d", coverage, len(metadata))


async def score_brd_quality(
    trace_id: str,
    data_product_id: str,
) -> None:
    """Score BRD completeness after save_brd is called."""
    if not trace_id:
        return

    try:
        from tools.postgres_tools import _get_pool
        from services import postgres as pg

        pool = await _get_pool()
        rows = await pg.query(
            pool,
            """SELECT brd_json FROM business_requirements
               WHERE data_product_id = $1::uuid
               ORDER BY created_at DESC LIMIT 1""",
            data_product_id,
        )
        if not rows:
            return

        brd_text = ""
        brd_json = rows[0].get("brd_json")
        if isinstance(brd_json, dict):
            brd_text = brd_json.get("document", str(brd_json))
        elif isinstance(brd_json, str):
            try:
                parsed = json.loads(brd_json)
                brd_text = parsed.get("document", brd_json)
            except (json.JSONDecodeError, TypeError):
                brd_text = brd_json

        # Count sections present
        section_markers = [
            "SECTION 1:", "SECTION 2:", "SECTION 3:",
            "SECTION 4:", "SECTION 5:", "SECTION 6:", "SECTION 7:",
        ]
        section_count = sum(1 for m in section_markers if m in brd_text)
        _safe_score(trace_id, "brd_section_count", section_count,
                    f"{section_count}/7 sections present")

        # Check for placeholder text
        placeholders = ["[TBD]", "[TODO]", "[PLACEHOLDER]", "[FILL IN]"]
        placeholder_count = sum(brd_text.upper().count(p) for p in placeholders)
        if placeholder_count > 0:
            _safe_score(trace_id, "brd_placeholder_count", placeholder_count)

        logger.info("Scored BRD quality: sections=%d, placeholders=%d",
                     section_count, placeholder_count)

    except Exception as e:
        logger.debug("Failed to score BRD quality: %s", e)


def score_yaml_quality(
    trace_id: str,
    passed_first: bool,
    retry_count: int,
    verification_issues: int = 0,
) -> None:
    """Score YAML generation/validation quality."""
    if not trace_id:
        return

    _safe_score(trace_id, "yaml_validation_first_pass", 1.0 if passed_first else 0.0)
    _safe_score(trace_id, "yaml_retry_count", retry_count)
    if verification_issues > 0:
        _safe_score(trace_id, "verification_issues_found", verification_issues)

    logger.info("Scored YAML quality: first_pass=%s, retries=%d, issues=%d",
                passed_first, retry_count, verification_issues)


def score_safety_net(trace_id: str, activation_type: str) -> None:
    """Record safety net activation — should trend to 0 after agent merge."""
    if not trace_id:
        return
    _safe_score(trace_id, "safety_net_activations", 1.0, activation_type)
    logger.info("Scored safety net activation: %s", activation_type)


def score_pipeline_duration(trace_id: str, start_time: float) -> None:
    """Score total pipeline wall time."""
    if not trace_id:
        return
    duration = time.time() - start_time
    _safe_score(trace_id, "pipeline_duration_seconds", duration)
    logger.info("Scored pipeline duration: %.1fs", duration)


class PipelineTimer:
    """Context manager for tracking pipeline timing with Langfuse scoring."""

    def __init__(self, trace_id: str = ""):
        self.trace_id = trace_id
        self.start_time = 0.0
        self.phase_times: dict[str, float] = {}
        self._phase_start: float = 0.0
        self._current_phase: str = ""

    def start(self) -> None:
        self.start_time = time.time()

    def phase_started(self, phase: str) -> None:
        now = time.time()
        if self._current_phase:
            self.phase_times[self._current_phase] = now - self._phase_start
        self._current_phase = phase
        self._phase_start = now

    def finish(self) -> None:
        now = time.time()
        if self._current_phase:
            self.phase_times[self._current_phase] = now - self._phase_start
        if self.trace_id and self.start_time:
            score_pipeline_duration(self.trace_id, self.start_time)
            # Score individual phase durations
            client = _get_langfuse()
            if client:
                for phase, duration in self.phase_times.items():
                    _safe_score(self.trace_id, f"phase_{phase}_seconds", duration)
