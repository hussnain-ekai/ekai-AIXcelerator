"""Agent conversation endpoints — message handling, SSE streaming, and control actions."""

import asyncio
import base64
import hashlib
import io
import json
import logging
import re
import zipfile
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from config import get_settings
from models.schemas import (
    AgentStreamEvent,
    ApproveRequest,
    CitationReference,
    HybridAnswerContract,
    InvokeRequest,
    InvokeResponse,
    RecoveryAction,
    RetryRequest,
)
from services.supervisor_guardrails import (
    build_failure_recovery_message as _build_failure_recovery_message,
    classify_failure_category as _classify_failure_category,
)

logger = logging.getLogger(__name__)
_settings = get_settings()


def _sanitize_error_for_user(exc: Exception) -> str:
    """Convert internal errors to user-friendly messages.

    Business analysts should never see raw DB errors, stack traces, or
    technical identifiers. The full error is logged server-side already.
    """
    msg = str(exc)
    lower = msg.lower()

    if "foreignkeyviolation" in lower or "foreign key constraint" in lower:
        return "A data reference issue occurred. The operation was logged — please try again."
    if "uniqueviolation" in lower or "unique constraint" in lower:
        return "This record already exists. The operation has been noted."
    if "connection" in lower and ("refused" in lower or "timeout" in lower):
        return "A service connection issue occurred. Please try again in a moment."
    if "snowflake" in lower and ("timeout" in lower or "warehouse" in lower):
        return "The data warehouse is temporarily unavailable. Please try again."

    # Generic fallback — never expose raw exception text
    return (
        "Something went wrong while processing your request. Please try again or contact support."
    )


def _compose_failure_recovery_plan(
    *,
    phase: str,
    reason: str,
    timed_out: bool,
    last_tool: str | None,
) -> str:
    """Create a user-facing failure + recovery plan message."""
    if timed_out:
        safe_reason = "The step timed out before completion."
    else:
        safe_reason = _sanitize_error_for_user(Exception(reason))

    category = _classify_failure_category(reason, timed_out=timed_out)
    return _build_failure_recovery_message(
        phase=phase,
        category=category,
        reason=safe_reason,
        last_tool=last_tool,
    )


_PUBLISH_DEPLOYMENT_TOOLS: frozenset[str] = frozenset(
    {"create_semantic_view", "create_cortex_agent", "grant_agent_access", "log_agent_action"},
)
_NON_FATAL_PUBLISH_ERROR_MARKERS: tuple[str, ...] = (
    "explicit user approval is required before deployment",
    "publishing is blocked",
)


def _coerce_tool_result_payload(output: Any) -> dict[str, Any] | None:
    """Best-effort parse for JSON-like tool outputs."""
    candidate = output.content if hasattr(output, "content") else output

    if isinstance(candidate, dict):
        return candidate
    if not isinstance(candidate, str):
        return None

    text = candidate.strip()
    if not text or text[0] not in ("{", "["):
        return None

    try:
        parsed = json.loads(text)
    except Exception:
        return None

    return parsed if isinstance(parsed, dict) else None


def _extract_tool_error_from_payload(payload: dict[str, Any]) -> str | None:
    """Extract an error string from a structured tool payload if present."""
    raw_error = payload.get("error")
    if isinstance(raw_error, str) and raw_error.strip():
        return raw_error.strip()

    status = str(payload.get("status") or "").strip().lower()
    if status in {"error", "failed", "failure"}:
        for key in ("message", "detail", "reason"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "The tool reported a failure status."
    return None


def _is_tool_payload_success(payload: dict[str, Any]) -> bool:
    """Return True for successful structured tool results."""
    status = str(payload.get("status") or "").strip().lower()
    return status in {"success", "ok", "completed"}


def _infer_source_mode_from_text(text: str, phase: str) -> str:
    """Best-effort source lane inference for the answer contract."""
    lower = text.lower()
    has_document_signals = any(
        token in lower for token in ("document", "invoice", "pdf", "policy", "citation", "page ")
    )
    has_structured_signals = any(
        token in lower
        for token in ("table", "column", "metric", "kpi", "sql", "semantic view", "warehouse")
    )

    if has_document_signals and has_structured_signals:
        return "hybrid"
    if has_document_signals:
        return "document"
    if has_structured_signals:
        return "structured"

    # Default by mission phase when lexical signals are weak.
    if phase in {"discovery", "prepare", "transformation", "modeling", "generation", "validation"}:
        return "structured"
    return "unknown"


def _infer_exactness_state(text: str) -> str:
    lower = text.lower()
    if "insufficient evidence" in lower:
        return "insufficient_evidence"
    if any(token in lower for token in ("approximately", "approx", "estimate", "estimated")):
        return "estimated"
    # Heuristic: direct numeric result language usually indicates deterministic output.
    if re.search(r"[$€£]?\s?\d[\d,]*(\.\d+)?", text):
        return "validated_exact"
    return "not_applicable"


def _infer_confidence_and_state(text: str) -> tuple[str, str]:
    lower = text.lower()
    if "insufficient evidence" in lower or "cannot determine" in lower:
        return "abstain", "abstained_missing_evidence"
    if any(token in lower for token in ("conflict", "inconsistent", "disagree", "contradict")):
        return "abstain", "abstained_conflicting_evidence"
    if any(token in lower for token in ("warning", "caution", "partial")):
        return "medium", "answer_with_warnings"
    return "high", "answer_ready"


def _extract_citations_from_text(text: str) -> list[CitationReference]:
    """Extract lightweight citation hints from answer text."""
    citations: list[CitationReference] = []
    for page_match in re.findall(r"\bpage\s+(\d+)\b", text, flags=re.IGNORECASE):
        citations.append(
            CitationReference(
                citation_type="document_chunk",
                reference_id=f"page-{page_match}",
                label=f"Page {page_match}",
                page=int(page_match),
            )
        )
        if len(citations) >= 5:
            break
    return citations


def _coerce_citation_reference(value: Any) -> CitationReference | None:
    """Best-effort conversion of citation-like dict payloads."""
    if not isinstance(value, dict):
        return None

    citation_type = str(value.get("citation_type") or "").strip().lower()
    if citation_type not in {"sql", "document_chunk", "document_fact"}:
        return None

    reference_id = str(value.get("reference_id") or "").strip()
    if not reference_id:
        return None

    page_value = value.get("page")
    page: int | None = None
    if isinstance(page_value, int):
        page = page_value
    elif isinstance(page_value, str) and page_value.isdigit():
        page = int(page_value)

    score_value = value.get("score")
    score: float | None = None
    if score_value is not None:
        try:
            score = float(score_value)
        except (TypeError, ValueError):
            score = None

    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    try:
        return CitationReference(
            citation_type=citation_type,  # type: ignore[arg-type]
            reference_id=reference_id,
            label=(str(value.get("label")) if value.get("label") is not None else None),
            page=page,
            score=score,
            metadata=metadata,
        )
    except Exception:
        return None


def _coerce_recovery_action(value: Any) -> RecoveryAction | None:
    """Best-effort conversion of recovery action dict payloads."""
    if not isinstance(value, dict):
        return None
    action = str(value.get("action") or "").strip()
    description = str(value.get("description") or "").strip()
    if not action or not description:
        return None
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    try:
        return RecoveryAction(action=action, description=description, metadata=metadata)
    except Exception:
        return None


def _dedupe_citations(citations: list[CitationReference]) -> list[CitationReference]:
    """Deduplicate citations preserving original order."""
    seen: set[tuple[str, str, int | None]] = set()
    result: list[CitationReference] = []
    for citation in citations:
        key = (citation.citation_type, citation.reference_id, citation.page)
        if key in seen:
            continue
        seen.add(key)
        result.append(citation)
    return result


def _merge_answer_contract_hints(
    hints: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Merge tool-emitted contract hints into a single contract-ready structure."""
    if not hints:
        return None

    source_modes: set[str] = set()
    exactness_values: set[str] = set()
    confidence_values: set[str] = set()
    trust_values: set[str] = set()
    evidence_summaries: list[str] = []
    conflict_notes: list[str] = []
    citations: list[CitationReference] = []
    recovery_actions: list[RecoveryAction] = []
    merged_metadata: dict[str, Any] = {}

    for hint in hints:
        if not isinstance(hint, dict):
            continue

        mode = str(hint.get("source_mode") or "").strip().lower()
        if mode in {"structured", "document", "hybrid", "unknown"}:
            source_modes.add(mode)

        exactness = str(hint.get("exactness_state") or "").strip().lower()
        if exactness in {"validated_exact", "estimated", "insufficient_evidence", "not_applicable"}:
            exactness_values.add(exactness)

        confidence = str(hint.get("confidence_decision") or "").strip().lower()
        if confidence in {"high", "medium", "abstain"}:
            confidence_values.add(confidence)

        trust = str(hint.get("trust_state") or "").strip().lower()
        if trust in {
            "answer_ready",
            "answer_with_warnings",
            "abstained_missing_evidence",
            "abstained_conflicting_evidence",
            "blocked_access",
            "failed_recoverable",
            "failed_admin",
        }:
            trust_values.add(trust)

        summary = str(hint.get("evidence_summary") or "").strip()
        if summary:
            evidence_summaries.append(summary)

        for note in (
            hint.get("conflict_notes", []) if isinstance(hint.get("conflict_notes"), list) else []
        ):
            note_text = str(note).strip()
            if note_text:
                conflict_notes.append(note_text)

        raw_citations = hint.get("citations")
        if isinstance(raw_citations, list):
            for raw_citation in raw_citations:
                parsed = _coerce_citation_reference(raw_citation)
                if parsed:
                    citations.append(parsed)

        raw_actions = hint.get("recovery_actions")
        if isinstance(raw_actions, list):
            for raw_action in raw_actions:
                parsed = _coerce_recovery_action(raw_action)
                if parsed:
                    recovery_actions.append(parsed)

        metadata = hint.get("metadata")
        if isinstance(metadata, dict):
            merged_metadata.update(metadata)

    if not source_modes:
        source_mode = "unknown"
    elif len(source_modes) == 1:
        source_mode = next(iter(source_modes))
    else:
        source_mode = "hybrid"

    if "insufficient_evidence" in exactness_values:
        exactness_state = "insufficient_evidence"
    elif "validated_exact" in exactness_values:
        exactness_state = "validated_exact"
    elif "estimated" in exactness_values:
        exactness_state = "estimated"
    else:
        exactness_state = "not_applicable"

    if "blocked_access" in trust_values:
        confidence_decision = "abstain"
        trust_state = "blocked_access"
    elif "failed_admin" in trust_values:
        confidence_decision = "abstain"
        trust_state = "failed_admin"
    elif "failed_recoverable" in trust_values:
        confidence_decision = "abstain"
        trust_state = "failed_recoverable"
    elif (
        "abstain" in confidence_values
        or "abstained_conflicting_evidence" in trust_values
        or "abstained_missing_evidence" in trust_values
    ):
        confidence_decision = "abstain"
        trust_state = (
            "abstained_conflicting_evidence"
            if ("abstained_conflicting_evidence" in trust_values or conflict_notes)
            else "abstained_missing_evidence"
        )
    elif "answer_with_warnings" in trust_values or "medium" in confidence_values:
        confidence_decision = "medium"
        trust_state = (
            "answer_with_warnings" if "answer_with_warnings" in trust_values else "answer_ready"
        )
    else:
        confidence_decision = "high"
        trust_state = "answer_ready"

    deduped_citations = _dedupe_citations(citations)
    evidence_summary = evidence_summaries[0] if evidence_summaries else None
    if len(evidence_summaries) > 1:
        evidence_summary = " ".join(dict.fromkeys(evidence_summaries))[:320]

    if confidence_decision == "abstain" and not recovery_actions:
        recovery_actions.append(
            RecoveryAction(
                action="provide_more_evidence",
                description="Upload or activate additional evidence relevant to this question.",
                metadata={},
            )
        )

    return {
        "source_mode": source_mode,
        "exactness_state": exactness_state,
        "confidence_decision": confidence_decision,
        "trust_state": trust_state,
        "evidence_summary": evidence_summary,
        "conflict_notes": list(dict.fromkeys(conflict_notes))[:8],
        "citations": deduped_citations,
        "recovery_actions": recovery_actions,
        "metadata": merged_metadata,
    }


def _build_answer_contract_payload(
    *,
    phase: str,
    assistant_text: str,
    failure_message: str | None,
    last_tool: str | None,
    tool_contract_hints: list[dict[str, Any]] | None = None,
    query_route_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a normalized answer contract payload for UI trust rendering."""
    text = (assistant_text or "").strip()

    if failure_message:
        contract = HybridAnswerContract(
            source_mode="unknown",
            exactness_state="not_applicable",
            confidence_decision="abstain",
            trust_state="failed_recoverable",
            evidence_summary=failure_message,
            recovery_actions=[
                RecoveryAction(
                    action="retry_last_step",
                    description="Retry the last stable step or rerun the affected phase.",
                    metadata={"phase": phase, "last_tool": last_tool},
                )
            ],
            metadata={"phase": phase, "last_tool": last_tool},
        )
        payload = contract.model_dump(mode="json")
        return _apply_exactness_guardrail(payload, query_route_plan=query_route_plan)

    merged_hint = _merge_answer_contract_hints(tool_contract_hints or [])
    if merged_hint is not None:
        contract = HybridAnswerContract(
            source_mode=merged_hint["source_mode"],  # type: ignore[arg-type]
            exactness_state=merged_hint["exactness_state"],  # type: ignore[arg-type]
            confidence_decision=merged_hint["confidence_decision"],  # type: ignore[arg-type]
            trust_state=merged_hint["trust_state"],  # type: ignore[arg-type]
            evidence_summary=merged_hint.get("evidence_summary") or (text[:320] if text else None),
            conflict_notes=merged_hint.get("conflict_notes") or [],
            citations=merged_hint.get("citations") or [],
            recovery_actions=merged_hint.get("recovery_actions") or [],
            metadata={
                "phase": phase,
                "last_tool": last_tool,
                **(
                    merged_hint.get("metadata")
                    if isinstance(merged_hint.get("metadata"), dict)
                    else {}
                ),
            },
        )
        payload = contract.model_dump(mode="json")
        return _apply_exactness_guardrail(payload, query_route_plan=query_route_plan)

    source_mode = _infer_source_mode_from_text(text, phase)
    exactness_state = _infer_exactness_state(text)
    confidence, trust_state = _infer_confidence_and_state(text)
    citations = _extract_citations_from_text(text)

    recovery_actions: list[RecoveryAction] = []
    if confidence == "abstain":
        recovery_actions.append(
            RecoveryAction(
                action="provide_more_evidence",
                description="Upload or activate additional evidence relevant to this question.",
                metadata={"phase": phase},
            )
        )

    contract = HybridAnswerContract(
        source_mode=source_mode,  # type: ignore[arg-type]
        exactness_state=exactness_state,  # type: ignore[arg-type]
        confidence_decision=confidence,  # type: ignore[arg-type]
        trust_state=trust_state,  # type: ignore[arg-type]
        evidence_summary=text[:320] if text else None,
        citations=citations,
        recovery_actions=recovery_actions,
        metadata={"phase": phase, "last_tool": last_tool},
    )
    payload = contract.model_dump(mode="json")
    return _apply_exactness_guardrail(payload, query_route_plan=query_route_plan)


def _route_plan_requires_exact_evidence(query_route_plan: dict[str, Any] | None) -> bool:
    """True when planner classified the question as exactness-sensitive."""
    if not isinstance(query_route_plan, dict):
        return False
    if bool(query_route_plan.get("requires_exact_evidence")):
        return True
    intent = str(query_route_plan.get("intent") or "").strip().lower()
    return intent == "transaction_lookup"


def _has_deterministic_exact_citation(citations: list[dict[str, Any]]) -> bool:
    """True only when evidence includes deterministic SQL or fact-row citations."""
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        ctype = str(citation.get("citation_type") or "").strip().lower()
        if ctype in {"sql", "document_fact"}:
            return True
    return False


def _apply_exactness_guardrail(
    payload: dict[str, Any],
    *,
    query_route_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    """Enforce deterministic exactness policy for exact-number asks.

    HYB-AI-004 policy:
    - Exactness-sensitive asks MUST have SQL/document_fact citations.
    - Never emit ``validated_exact`` from chunk similarity or free-form text.
    - If deterministic evidence is missing, abstain with a recovery path.
    """
    if not isinstance(payload, dict):
        return payload

    try:
        from config import get_effective_settings as _get_effective_settings

        if not _get_effective_settings().feature_exactness_guardrail:
            return payload
    except Exception:
        # Fail open to avoid breaking answer delivery if config loading fails.
        pass

    citations_raw = payload.get("citations")
    citations = citations_raw if isinstance(citations_raw, list) else []
    citation_dicts = [c for c in citations if isinstance(c, dict)]
    has_deterministic = _has_deterministic_exact_citation(citation_dicts)

    exactness_state = str(payload.get("exactness_state") or "not_applicable")
    requires_exact = _route_plan_requires_exact_evidence(query_route_plan)
    should_enforce = requires_exact or exactness_state == "validated_exact"

    if not should_enforce or has_deterministic:
        return payload

    payload["exactness_state"] = "insufficient_evidence"
    payload["confidence_decision"] = "abstain"
    if str(payload.get("trust_state") or "") != "abstained_conflicting_evidence":
        payload["trust_state"] = "abstained_missing_evidence"

    fallback_summary = (
        "Exact value requested but deterministic SQL/fact evidence was not available."
    )
    summary = str(payload.get("evidence_summary") or "").strip()
    if not summary:
        payload["evidence_summary"] = fallback_summary
    elif "deterministic" not in summary.lower():
        payload["evidence_summary"] = f"{summary} {fallback_summary}".strip()

    actions = payload.get("recovery_actions")
    recovery_actions = actions if isinstance(actions, list) else []
    if not any(
        isinstance(action, dict)
        and str(action.get("action") or "") == "provide_deterministic_source"
        for action in recovery_actions
    ):
        recovery_actions.append(
            {
                "action": "provide_deterministic_source",
                "description": "Provide or activate SQL rows/document fact evidence for an exact value.",
                "metadata": {
                    "requires_exact_evidence": True,
                    "route_intent": (
                        str(query_route_plan.get("intent") or "unknown")
                        if isinstance(query_route_plan, dict)
                        else "unknown"
                    ),
                },
            }
        )
    payload["recovery_actions"] = recovery_actions
    return payload


def _extract_yaml_from_text(text: str) -> str | None:
    """Extract YAML content from LLM text output (safety net helper)."""
    # Try markdown yaml block
    match = re.search(r"```ya?ml\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Try generic code block with YAML content
    match = re.search(r"```\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if match:
        content = match.group(1).strip()
        if "tables:" in content and "base_table:" in content:
            return content
    # Try raw YAML (look for name: + tables: pattern)
    match = re.search(r"(name:\s+\S+.*?tables:.*)", text, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        if "base_table:" in candidate:
            return candidate
    return None


# Discovery trigger constant
DISCOVERY_TRIGGER = "__START_DISCOVERY__"
RERUN_DISCOVERY_TRIGGER = "__RERUN_DISCOVERY__"

router = APIRouter(prefix="/agent", tags=["Agent"])

# In-memory store for active streaming sessions.
# Maps session_id -> asyncio.Queue of SSE events.
_active_streams: dict[str, asyncio.Queue[dict | None]] = {}


def _infer_model_builder_phase_from_task_description(description: str) -> str | None:
    """Infer model-builder phase from explicit orchestrator markers only.

    Do NOT use broad keyword matching (e.g. "generate") because the task
    description includes copied conversation history that can contain unrelated
    words and cause false phase transitions.
    """
    desc = (description or "").lower()

    # Validation mode
    if re.search(r"\bstep\s*7\b", desc):
        return "validation"

    # Generation mode (semantic model/YAML)
    if "yaml revision mode" in desc or re.search(r"\bstep\s*4\b", desc):
        return "generation"

    # Requirements/BRD mode
    if (
        "brd revision mode" in desc
        or re.search(r"\bstep\s*1\b", desc)
        or "continue." in desc
        or re.search(r"\bround\s+\d+\b", desc)
    ):
        return "requirements"

    return None


def _map_phase_to_status(phase: str) -> str | None:
    """Map internal/live phases to persisted data_products.status values."""
    return {
        "idle": "discovery",
        "discovery": "discovery",
        "prepare": "discovery",
        "transformation": "discovery",
        "requirements": "requirements",
        "modeling": "generation",  # internal phase folded into external generation status
        "generation": "generation",
        "validation": "validation",
        "publishing": "validation",  # closest pre-publish status in current enum
        "explorer": "published",
    }.get(phase)


def _is_explicit_publish_approval(message: str, allow_bare_ack: bool) -> bool:
    """Return True only for explicit affirmative publish intent."""
    text = re.sub(r"\s+", " ", (message or "").strip().lower())
    if not text:
        return False

    # Strong publish-intent phrases are always accepted.
    if re.search(
        r"\b(publish|proceed with publish(?:ing)?|go ahead and publish|deploy now|ship it)\b",
        text,
    ):
        return True

    if not allow_bare_ack:
        return False

    # During an explicit publishing phase, short yes/no style acknowledgements are valid.
    return text in {
        "yes",
        "y",
        "yeah",
        "yep",
        "sure",
        "ok",
        "okay",
        "go ahead",
        "proceed",
        "do it",
        "approved",
    }


_REQ_MOVE_PATTERNS: tuple[str, ...] = (
    r"\b(move|go|proceed|advance|continue|start|begin)\b.*\brequirements?\b",
    r"\b(move|go|proceed|advance|continue|start|begin)\b.*\bbrd\b",
    r"\brequirements?\b.*\b(move|go|proceed|advance|continue|start|begin)\b",
    r"\bdefine\b.*\b(requirements?|brd)\b",
)

_GENERIC_PROCEED_WORDS: set[str] = {
    "proceed",
    "please proceed",
    "go ahead",
    "continue",
    "looks good",
    "looks good please proceed",
    "let's proceed",
    "lets proceed",
}

_AUTOPILOT_MARKERS: tuple[str, ...] = (
    "end to end",
    "end-to-end",
    "all remaining stages",
    "proceed automatically",
    "automatically",
    "without pause",
    "no pause",
    "no pauses",
    "do not pause",
    "don't pause",
    "do not stop",
    "don't stop",
    "without confirmation",
    "do not stop for confirmation",
    "dont stop for confirmation",
    "autopilot",
)

_AUTOPILOT_ACTION_PATTERN = re.compile(
    r"\b(proceed|continue|run|complete|finish|build|generate|validate|publish|deploy|test)\b"
)

_ANALYSIS_ONLY_NO_PUBLISH_MARKERS: tuple[str, ...] = (
    "skip publishing",
    "skip publish",
    "without publishing",
    "do not publish",
    "don't publish",
    "do not deploy",
    "don't deploy",
    "skip deployment",
    "analysis mode only",
    "stay in analysis mode",
)


_AGENT_INSTRUCTION_PATTERNS: tuple[str, ...] = (
    r"\b(update|change|modify|revise|rewrite|adjust|tune|improve)\b.*\b(agent|ai agent|cortex agent)\b.*\b(instruction|instructions|prompt|behavior|behaviour|tone|response|disclaimer|guardrail)\b",
    r"\b(agent|ai agent|cortex agent)\b.*\b(instruction|instructions|prompt|behavior|behaviour|tone|response|disclaimer|guardrail)\b.*\b(update|change|modify|revise|rewrite|adjust|tune|improve)\b",
    r"\b(update|change|modify|revise|rewrite|adjust|tune|improve)\b.*\b(instruction|instructions|prompt|behavior|behaviour|tone|response|disclaimer|guardrail)\b",
    r"\bhow\s+(should|must)\s+(the\s+)?(agent|ai)\s+(respond|answer|behave)\b",
)

_MODEL_OR_REQUIREMENTS_CHANGE_PATTERNS: tuple[str, ...] = (
    r"\b(brd|requirement|requirements)\b",
    r"\bsemantic\s+model\b",
    r"\byaml\b",
    r"\b(metric|metrics)\b",
    r"\b(dimension|dimensions)\b",
    r"\b(relationship|relationships)\b",
    r"\b(table|tables|column|columns|field|fields)\b",
    r"\b(validation|validate)\b",
)


def _normalize_user_text(message: str) -> str:
    return re.sub(r"\s+", " ", (message or "").strip().lower())


def _classify_query_intent(message: str) -> tuple[str, str]:
    """Classify question intent for hybrid routing decisions."""
    text = _normalize_user_text(message)
    if not text:
        return "unknown", "No user query content was provided."

    has_metric = any(
        token in text
        for token in (
            "kpi",
            "metric",
            "measure",
            "trend",
            "growth",
            "decline",
            "count",
            "average",
            "sum",
            "total",
            "compare",
            "comparison",
            "top ",
            "bottom ",
            "how many",
            "how much",
            "percentage",
            "rate ",
            "frequency",
            "number of ",
            "most common",
            "least common",
            "highest",
            "lowest",
            "ranking",
        )
    )
    has_transaction = any(
        token in text
        for token in (
            "invoice",
            "purchase order",
            "po ",
            "order id",
            "transaction",
            "receipt",
            "part ",
            "spare",
            "serial",
            "sku",
            "line item",
            "how much",
            "exact amount",
            "exact value",
            "exact number",
        )
    )
    has_policy = any(
        token in text
        for token in (
            "policy",
            "manual",
            "guideline",
            "procedure",
            "requirement",
            "compliance",
            "contract",
            "overview report",
            "outlook report",
            "document says",
            "report says",
            "recommend",
            "recommendation",
            "advisory",
            "bulletin",
            "regulation",
            "standard",
            "guidance",
            "best practice",
            "safety management",
            "corrective action",
        )
    )
    has_document_signal = any(
        token in text
        for token in (
            "document",
            "pdf",
            "file",
            "report",
            "notes",
            "memo",
            "investigation",
            "finding",
            "assessment",
            "analysis report",
            "ntsb",
            "faa",
        )
    )

    if has_metric and (has_transaction or has_policy or has_document_signal):
        return "hybrid", "Question combines numerical metrics with document/business context."
    if has_transaction:
        return (
            "transaction_lookup",
            "Question requests exact transactional or identifier-level values.",
        )
    if has_policy or has_document_signal:
        return "policy", "Question is primarily about document policy/context interpretation."
    if has_metric:
        return "metric", "Question focuses on structured metric/KPI analysis."
    return "unknown", "Could not confidently infer a specific query intent class."


def _build_query_route_plan(
    message: str,
    *,
    current_phase: str,
    already_published: bool,
    has_documents: bool = False,
) -> dict[str, Any]:
    """Build a planner object persisted for hybrid-routing auditability."""
    intent, rationale = _classify_query_intent(message)

    if intent == "transaction_lookup":
        lanes = ["document_facts", "structured_sql"]
        if already_published:
            lanes.insert(1, "structured_agent")
        requires_exact = True
    elif intent == "policy":
        lanes = ["document_chunks"]
        requires_exact = False
    elif intent == "metric":
        lanes: list[str] = ["structured_agent" if already_published else "structured_sql"]
        # For document-enabled products, always include document search
        # alongside structured queries — the structured model may lack
        # dimensions the user is asking about (e.g. cause factors, recommendations).
        if has_documents:
            lanes.append("document_chunks")
        requires_exact = False
    elif intent == "hybrid":
        lanes = ["document_facts", "document_chunks"]
        lanes.append("structured_agent" if already_published else "structured_sql")
        requires_exact = any(
            token in _normalize_user_text(message)
            for token in ("exact", "exactly", "precise", "specific number", "how much")
        )
    else:
        lanes = ["structured_sql", "document_chunks"]
        requires_exact = False

    # Safety net: hybrid/document products always include document lanes
    if has_documents and "document_chunks" not in lanes:
        lanes.append("document_chunks")

    return {
        "version": "hyb-ai-003-v1",
        "intent": intent,
        "rationale": rationale,
        "current_phase": current_phase,
        "lanes": lanes,
        "requires_exact_evidence": requires_exact,
        "conflict_policy": "abstain_on_conflict",
    }


def _summarize_tool_input_for_trace(tool_name: str, tool_input: Any) -> dict[str, Any]:
    """Create compact, non-sensitive tool-input summary for audit logs."""
    summary: dict[str, Any] = {"tool": tool_name}
    if not isinstance(tool_input, dict):
        return summary

    if tool_name == "execute_rcr_query":
        sql_text = str(tool_input.get("sql") or "").strip()
        if sql_text:
            summary["sql_hash"] = hashlib.sha1(sql_text.encode("utf-8")).hexdigest()[:12]
            summary["has_where"] = " where " in sql_text.lower()
            summary["has_join"] = " join " in sql_text.lower()
    elif tool_name == "query_document_facts":
        question = str(tool_input.get("question") or "").strip()
        if question:
            summary["question_hash"] = hashlib.sha1(question.encode("utf-8")).hexdigest()[:12]
        summary["limit"] = tool_input.get("limit")
    elif tool_name == "search_document_chunks":
        query_text = str(tool_input.get("query_text") or "").strip()
        if query_text:
            summary["query_hash"] = hashlib.sha1(query_text.encode("utf-8")).hexdigest()[:12]
        summary["limit"] = tool_input.get("limit")
    elif tool_name == "query_cortex_agent":
        summary["agent"] = str(tool_input.get("agent_fqn") or "")[:120]
        question = str(tool_input.get("question") or "").strip()
        if question:
            summary["question_hash"] = hashlib.sha1(question.encode("utf-8")).hexdigest()[:12]
    else:
        summary["input_keys"] = sorted(str(key) for key in tool_input.keys())[:10]

    return summary


def _resolve_llm_signature_for_audit(settings_obj: Any) -> dict[str, str]:
    """Return provider/model signature with a stable short hash for audit traces."""
    provider = str(getattr(settings_obj, "llm_provider", "") or "unknown").strip() or "unknown"

    model = ""
    if provider == "snowflake-cortex":
        model = str(getattr(settings_obj, "cortex_model", "") or "").strip()
    elif provider == "vertex-ai":
        model = str(getattr(settings_obj, "vertex_model", "") or "").strip()
    elif provider == "openai":
        model = str(getattr(settings_obj, "openai_model", "") or "").strip()
    elif provider == "anthropic":
        model = str(getattr(settings_obj, "anthropic_model", "") or "").strip()
    elif provider == "azure-openai":
        model = str(getattr(settings_obj, "azure_openai_deployment", "") or "").strip()

    if not model:
        model = "unknown"

    model_hash = hashlib.sha256(f"{provider}:{model}".encode("utf-8")).hexdigest()[:16]
    return {"provider": provider, "model": model, "model_hash": model_hash}


def _is_requirements_transition_intent(message: str) -> bool:
    """Detect explicit user intent to move from discovery to requirements."""
    text = _normalize_user_text(message)
    if not text:
        return False
    if text in _GENERIC_PROCEED_WORDS:
        return True
    return any(re.search(p, text) for p in _REQ_MOVE_PATTERNS)


def _is_end_to_end_autopilot_intent(message: str) -> bool:
    """Detect explicit user intent to continue the full pipeline without pauses."""
    text = _normalize_user_text(message)
    if not text:
        return False

    has_marker = any(marker in text for marker in _AUTOPILOT_MARKERS)
    if not has_marker:
        return False

    return bool(_AUTOPILOT_ACTION_PATTERN.search(text))


def _is_analysis_only_no_publish_intent(message: str) -> bool:
    """Detect explicit user intent to answer now without publishing/deployment."""
    text = _normalize_user_text(message)
    if not text:
        return False

    has_no_publish = any(marker in text for marker in _ANALYSIS_ONLY_NO_PUBLISH_MARKERS)
    if not has_no_publish:
        return False

    asks_to_answer = any(
        token in text
        for token in (
            "answer",
            "question",
            "analyze",
            "analysis",
            "directly",
            "from available tables",
            "with citations",
        )
    )
    return asks_to_answer


def _is_agent_instruction_update_intent(message: str) -> bool:
    """Detect user intent to update the published AI agent behavior/instructions."""
    text = _normalize_user_text(message)
    if not text:
        return False
    return any(re.search(p, text) for p in _AGENT_INSTRUCTION_PATTERNS)


def _is_model_or_requirements_change_intent(message: str) -> bool:
    """Detect intent that should route to model/requirements updates, not agent instructions."""
    text = _normalize_user_text(message)
    if not text:
        return False
    return any(re.search(p, text) for p in _MODEL_OR_REQUIREMENTS_CHANGE_PATTERNS)


def _is_post_publish_agent_instruction_only_intent(message: str) -> bool:
    """True when user asks to update agent behavior without asking for model/BRD changes."""
    if not _is_agent_instruction_update_intent(message):
        return False
    return not _is_model_or_requirements_change_intent(message)


def _requirements_entry_ready(snapshot: dict[str, Any]) -> tuple[bool, str]:
    """Return whether moving to requirements is valid based on workflow state."""
    has_quality_report = bool(snapshot.get("quality_report_exists"))
    data_tier = (snapshot.get("data_tier") or "").lower()
    transformation_done = bool(snapshot.get("transformation_done"))

    if not has_quality_report:
        return False, "Discovery profiling is not complete yet."

    # Conservative gate: if data_tier is unknown, block until classification
    # completes. This prevents skipping transformations for non-gold data.
    if not data_tier:
        return False, "Data tier classification not yet available."

    if data_tier in {"silver", "bronze", "raw"} and not transformation_done:
        return False, "Data cleanup is required before requirements."

    return True, "Requirements entry conditions satisfied."


def _evaluate_supervisor_transition(
    message: str,
    snapshot: dict[str, Any],
    *,
    already_published: bool = False,
) -> tuple[str | None, str | None]:
    """Deterministically evaluate supervisor-enforced phase transitions."""
    current_phase = (snapshot.get("current_phase") or "discovery").lower()
    if current_phase in {"idle", ""}:
        current_phase = "discovery"

    if already_published and _is_post_publish_agent_instruction_only_intent(message):
        return "publishing", "Post-publish agent instruction update requested."

    wants_requirements = _is_requirements_transition_intent(message)
    if current_phase in {"discovery", "prepare", "transformation"} and wants_requirements:
        is_ready, reason = _requirements_entry_ready(snapshot)
        if is_ready:
            return "requirements", reason
        return None, reason

    return None, None


def _build_supervisor_contract(
    snapshot: dict[str, Any],
    user_message: str,
    transition_target: str | None,
    transition_reason: str | None,
    *,
    data_product_id: str,
    already_published: bool,
    forced_subagent: str | None = None,
    forced_intent: str | None = None,
    run_mode: str | None = None,
    publish_preapproved: bool = False,
    document_context: dict[str, Any] | None = None,
    query_route_plan: dict[str, Any] | None = None,
) -> str:
    """Build a compact supervisor contract injected into orchestrator input."""
    current_phase = snapshot.get("current_phase") or "discovery"
    data_tier = snapshot.get("data_tier") or "unknown"
    validation_status = snapshot.get("validation_status") or "none"

    lines = [
        "[SUPERVISOR CONTEXT CONTRACT — INTERNAL, NEVER SHOW TO USER]",
        f"current_phase={current_phase}",
        f"data_product_id={data_product_id}",
        f"already_published={already_published}",
        f"data_tier={data_tier}",
        f"product_type={snapshot.get('product_type') or 'structured'}",
        f"has_documents={bool(snapshot.get('has_documents'))}",
        *(
            [
                f"doc_chunks_count={snapshot['doc_chunks_count']}",
                f"doc_facts_count={snapshot['doc_facts_count']}",
            ]
            if snapshot.get("doc_chunks_count")
            else []
        ),
        f"data_description_exists={bool(snapshot.get('data_description_exists'))}",
        f"transformation_done={bool(snapshot.get('transformation_done'))}",
        f"brd_exists={bool(snapshot.get('brd_exists'))}",
        f"semantic_view_exists={bool(snapshot.get('semantic_view_exists'))}",
        f"validation_status={validation_status}",
        *(
            [f"published_agent_fqn={snapshot['published_agent_fqn']}"]
            if snapshot.get("published_agent_fqn")
            else []
        ),
        "internal_ids_available=true",
        "id_request_policy=never ask user for data_product_id/session_id/uuid; use context values silently",
        (
            "communication_policy=business labels first; reveal technical detail only if user explicitly asks"
        ),
        (
            "requirements_policy=ask focused high-signal questions, avoid generic fluff and info dumps, continue until requirements are complete"
        ),
    ]

    if document_context:
        context_step = str(document_context.get("step") or "").strip()
        context_version = document_context.get("context_version")
        active_items = document_context.get("active_items") or []
        candidate_count = int(document_context.get("candidate_count") or 0)

        if context_step:
            lines.append(f"context_step={context_step}")
        if context_version is not None:
            lines.append(f"context_version={context_version}")

        lines.append(
            "document_context_policy=use active context evidence for the current mission step; "
            "candidate/reference evidence is optional unless user activates it"
        )
        lines.append(f"context_active_items={len(active_items)}")
        lines.append(f"context_candidate_items={candidate_count}")

        for idx, item in enumerate(active_items[:8], start=1):
            filename = str(item.get("filename") or "document")
            doc_kind = str(item.get("doc_kind") or "reference")
            summary = str(item.get("summary") or "").replace("\n", " ").strip()
            summary = re.sub(r"\\s+", " ", summary)[:240]

            payload = item.get("payload")
            payload_text = ""
            if isinstance(payload, dict):
                table_names = payload.get("table_names")
                metric_hints = payload.get("metric_hints")

                table_str = ""
                metric_str = ""
                if isinstance(table_names, list):
                    table_str = ", ".join(str(v) for v in table_names[:5])
                if isinstance(metric_hints, list):
                    metric_str = ", ".join(str(v) for v in metric_hints[:3])

                if table_str:
                    payload_text += f" tables={table_str};"
                if metric_str:
                    payload_text += f" metric_hints={metric_str};"

            lines.append(
                f"context_item_{idx}={doc_kind}|{filename}|{summary}{payload_text}".strip()
            )

    if transition_target:
        lines.append(f"forced_transition={current_phase}->{transition_target}")
    if transition_reason:
        lines.append(f"transition_reason={transition_reason}")
    if run_mode:
        lines.append(f"run_mode={run_mode}")
        if run_mode == "autopilot_end_to_end":
            lines.append("pause_policy=skip_optional_review_pauses")
    if query_route_plan:
        intent = str(query_route_plan.get("intent") or "unknown")
        lines.append(f"query_intent={intent}")
        try:
            route_plan_json = json.dumps(query_route_plan, separators=(",", ":"), ensure_ascii=True)
        except Exception:
            route_plan_json = "{}"
        lines.append(f"query_route_plan={route_plan_json[:1000]}")
        lines.append(
            "planner_policy=follow query_route_plan lanes and rationale unless a hard error requires fallback"
        )
    agent_fqn = snapshot.get("published_agent_fqn")
    if agent_fqn:
        lines.append(f"published_agent_fqn={agent_fqn}")
    lines.append(f"publish_approval={'preapproved' if publish_preapproved else 'required'}")
    if forced_subagent:
        lines.append(f"forced_subagent={forced_subagent}")
    if forced_intent:
        lines.append(f"forced_intent={forced_intent}")

    lines.append("[END SUPERVISOR CONTEXT CONTRACT]")
    lines.append(f"[USER MESSAGE]\n{user_message}")
    return "\n".join(lines)


# ── Artifact appendix injection ──────────────────────────────

# Phase → which artifact keys to inject
_PHASE_ARTIFACT_MAP: dict[str, list[str]] = {
    "requirements": ["data_description"],
    "generation": ["data_description", "brd"],
    "modeling": ["data_description", "brd"],
    "validation": ["brd", "semantic_view"],
    "publishing": ["brd", "semantic_view"],
}


def _format_artifact_content(key: str, raw: Any) -> str:
    """Extract human-readable text from a stored artifact value.

    - BRD: stored as ``{"document": "<text>"}`` → extract the text.
    - Data Description: structured JSON dict/list → pretty-print as indented JSON.
    - Semantic View: stored as TEXT (YAML string) → pass through.
    """
    if raw is None:
        return ""

    # Handle JSON strings that need parsing first
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            # Already a plain string (e.g. YAML text) — return as-is
            return raw.strip()

    if isinstance(raw, dict):
        # BRD is stored as {"document": "<text>"}
        if "document" in raw:
            return str(raw["document"]).strip()
        # Otherwise structured JSON — pretty-print
        return json.dumps(raw, indent=2, ensure_ascii=False)

    if isinstance(raw, list):
        return json.dumps(raw, indent=2, ensure_ascii=False)

    return str(raw).strip()


def _build_artifact_appendices(
    snapshot: dict[str, Any],
    current_phase: str,
) -> str:
    """Build formatted artifact appendix text for injection into the orchestrator message.

    Returns an empty string when no artifacts are relevant for the current phase.
    """
    artifact_keys = _PHASE_ARTIFACT_MAP.get(current_phase)
    if not artifact_keys:
        return ""

    _LABELS = {
        "data_description": "DATA DESCRIPTION",
        "brd": "BUSINESS REQUIREMENTS DOCUMENT",
        "semantic_view": "SEMANTIC VIEW YAML",
    }
    _CONTENT_KEYS = {
        "data_description": "data_description_content",
        "brd": "brd_content",
        "semantic_view": "semantic_view_content",
    }
    _VERSION_KEYS = {
        "data_description": "data_description_version",
        "brd": "brd_version",
        "semantic_view": "semantic_view_version",
    }

    sections: list[str] = []
    total_chars = 0

    for key in artifact_keys:
        raw = snapshot.get(_CONTENT_KEYS[key])
        if raw is None:
            continue
        text = _format_artifact_content(key, raw)
        if not text:
            continue
        version = snapshot.get(_VERSION_KEYS[key], "?")
        label = _LABELS[key]
        sections.append(
            f"[ARTIFACT APPENDIX: {label} (v{version})]\n{text}\n[END {label}]"
        )
        total_chars += len(text)

    if not sections:
        return ""

    header = (
        "\n\n"
        "═══════════════════════════════════════════════════════\n"
        "ARTIFACT APPENDICES (pre-loaded — COPY VERBATIM into task descriptions)\n"
        "═══════════════════════════════════════════════════════\n\n"
    )
    result = header + "\n\n".join(sections)
    logger.info(
        "Injected artifact appendices: phase=%s artifacts=%d chars=%d",
        current_phase,
        len(sections),
        total_chars,
    )
    return result


def _is_non_fatal_publish_tool_error(
    *,
    tool_name: str,
    error_text: str,
    publish_completed: bool,
) -> bool:
    """Return True when a publishing tool error should not hard-abort the stream."""
    normalized_error = (error_text or "").lower()

    if any(marker in normalized_error for marker in _NON_FATAL_PUBLISH_ERROR_MARKERS):
        return True

    # Access grants are helpful but not required to complete semantic view/agent deployment
    # in the same user role context. Do not abort the workflow on grant-only issues.
    if tool_name == "grant_agent_access":
        return True

    # If the agent already exists, subsequent deployment sub-steps may fail due to
    # idempotency/race conditions. Keep stream alive and let the turn finish.
    if publish_completed and tool_name in {"log_agent_action", "upload_artifact"}:
        return True

    return False


def _is_internal_reasoning_leak(text: str) -> bool:
    """Detect obvious internal-orchestration leakage in assistant output."""
    lower = text.lower()
    leak_patterns = (
        "task()",
        "`task`",
        "subagent",
        "auto-chain",
        "pause =",
        "rule 6",
        "rule 10",
        "tool call",
        "i will call the task",
        "i need to make sure i don't forget",
        "system prompt says",
        "[internal",
        "orchestration events",
        "execution status from orchestration",
        "chain-of-thought",
    )
    return any(p in lower for p in leak_patterns)


def _sanitize_assistant_text(text: str) -> str:
    """Supervisor-level output sanitizer for persona-safe chat rendering.

    Delegates to the shared sanitizer in supervisor_guardrails and adds
    router-specific rules (e.g. data_product_id line stripping).
    """
    from services.supervisor_guardrails import sanitize_assistant_text as _shared_sanitize

    if not text:
        return ""

    # Apply shared sanitizer (markdown stripping, jargon replacement, leak detection).
    text = _shared_sanitize(text)

    # Router-specific: also drop lines exposing IDs.
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        if re.search(r"\b(data[_ ]product[_ ]id|session[_ ]id)\b", line, flags=re.IGNORECASE):
            continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    return text.strip()


def _sanitize_token_chunk(chunk: str) -> str:
    """Lightweight token sanitizer for streaming path."""
    if not chunk:
        return ""
    chunk = chunk.replace("`", "")
    chunk = chunk.replace("**", "")
    return chunk


def _extract_reasoning_update(text: str) -> str | None:
    """Extract one short, user-safe sentence from assistant output for live progress."""
    if not text:
        return None

    safe = _sanitize_assistant_text(text)
    if not safe:
        return None

    safe = re.sub(r"\s+", " ", safe).strip(" -•")
    if len(safe) < 24:
        return None
    if _is_internal_reasoning_leak(safe):
        return None

    # Emit only completed sentences to avoid noisy token-by-token churn.
    # Prefer the latest complete sentence so progress feels current.
    matches = list(re.finditer(r"(.{24,220}?[.!?])(?:\s|$)", safe))
    if not matches:
        tail = safe[-220:].strip()
        if len(tail) >= 24 and not _is_internal_reasoning_leak(tail):
            return tail
        return None

    for match in reversed(matches):
        snippet = match.group(1).strip()
        if len(snippet) < 24:
            continue
        if _is_internal_reasoning_leak(snippet):
            continue
        return snippet
    return None


def _append_reasoning_buffer(current: str, new_text: str, *, max_chars: int = 2000) -> str:
    """Append model reasoning text and retain a readable rolling window."""
    if not new_text:
        return current

    merged = f"{current} {new_text}".strip() if current else new_text.strip()
    if len(merged) <= max_chars:
        return merged

    window = merged[-max_chars:]
    first_space = window.find(" ")
    if 0 < first_space < 80:
        window = window[first_space + 1 :]
    return window.lstrip(" -•\t\r\n")


def _extract_reasoning_sidechannel(value: Any) -> str:
    """Extract reasoning text from provider side-channel fields."""

    def _coerce(candidate: Any) -> str:
        return _flatten_text_payload(candidate).strip()

    if value is None:
        return ""

    if isinstance(value, dict):
        for key in ("reasoning_content", "reasoning", "thinking", "thoughts", "analysis"):
            candidate = _coerce(value.get(key))
            if candidate:
                return candidate

        additional = value.get("additional_kwargs")
        if isinstance(additional, dict):
            for key in ("reasoning_content", "reasoning", "thinking", "thoughts", "analysis"):
                candidate = _coerce(additional.get(key))
                if candidate:
                    return candidate

        choices = value.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                delta = first.get("delta")
                if isinstance(delta, dict):
                    for key in (
                        "reasoning_content",
                        "reasoning",
                        "thinking",
                        "thoughts",
                        "analysis",
                    ):
                        candidate = _coerce(delta.get(key))
                        if candidate:
                            return candidate
        return ""

    for attr in ("reasoning_content", "reasoning", "thinking", "thoughts", "analysis"):
        if hasattr(value, attr):
            candidate = _coerce(getattr(value, attr))
            if candidate:
                return candidate

    if hasattr(value, "additional_kwargs"):
        extra = getattr(value, "additional_kwargs", None)
        if isinstance(extra, dict):
            for key in ("reasoning_content", "reasoning", "thinking", "thoughts", "analysis"):
                candidate = _coerce(extra.get(key))
                if candidate:
                    return candidate

    return ""


def _flatten_text_payload(value: Any) -> str:
    """Flatten loosely-typed provider payloads into plain text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_flatten_text_payload(item) for item in value]
        return " ".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in (
            "text",
            "content",
            "reasoning_content",
            "reasoning",
            "thinking",
            "thoughts",
            "analysis",
            "parts",
            "delta",
        ):
            if key in value:
                candidate = _flatten_text_payload(value.get(key))
                if candidate:
                    return candidate
        return ""
    return str(value)


def _extract_stream_payloads(chunk: Any) -> tuple[str, str]:
    """Extract `(token_text, reasoning_text)` from a model stream chunk."""
    token_text = ""
    reasoning_text = _extract_reasoning_sidechannel(chunk)

    raw_content = None
    if isinstance(chunk, dict):
        raw_content = chunk.get("content")
    elif hasattr(chunk, "content"):
        raw_content = getattr(chunk, "content")

    if isinstance(raw_content, list):
        token_parts: list[str] = []
        reasoning_parts: list[str] = []
        for block in raw_content:
            if isinstance(block, dict):
                block_type = str(block.get("type", "")).lower()
                text_value = ""
                preferred_text = block.get("text")
                if isinstance(preferred_text, str):
                    text_value = preferred_text
                elif preferred_text is not None:
                    text_value = _flatten_text_payload(preferred_text)

                if not text_value:
                    preferred_content = block.get("content")
                    if isinstance(preferred_content, str):
                        text_value = preferred_content
                    elif preferred_content is not None:
                        text_value = _flatten_text_payload(preferred_content)

                if not text_value:
                    text_value = _flatten_text_payload(block)
                if not text_value:
                    continue
                if any(
                    marker in block_type for marker in ("reason", "think", "analysis", "thought")
                ):
                    reasoning_parts.append(text_value)
                else:
                    token_parts.append(text_value)
            else:
                text_value = block if isinstance(block, str) else _flatten_text_payload(block)
                if text_value:
                    token_parts.append(text_value)
        token_text = "".join(token_parts)
        reasoning_text = " ".join(reasoning_parts).strip()
    elif isinstance(raw_content, dict):
        block_type = str(raw_content.get("type", "")).lower()
        text_value = ""
        preferred_text = raw_content.get("text")
        if isinstance(preferred_text, str):
            text_value = preferred_text
        elif preferred_text is not None:
            text_value = _flatten_text_payload(preferred_text)

        if not text_value:
            preferred_content = raw_content.get("content")
            if isinstance(preferred_content, str):
                text_value = preferred_content
            elif preferred_content is not None:
                text_value = _flatten_text_payload(preferred_content)

        if not text_value:
            text_value = _flatten_text_payload(raw_content)
        if any(marker in block_type for marker in ("reason", "think", "analysis", "thought")):
            reasoning_text = text_value.strip()
        else:
            token_text = text_value
    else:
        if isinstance(raw_content, str):
            token_text = raw_content
        else:
            token_text = _flatten_text_payload(raw_content)

    sidechannel_from_content = _extract_reasoning_sidechannel(raw_content)
    if sidechannel_from_content and sidechannel_from_content not in reasoning_text:
        reasoning_text = f"{reasoning_text} {sidechannel_from_content}".strip()

    additional_kwargs = None
    if isinstance(chunk, dict):
        maybe_kwargs = chunk.get("additional_kwargs")
        if isinstance(maybe_kwargs, dict):
            additional_kwargs = maybe_kwargs
    elif hasattr(chunk, "additional_kwargs"):
        maybe_kwargs = getattr(chunk, "additional_kwargs")
        if isinstance(maybe_kwargs, dict):
            additional_kwargs = maybe_kwargs

    if isinstance(additional_kwargs, dict):
        for key in ("reasoning_content", "reasoning", "thinking", "thoughts", "analysis"):
            candidate = _flatten_text_payload(additional_kwargs.get(key))
            if candidate:
                reasoning_text = f"{reasoning_text} {candidate}".strip()

        provider_fields = additional_kwargs.get("provider_specific_fields")
        if isinstance(provider_fields, dict):
            for key in ("reasoning_content", "reasoning", "thinking", "thoughts", "analysis"):
                candidate = _flatten_text_payload(provider_fields.get(key))
                if candidate:
                    reasoning_text = f"{reasoning_text} {candidate}".strip()

    for attr in ("reasoning_content", "reasoning", "thinking", "thoughts", "analysis"):
        if hasattr(chunk, attr):
            candidate = _flatten_text_payload(getattr(chunk, attr))
            if candidate:
                reasoning_text = f"{reasoning_text} {candidate}".strip()

    if hasattr(chunk, "response_metadata"):
        metadata = getattr(chunk, "response_metadata")
        if isinstance(metadata, dict):
            for key in ("reasoning_content", "reasoning", "thinking", "thoughts", "analysis"):
                candidate = _flatten_text_payload(metadata.get(key))
                if candidate:
                    reasoning_text = f"{reasoning_text} {candidate}".strip()

    if token_text:
        token_text = _sanitize_token_chunk(token_text)
    if reasoning_text:
        reasoning_text = re.sub(r"\s+", " ", _sanitize_assistant_text(reasoning_text)).strip()

    return token_text, reasoning_text


def _extract_user_message_from_supervisor_contract(content: str) -> str | None:
    """Extract raw user message from injected supervisor contract payload."""
    if "[SUPERVISOR CONTEXT CONTRACT" not in content:
        return None

    marker = "[USER MESSAGE]"
    idx = content.find(marker)
    if idx == -1:
        return None

    recovered = content[idx + len(marker) :].strip()
    return recovered or None


def _phase_to_context_step(phase: str | None) -> str:
    """Map internal phase labels to mission-control step labels."""
    normalized = (phase or "").lower().strip()
    if normalized in {"prepare", "transformation", "idle", ""}:
        return "discovery"
    if normalized in {
        "discovery",
        "requirements",
        "modeling",
        "generation",
        "validation",
        "publishing",
    }:
        return normalized
    if normalized == "explorer":
        return "publishing"
    return "discovery"


async def _get_document_context_contract(
    data_product_id: str,
    phase: str | None,
) -> dict[str, Any] | None:
    """Load active document evidence for the current step to guide supervisor routing."""
    step = _phase_to_context_step(phase)

    try:
        from services.postgres import get_pool as _gp, query as _q
        from config import get_effective_settings

        _settings = get_effective_settings()
        _pool = await _gp(_settings.database_url)

        version_rows = await _q(
            _pool,
            """SELECT version
               FROM context_versions
               WHERE data_product_id = $1::uuid
               ORDER BY version DESC
               LIMIT 1""",
            data_product_id,
        )
        context_version = int(version_rows[0]["version"]) if version_rows else None

        active_rows = await _q(
            _pool,
            """SELECT
                   ud.id AS document_id,
                   ud.filename,
                   ud.doc_kind,
                   ud.summary,
                   de.id AS evidence_id,
                   de.evidence_type,
                   de.payload
               FROM context_step_selections cs
               JOIN document_evidence de ON de.id = cs.evidence_id
               JOIN uploaded_documents ud ON ud.id = cs.document_id
               WHERE cs.data_product_id = $1::uuid
                 AND cs.step_name = $2
                 AND cs.state = 'active'
                 AND COALESCE(ud.is_deleted, false) = false
               ORDER BY cs.updated_at DESC
               LIMIT 8""",
            data_product_id,
            step,
        )

        candidate_rows = await _q(
            _pool,
            """SELECT COUNT(*) AS candidate_count
               FROM context_step_selections cs
               JOIN uploaded_documents ud ON ud.id = cs.document_id
               WHERE cs.data_product_id = $1::uuid
                 AND cs.step_name = $2
                 AND cs.state = 'candidate'
                 AND COALESCE(ud.is_deleted, false) = false""",
            data_product_id,
            step,
        )

        candidate_count = int(candidate_rows[0]["candidate_count"]) if candidate_rows else 0

        active_items: list[dict[str, Any]] = []
        for row in active_rows:
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            if not isinstance(payload, dict):
                payload = {}

            active_items.append(
                {
                    "document_id": row.get("document_id"),
                    "evidence_id": row.get("evidence_id"),
                    "filename": row.get("filename"),
                    "doc_kind": row.get("doc_kind"),
                    "summary": row.get("summary"),
                    "evidence_type": row.get("evidence_type"),
                    "payload": payload,
                }
            )

        return {
            "step": step,
            "context_version": context_version,
            "active_items": active_items,
            "candidate_count": candidate_count,
        }
    except Exception as e:
        # The context routing tables may not exist in older environments yet.
        logger.info("Document context pack unavailable for %s: %s", data_product_id, e)
        return None


async def _persist_artifact_context_snapshot(
    *,
    data_product_id: str,
    artifact_type: str,
    created_by: str,
    phase: str,
    document_context_contract: dict[str, Any] | None,
    artifact_id: str | None = None,
    artifact_version: int | None = None,
    snapshot_extra: dict[str, Any] | None = None,
) -> None:
    """Best-effort trace of context inputs used to produce an artifact."""
    if not document_context_contract:
        return

    context_version_raw = document_context_contract.get("context_version")
    if context_version_raw is None:
        return

    try:
        context_version = int(context_version_raw)
    except Exception:
        return

    step = str(document_context_contract.get("step") or _phase_to_context_step(phase))
    active_items = (
        document_context_contract.get("active_items")
        if isinstance(document_context_contract.get("active_items"), list)
        else []
    )
    active_document_ids = [
        str(item.get("document_id"))
        for item in active_items
        if isinstance(item, dict) and item.get("document_id")
    ]
    active_evidence_ids = [
        str(item.get("evidence_id"))
        for item in active_items
        if isinstance(item, dict) and item.get("evidence_id")
    ]

    try:
        from config import get_effective_settings
        from services.postgres import execute as _execute
        from services.postgres import get_pool as _gp
        from services.postgres import query as _query

        _settings = get_effective_settings()
        _pool = await _gp(_settings.database_url)

        version_rows = await _query(
            _pool,
            """SELECT id
               FROM context_versions
               WHERE data_product_id = $1::uuid
                 AND version = $2
               LIMIT 1""",
            data_product_id,
            context_version,
        )
        context_version_id = (
            str(version_rows[0]["id"])
            if version_rows and isinstance(version_rows[0].get("id"), str)
            else None
        )
        if not context_version_id:
            return

        resolved_version = artifact_version
        if resolved_version is None:
            if artifact_type == "brd":
                brd_rows = await _query(
                    _pool,
                    """SELECT MAX(version) AS version
                       FROM business_requirements
                       WHERE data_product_id = $1::uuid""",
                    data_product_id,
                )
                resolved_version = (
                    int(brd_rows[0]["version"])
                    if brd_rows and brd_rows[0].get("version") is not None
                    else None
                )
            elif artifact_type in {"yaml", "semantic_view"}:
                sv_rows = await _query(
                    _pool,
                    """SELECT MAX(version) AS version
                       FROM semantic_views
                       WHERE data_product_id = $1::uuid""",
                    data_product_id,
                )
                resolved_version = (
                    int(sv_rows[0]["version"])
                    if sv_rows and sv_rows[0].get("version") is not None
                    else None
                )

        snapshot_payload: dict[str, Any] = {
            "phase": phase,
            "step": step,
            "context_version": context_version,
            "active_document_ids": active_document_ids[:24],
            "active_evidence_ids": active_evidence_ids[:48],
        }
        if snapshot_extra and isinstance(snapshot_extra, dict):
            snapshot_payload.update(snapshot_extra)

        await _execute(
            _pool,
            """INSERT INTO artifact_context_snapshots
                 (id, data_product_id, artifact_id, artifact_type, artifact_version,
                  context_version_id, snapshot, created_by, created_at)
               VALUES
                 ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::uuid, $7::jsonb, $8, NOW())""",
            str(uuid4()),
            data_product_id,
            artifact_id,
            artifact_type,
            resolved_version,
            context_version_id,
            json.dumps(snapshot_payload),
            created_by,
        )
    except Exception as e:
        code = getattr(e, "code", None)
        if code in {"42P01", "42703", "23503"}:
            logger.info(
                "artifact_context_snapshots unavailable for %s (%s): %s",
                data_product_id,
                artifact_type,
                e,
            )
            return
        logger.warning(
            "Failed to persist artifact context snapshot for %s (%s): %s",
            data_product_id,
            artifact_type,
            e,
        )


async def _get_workflow_snapshot(data_product_id: str) -> dict[str, Any]:
    """Load workflow state used by supervisor guards and context contract."""
    snapshot: dict[str, Any] = {
        "current_phase": "discovery",
        "data_tier": None,
        "product_type": "structured",
        "has_documents": False,
        "transformation_done": False,
        "quality_report_exists": False,
        "data_description_exists": False,
        "brd_exists": False,
        "semantic_view_exists": False,
        "validation_status": None,
    }
    try:
        from services.postgres import get_pool as _gp, query as _q
        from config import get_effective_settings

        _settings = get_effective_settings()
        _pool = await _gp(_settings.database_url)

        dp_rows = await _q(
            _pool,
            """SELECT
                   name,
                   state->>'current_phase' AS current_phase,
                   state->>'data_tier' AS data_tier,
                   state->'working_layer' AS working_layer,
                   product_type,
                   published_agent_fqn
               FROM data_products
               WHERE id = $1::uuid""",
            data_product_id,
        )
        if dp_rows:
            row = dp_rows[0]
            snapshot["current_phase"] = row.get("current_phase") or "discovery"
            snapshot["data_tier"] = row.get("data_tier")
            snapshot["product_type"] = row.get("product_type") or "structured"
            dp_name = row.get("name") or ""
            snapshot["data_product_name"] = dp_name
            if dp_name:
                from tools.naming import sanitize_dp_name
                sanitized = sanitize_dp_name(dp_name)
                snapshot["target_schema_marts"] = f"EKAIX.{sanitized}_MARTS"
                snapshot["target_schema_docs"] = f"EKAIX.{sanitized}_DOCS"
            working_layer = row.get("working_layer")
            if isinstance(working_layer, str):
                try:
                    working_layer = json.loads(working_layer)
                except Exception:
                    working_layer = None
            snapshot["transformation_done"] = (
                isinstance(working_layer, dict) and len(working_layer) > 0
            )
            agent_fqn = row.get("published_agent_fqn")
            if agent_fqn and isinstance(agent_fqn, str):
                snapshot["published_agent_fqn"] = agent_fqn

        dd_rows = await _q(
            _pool,
            """SELECT description_json, version
               FROM data_descriptions
               WHERE data_product_id = $1::uuid
               ORDER BY version DESC LIMIT 1""",
            data_product_id,
        )
        snapshot["data_description_exists"] = bool(dd_rows)
        if dd_rows:
            snapshot["data_description_content"] = dd_rows[0].get("description_json")
            snapshot["data_description_version"] = dd_rows[0].get("version")

        quality_rows = await _q(
            _pool,
            "SELECT 1 FROM data_quality_checks WHERE data_product_id = $1::uuid LIMIT 1",
            data_product_id,
        )
        snapshot["quality_report_exists"] = bool(quality_rows)

        brd_rows = await _q(
            _pool,
            """SELECT brd_json, version
               FROM business_requirements
               WHERE data_product_id = $1::uuid
               ORDER BY version DESC LIMIT 1""",
            data_product_id,
        )
        snapshot["brd_exists"] = bool(brd_rows)
        if brd_rows:
            snapshot["brd_content"] = brd_rows[0].get("brd_json")
            snapshot["brd_version"] = brd_rows[0].get("version")

        doc_rows = await _q(
            _pool,
            "SELECT 1 FROM uploaded_documents WHERE data_product_id = $1::uuid LIMIT 1",
            data_product_id,
        )
        snapshot["has_documents"] = bool(doc_rows)

        if snapshot["has_documents"]:
            count_rows = await _q(
                _pool,
                """SELECT
                     (SELECT count(*) FROM doc_chunks WHERE data_product_id = $1::uuid) AS chunk_count,
                     (SELECT count(*) FROM doc_facts  WHERE data_product_id = $1::uuid) AS fact_count
                """,
                data_product_id,
            )
            if count_rows:
                snapshot["doc_chunks_count"] = count_rows[0].get("chunk_count", 0)
                snapshot["doc_facts_count"] = count_rows[0].get("fact_count", 0)

        sv_rows = await _q(
            _pool,
            """SELECT yaml_content, validation_status, version
               FROM semantic_views
               WHERE data_product_id = $1::uuid
               ORDER BY version DESC
               LIMIT 1""",
            data_product_id,
        )
        snapshot["semantic_view_exists"] = bool(sv_rows)
        if sv_rows:
            snapshot["validation_status"] = sv_rows[0].get("validation_status")
            snapshot["semantic_view_content"] = sv_rows[0].get("yaml_content")
            snapshot["semantic_view_version"] = sv_rows[0].get("version")
    except Exception as e:
        logger.warning("Failed to load workflow snapshot for %s: %s", data_product_id, e)

    return snapshot


async def _get_publish_gate_context(data_product_id: str) -> tuple[str | None, bool]:
    """Return (current_phase, already_published) for publish gating."""
    try:
        from services.postgres import get_pool as _gp, query as _q
        from config import get_effective_settings

        _settings = get_effective_settings()
        _pool = await _gp(_settings.database_url)
        rows = await _q(
            _pool,
            """SELECT
                   status,
                   state->>'current_phase' AS current_phase,
                   (LOWER(COALESCE(state->>'published', 'false')) IN ('true', 't', '1', 'yes')) AS state_published,
                   published_at
               FROM data_products
               WHERE id = $1::uuid""",
            data_product_id,
        )
        if not rows:
            return None, False

        row = rows[0]
        phase = row.get("current_phase")
        # Use explicit publish markers only. `published_at` is historical and may
        # remain set after a re-run starts a new lifecycle.
        already_published = bool(row.get("state_published")) or row.get("status") == "published"
        return phase if isinstance(phase, str) else None, already_published
    except Exception as e:
        logger.warning("Publish gate context lookup failed for %s: %s", data_product_id, e)
        return None, False


@router.post("/message")
async def send_message(request: InvokeRequest) -> InvokeResponse:
    """Accept a user message and begin asynchronous agent processing.

    The message is dispatched to the Deep Agents orchestrator in a background
    task. The caller should subscribe to the SSE stream at
    /agent/stream/{session_id} to receive real-time responses.
    """
    message_id = uuid4()
    session_id = request.session_id
    logger.info(
        "Received message for session %s (message_id=%s): %s",
        session_id,
        message_id,
        request.message[:100],
    )

    # Create a queue for this session's SSE events
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    _active_streams[session_id] = queue

    # Launch the agent invocation in the background
    asyncio.create_task(
        _run_agent(
            session_id,
            request.message,
            str(request.data_product_id),
            queue,
            file_contents=request.file_contents,
        )
    )

    return InvokeResponse(
        session_id=session_id,
        message_id=message_id,
        status="processing",
    )


async def _get_data_product_info(data_product_id: str) -> dict | None:
    """Fetch data product details from PostgreSQL.

    Returns a dict with name, description, database, schemas, tables,
    or None if not found.
    """
    from services.postgres import get_pool, query
    from config import get_settings

    settings = get_settings()
    pool = await get_pool(settings.database_url)
    rows = await query(
        pool,
        """SELECT name, description, product_type, database_reference, schemas, tables
           FROM data_products WHERE id = $1""",
        data_product_id,
    )
    if not rows:
        return None

    r = rows[0]
    return {
        "name": r["name"],
        "description": r["description"] or "No description provided",
        "product_type": r.get("product_type") or "structured",
        "database": r["database_reference"],
        "schemas": r["schemas"] or [],
        "tables": r["tables"] or [],
    }


def _simplify_type(data_type: str) -> str:
    """Simplify Snowflake data type to business-friendly category."""
    dt = data_type.upper().strip()
    if dt in (
        "NUMBER",
        "FLOAT",
        "DECIMAL",
        "INTEGER",
        "INT",
        "BIGINT",
        "SMALLINT",
        "TINYINT",
        "DOUBLE",
        "REAL",
        "NUMERIC",
    ):
        return "numeric"
    if dt in ("VARCHAR", "TEXT", "STRING", "CHAR", "NCHAR", "NVARCHAR", "CLOB", "NCLOB"):
        return "text"
    if dt in (
        "TIMESTAMP_NTZ",
        "TIMESTAMP_LTZ",
        "TIMESTAMP_TZ",
        "TIMESTAMP",
        "DATE",
        "DATETIME",
        "TIME",
    ):
        return "date/time"
    if dt == "BOOLEAN":
        return "boolean"
    if dt in ("VARIANT", "OBJECT", "ARRAY"):
        return "structured"
    return "text"


def _suggest_field_role(
    col_name: str,
    simplified_type: str,
    is_pk: bool,
    distinct_count: int | None,
    null_pct: float | None,
) -> str:
    """Suggest the analytical role of a field based on name and type."""
    name_lower = col_name.lower()

    # ID fields
    if is_pk or name_lower.endswith("_id") or name_lower.endswith("_key") or name_lower == "id":
        return "identifier"

    # Date/time → time dimension
    if simplified_type == "date/time":
        return "potential time dimension"

    # Numeric fields (not IDs) → potential measure
    if simplified_type == "numeric":
        # Skip fields that look like codes or counts of categories
        if any(kw in name_lower for kw in ("code", "zip", "postal", "phone")):
            return "potential dimension"
        return "potential measure"

    # Text fields with low cardinality → potential dimension
    if simplified_type == "text" and distinct_count is not None:
        if distinct_count <= 100:
            return "potential dimension"
        if distinct_count <= 500:
            return "potential dimension (many values)"

    # Boolean → potential filter/dimension
    if simplified_type == "boolean":
        return "potential dimension"

    # Descriptive text fields
    if any(
        kw in name_lower
        for kw in ("description", "comment", "note", "text", "body", "message", "remark")
    ):
        return "descriptive"

    return ""


def _build_maturity_section(
    maturity: dict[str, dict],
    metadata: list[dict],
) -> str:
    """Build a human-readable maturity classification section for the LLM context."""
    if not maturity:
        return (
            "Not available (pipeline may be cached from before maturity classification was added)."
        )

    lines: list[str] = []
    # Map FQN to short name
    name_map = {t["fqn"]: t["name"] for t in metadata}

    for fqn, info in maturity.items():
        name = name_map.get(fqn, fqn.split(".")[-1])
        level = info.get("maturity", "unknown")
        score = info.get("score", 0)
        signals = info.get("signals", {})

        # Build issue summary for non-gold tables
        issue_hints: list[str] = []
        if signals.get("varchar_ratio", 0) > 0.6:
            issue_hints.append(f"{int(signals['varchar_ratio'] * 100)}% text-typed columns")
        if signals.get("avg_null_pct", 0) > 20:
            issue_hints.append(f"{signals['avg_null_pct']:.0f}% average null rate")
        if signals.get("duplicate_rate", 0) > 0.05:
            issue_hints.append(f"{signals['duplicate_rate'] * 100:.1f}% duplicate rows")
        if signals.get("pk_confidence", 1) == 0:
            issue_hints.append("no clear unique identifier")
        if signals.get("nested_col_count", 0) > 0:
            issue_hints.append(f"{signals['nested_col_count']} nested/semi-structured columns")

        hint_str = f" — issues: {', '.join(issue_hints)}" if issue_hints else ""
        lines.append(f"  {name}: {level.upper()} (score {score}){hint_str}")

    return "\n".join(lines) if lines else "No tables classified."


async def _persist_phase(data_product_id: str, phase: str) -> None:
    """Persist current_phase to state JSONB and keep top-level status in sync."""
    try:
        from services.postgres import get_pool as _gp, execute as _ex
        from config import get_effective_settings

        _settings = get_effective_settings()
        _pool = await _gp(_settings.database_url)
        _status = _map_phase_to_status(phase)
        await _ex(
            _pool,
            """UPDATE data_products
               SET state = jsonb_set(COALESCE(state, '{}'::jsonb), '{current_phase}', $1::jsonb),
                   status = CASE
                     WHEN status = 'published'::data_product_status
                       OR published_at IS NOT NULL
                       OR (LOWER(COALESCE(state->>'published', 'false')) IN ('true', 't', '1', 'yes'))
                     THEN 'published'::data_product_status
                     ELSE COALESCE($2::data_product_status, status)
                   END
               WHERE id = $3::uuid""",
            f'"{phase}"',
            _status,
            data_product_id,
        )
    except Exception as e:
        logger.warning("Failed to persist current_phase=%s/status: %s", phase, e)


async def _post_publish_auto_repair(
    *,
    data_product_id: str,
    published_agent_fqn: str | None,
) -> None:
    """Deterministic post-publish safety net.

    After the LLM publish stream completes, verify that the Cortex Search
    Service and Cortex Agent actually exist.  If missing (the LLM skipped a
    step), create them deterministically.  Persist ``published_at`` and
    ``published_agent_fqn`` to PostgreSQL regardless.
    """
    from config import get_effective_settings
    from services.postgres import execute as pg_execute
    from services.postgres import get_pool as pg_get_pool
    from services.postgres import query as pg_query
    from services.snowflake import execute_query as sf_query
    from tools.naming import sanitize_dp_name

    settings = get_effective_settings()
    pool = await pg_get_pool(settings.database_url)

    # Fetch data product name to derive schema names
    dp_rows = await pg_query(
        pool,
        "SELECT name FROM data_products WHERE id = $1::uuid",
        data_product_id,
    )
    if not dp_rows:
        logger.warning("Post-publish auto-repair: data product %s not found", data_product_id)
        return

    dp_name = dp_rows[0].get("name") or ""
    if not dp_name:
        logger.warning("Post-publish auto-repair: data product %s has no name", data_product_id)
        return

    sanitized = sanitize_dp_name(dp_name)
    docs_schema = f"{sanitized}_DOCS"
    marts_schema = f"{sanitized}_MARTS"
    quoted_docs = f'"EKAIX"."{docs_schema}"'
    quoted_marts = f'"EKAIX"."{marts_schema}"'
    warehouse = settings.snowflake_warehouse

    # ── Step 1: Ensure Cortex Search Service exists ──────────────────
    has_search_service = False
    has_doc_chunks = False
    try:
        svc_rows = await sf_query(f"SHOW CORTEX SEARCH SERVICES IN SCHEMA {quoted_docs}")
        has_search_service = bool(svc_rows)
    except Exception:
        pass  # Schema or service doesn't exist

    if not has_search_service:
        # Check if DOC_CHUNKS has rows
        try:
            chunk_rows = await sf_query(
                f"SELECT COUNT(*) AS cnt FROM {quoted_docs}.DOC_CHUNKS"
            )
            has_doc_chunks = (
                bool(chunk_rows)
                and int(chunk_rows[0].get("CNT") or chunk_rows[0].get("cnt") or 0) > 0
            )
        except Exception:
            pass

        if has_doc_chunks:
            logger.info(
                "Post-publish auto-repair: creating Cortex Search Service in %s",
                docs_schema,
            )
            try:
                await sf_query("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 300")
            except Exception:
                pass
            service_fqn = f'{quoted_docs}."EKAIX_DOCUMENT_SEARCH"'
            create_css_sql = f"""CREATE OR REPLACE CORTEX SEARCH SERVICE {service_fqn}
  ON chunk_text
  ATTRIBUTES document_id, filename, doc_kind, page_no, section_path
  WAREHOUSE = {warehouse}
  TARGET_LAG = '1 hour'
AS
  SELECT chunk_id, document_id, filename, doc_kind, page_no,
         section_path, chunk_seq, chunk_text
  FROM {quoted_docs}.DOC_CHUNKS"""
            try:
                await sf_query(create_css_sql)
                has_search_service = True
                logger.info("Post-publish auto-repair: Cortex Search Service created")
            except Exception as e:
                logger.warning("Post-publish auto-repair: failed to create search service: %s", e)
            try:
                await sf_query("ALTER SESSION UNSET STATEMENT_TIMEOUT_IN_SECONDS")
            except Exception:
                pass

    # ── Step 2: Ensure Cortex Agent exists ───────────────────────────
    has_agent = False
    agent_fqn_resolved = published_agent_fqn
    try:
        agent_rows = await sf_query(f"SHOW AGENTS IN SCHEMA {quoted_marts}")
        has_agent = bool(agent_rows)
        if has_agent and not agent_fqn_resolved and agent_rows:
            # Capture FQN from existing agent
            first_name = agent_rows[0].get("name") or agent_rows[0].get("NAME")
            if first_name:
                agent_fqn_resolved = f"EKAIX.{marts_schema}.{first_name}"
    except Exception:
        pass

    if not has_agent:
        logger.info(
            "Post-publish auto-repair: creating Cortex Agent in %s",
            marts_schema,
        )
        # Check for semantic view
        sv_fqn = ""
        try:
            sv_rows = await sf_query(
                f"SHOW VIEWS IN SCHEMA {quoted_marts}"
            )
            for sv_row in sv_rows or []:
                vname = sv_row.get("name") or sv_row.get("NAME") or ""
                if "SEMANTIC" in vname.upper() or vname.upper().startswith("SV_"):
                    sv_fqn = f"EKAIX.{marts_schema}.{vname}"
                    break
        except Exception:
            pass

        # Build agent tools based on what's available
        tools_yaml_parts: list[str] = []
        resources_yaml_parts: list[str] = []

        if sv_fqn:
            tools_yaml_parts.append("""  - tool_spec:
      type: cortex_analyst_text_to_sql
      name: Analyst
      description: 'Answers questions about the data using the semantic model'""")
            resources_yaml_parts.append(f"""  Analyst:
    semantic_view: '{sv_fqn}'
    execution_environment:
      type: warehouse
      warehouse: '{warehouse}'""")

        if has_search_service:
            tools_yaml_parts.append("""  - tool_spec:
      type: cortex_search
      name: DocumentSearch
      description: 'Searches uploaded documents for relevant context and evidence'""")
            resources_yaml_parts.append(f"""  DocumentSearch:
    search_service: 'EKAIX.{docs_schema}.EKAIX_DOCUMENT_SEARCH'
    max_results: 5""")

        if tools_yaml_parts:
            agent_name = f"{sanitized}_AGENT"
            agent_fqn_full = f'{quoted_marts}."{agent_name}"'
            tools_yaml = "\n".join(tools_yaml_parts)
            resources_yaml = "\n".join(resources_yaml_parts)

            spec_yaml = f"""models:
  orchestration: claude-3-5-sonnet
orchestration:
  budget:
    seconds: 120
    tokens: 10000
instructions:
  response: 'Answer the user''s question using the available tools. Provide evidence-backed answers with citations.'
  system: 'AI agent for {dp_name} data product'
tools:
{tools_yaml}
tool_resources:
{resources_yaml}"""

            create_agent_sql = (
                f'CREATE OR REPLACE AGENT {agent_fqn_full}\n'
                f"  COMMENT = 'AI agent for {dp_name.replace(chr(39), chr(39)+chr(39))} data product'\n"
                f'  FROM SPECIFICATION\n$${spec_yaml}$$'
            )
            try:
                await sf_query(create_agent_sql)
                agent_fqn_resolved = f"EKAIX.{marts_schema}.{agent_name}"
                logger.info(
                    "Post-publish auto-repair: Cortex Agent created at %s",
                    agent_fqn_resolved,
                )
            except Exception as e:
                logger.warning("Post-publish auto-repair: failed to create agent: %s", e)
        else:
            logger.warning(
                "Post-publish auto-repair: no tools available to create agent (no semantic view, no search service)"
            )

    # ── Step 3: Persist published_at + published_agent_fqn to PG ─────
    try:
        await pg_execute(
            pool,
            """UPDATE data_products
               SET published_at = COALESCE(published_at, NOW()),
                   published_agent_fqn = COALESCE($1, published_agent_fqn),
                   state = jsonb_set(
                       COALESCE(state, '{}'::jsonb),
                       '{published}',
                       'true'::jsonb
                   )
               WHERE id = $2::uuid""",
            agent_fqn_resolved,
            data_product_id,
        )
        logger.info(
            "Post-publish auto-repair: persisted published_at and agent_fqn=%s for %s",
            agent_fqn_resolved,
            data_product_id,
        )
    except Exception as e:
        logger.warning("Post-publish auto-repair: failed to persist publish state: %s", e)

    # ── Step 4: Clear transient PostgreSQL doc_chunks ────────────────
    # Only safe to delete PG chunks when the Cortex Search Service is
    # confirmed healthy. If it failed to create, PG chunks are the ONLY
    # document store the explorer can search.
    if has_search_service:
        try:
            await pg_execute(
                pool,
                "DELETE FROM doc_chunks WHERE data_product_id = $1::uuid",
                data_product_id,
            )
            logger.info(
                "Post-publish auto-repair: cleared PG doc_chunks for %s (Cortex Search healthy)",
                data_product_id,
            )
        except Exception as e:
            logger.warning("Post-publish auto-repair: failed to clear doc_chunks: %s", e)
    else:
        logger.info(
            "Post-publish auto-repair: keeping PG doc_chunks for %s (Cortex Search not available)",
            data_product_id,
        )


def _build_discovery_summary(
    pipeline_results: dict,
    dp_name: str,
    data_product_id: str,
    dp_description: str = "",
) -> str:
    """Convert pipeline results into a structured summary for the LLM.

    Includes per-table field analysis with suggested roles (potential measure,
    dimension, time dimension) so the LLM can propose specific analytics.
    """
    metadata = pipeline_results.get("metadata", [])
    profiles = pipeline_results.get("profiles", [])
    classifications = pipeline_results.get("classifications", {})
    relationships = pipeline_results.get("relationships", [])
    quality = pipeline_results.get("quality", {})
    maturity = pipeline_results.get("maturity_classifications", {})

    # Build profile lookup: fqn -> {column -> profile_data}
    profile_lookup: dict[str, dict[str, dict]] = {}
    for p in profiles:
        table_fqn = p.get("table", "")
        col_map: dict[str, dict] = {}
        for col in p.get("columns", []):
            col_map[col.get("column", "")] = col
        profile_lookup[table_fqn] = col_map

    # For large datasets (>15 tables), only include the most interesting columns
    # per table to keep the summary under ~15K chars. PKs, FKs, and role-tagged
    # columns are always included; plain descriptive columns are trimmed.
    # Scale inversely: more tables → fewer columns per table.
    _LARGE_DATASET_THRESHOLD = 15
    num_tables = len(metadata)
    if num_tables > 30:
        _MAX_COLS_PER_TABLE_LARGE = 6
    elif num_tables > 20:
        _MAX_COLS_PER_TABLE_LARGE = 8
    else:
        _MAX_COLS_PER_TABLE_LARGE = 12

    # Build table detail sections (tables + field analysis)
    table_sections = []
    for table in metadata:
        fqn = table["fqn"]
        name = table["name"]
        classification = classifications.get(fqn, "UNKNOWN")
        biz_type = "transaction data" if classification == "FACT" else "reference data"
        row_count = table.get("row_count")
        row_str = f", ~{row_count:,} records" if row_count else ""

        # Get profile data for this table
        col_profiles = profile_lookup.get(fqn, {})

        # Build field analysis lines with priority scoring for large datasets
        all_field_entries: list[tuple[int, str]] = []  # (priority, line)
        for col in table.get("columns", []):
            col_name = col["name"]
            raw_type = col.get("data_type", "")
            simple_type = _simplify_type(raw_type)

            # Get profiling info
            prof = col_profiles.get(col_name, {})
            is_pk = prof.get("is_likely_pk", False)
            distinct = prof.get("distinct_count")
            null_pct = prof.get("null_pct")

            role = _suggest_field_role(col_name, simple_type, is_pk, distinct, null_pct)

            # Priority: PKs=0, FKs=1, measures=2, time=3, dimensions=4, descriptive=5
            priority = 5
            if is_pk:
                priority = 0
            elif col_name.lower().endswith("_id") or col_name.lower() == "id":
                priority = 1
            elif role in ("potential measure", "potential time dimension"):
                priority = 2 if "measure" in role else 3
            elif role == "potential dimension":
                priority = 4

            # Build description parts
            parts = [simple_type]
            if is_pk:
                parts.append("unique identifier")
            if distinct is not None and simple_type == "text" and distinct <= 100:
                parts.append(f"{distinct} values")
            # Only show completeness for identifier columns — non-ID sparseness
            # is structurally expected (e.g., coal fields on solar plants)
            col_lower = col_name.lower()
            is_id_col = (
                is_pk
                or col_lower.endswith("_id")
                or col_lower == "id"
                or col_lower.endswith("_code")
                or col_lower.endswith("_key")
            )
            if is_id_col and null_pct is not None and null_pct > 5:
                parts.append(f"{100 - null_pct:.0f}% complete")
            if role and role not in ("identifier", "descriptive", ""):
                parts.append(role)

            all_field_entries.append((priority, f"    - {col_name} ({', '.join(parts)})"))

        # For large datasets, keep only the most important columns per table
        if len(metadata) > _LARGE_DATASET_THRESHOLD:
            all_field_entries.sort(key=lambda x: x[0])
            kept = all_field_entries[:_MAX_COLS_PER_TABLE_LARGE]
            trimmed = len(all_field_entries) - len(kept)
            field_lines = [line for _, line in kept]
            if trimmed > 0:
                field_lines.append(f"    ... and {trimmed} more columns")
        else:
            field_lines = [line for _, line in all_field_entries]

        section = f"  {name} ({biz_type}{row_str})\n" + "\n".join(field_lines)
        table_sections.append(section)

    # Quality summary
    score = quality.get("overall_score", 0)
    completeness = quality.get("avg_completeness_pct", 0)
    issues = quality.get("issues", [])
    issue_summary = ""
    if issues:
        top_issues = issues[:3]
        issue_lines = [f"  - {i['message']}" for i in top_issues]
        issue_summary = "\nNotable issues:\n" + "\n".join(issue_lines)

    # Counts
    fact_count = sum(1 for v in classifications.values() if v == "FACT")
    dim_count = sum(1 for v in classifications.values() if v == "DIMENSION")

    # Description line
    desc_line = f"\nUser's description: {dp_description}" if dp_description else ""

    summary = f"""[INTERNAL CONTEXT — NOT FOR USER DISPLAY]

═══════════════════════════════════════════════════════
PRE-COMPUTED DISCOVERY RESULTS
═══════════════════════════════════════════════════════
Data Product: {dp_name}{desc_line}
Data Product ID (for tool calls only): {data_product_id}
Tables analyzed: {len(metadata)} ({fact_count} transaction, {dim_count} reference)

═══════════════════════════════════════════════════════
TABLE DETAILS & FIELD ANALYSIS
═══════════════════════════════════════════════════════
{chr(10).join(table_sections)}

═══════════════════════════════════════════════════════
DATA QUALITY
═══════════════════════════════════════════════════════
Score: {score}/100 (average completeness: {completeness:.0f}%)
{issue_summary}

═══════════════════════════════════════════════════════
DATA READINESS (maturity_classifications)
═══════════════════════════════════════════════════════
{_build_maturity_section(maturity, metadata)}

═══════════════════════════════════════════════════════
YOUR TASK
═══════════════════════════════════════════════════════
All profiling, classification, and quality checks are ALREADY DONE.
Quality report artifact is ALREADY saved.

The data map (ERD) and connections have NOT been built yet. You will build
them AFTER your conversation with the user.

Your job is to:
1. Identify the business domain from table/field naming patterns
2. Recognize each table's likely business role (transaction vs reference)
3. Weave the quality score in naturally
4. Form relationship HYPOTHESES from _id columns and naming patterns
5. Using the FIELD ANALYSIS above, propose a focused set of specific metrics this
   data could support. Use fields tagged "potential measure" for metrics and
   fields tagged "potential dimension" for grouping options.
6. Ask focused validation questions that cover unknown or ambiguous areas only.
   For each: state your inference, then ask the user to confirm. Example:
   "I suspect X connects to Y through Z — does that match your understanding?
   If you're not sure, I'll proceed with my analysis."

RULES:
- Do NOT call tools on this first message — document intelligence is ALREADY pre-computed in the DOCUMENT INTELLIGENCE section below (if documents exist). Use that context directly to form enriched questions. You may call search_document_chunks later for deeper exploration.
- Do NOT repeat the data above verbatim — interpret it in business language
- Refer to the data product as "{dp_name}"
- Use table short names (e.g. "your Customers table") not FQNs
- Your suggested metrics MUST reference actual field names from the analysis above
  Use format: business name (FIELD_NAME) — e.g. "average reading value (VALUE)"
- If the user's description states their goal, tailor your suggestions to it.
  Do NOT re-ask what they want to do — they already told you. Confirm understanding.
- DATA ISOLATION: ONLY discuss the tables listed above. You know NOTHING about
  any other databases, schemas, or tables in this Snowflake account. They do not
  exist to you. NEVER mention or speculate about any other datasets.
══════════════════════════════════════════════════════════════════"""

    # Hard cap: if summary exceeds 15K chars, the LLM may hang or produce
    # empty output. Truncate table sections to fit.
    _MAX_SUMMARY_CHARS = 15000
    if len(summary) > _MAX_SUMMARY_CHARS:
        logger.warning(
            "Discovery summary too large (%d chars, %d tables). Truncating to %d chars.",
            len(summary),
            len(metadata),
            _MAX_SUMMARY_CHARS,
        )
        # Find where table sections end and truncate
        marker = "═══════════════════════════════════════════════════════\nDATA QUALITY"
        marker_pos = summary.find(marker)
        if marker_pos > 0:
            # Get prefix (before tables) and suffix (quality + task sections)
            prefix_end = summary.find(
                "═══════════════════════════════════════════════════════\nTABLE DETAILS"
            )
            suffix = summary[marker_pos:]
            prefix = summary[:prefix_end] if prefix_end > 0 else ""
            # Available space for table sections
            available = _MAX_SUMMARY_CHARS - len(prefix) - len(suffix) - 200
            table_text = chr(10).join(table_sections)
            if len(table_text) > available:
                # Truncate table text and add note
                table_text = (
                    table_text[:available]
                    + f"\n\n  ... ({len(metadata)} tables total — showing key columns only)"
                )
            summary = f"""{prefix}═══════════════════════════════════════════════════════
TABLE DETAILS & FIELD ANALYSIS
═══════════════════════════════════════════════════════
{table_text}

{suffix}"""
        else:
            # Fallback: hard truncate
            summary = summary[:_MAX_SUMMARY_CHARS] + "\n... (truncated)"
        logger.info("Discovery summary truncated to %d chars", len(summary))

    return summary


async def _build_document_intelligence(
    data_product_id: str,
    pipeline_results: dict,
    doc_chunks_count: int,
) -> str:
    """Pre-compute document intelligence by cross-referencing doc_chunks with table metadata.

    Instead of relying on the LLM to call tools and parse JSON results,
    this function queries doc_chunks directly and returns plain-text excerpts
    with cross-references to structured tables/columns.

    Returns a formatted section to inject into the discovery context.
    """
    from services.postgres import get_pool as _gp, query as _q
    from config import get_effective_settings

    settings = get_effective_settings()
    pool = await _gp(settings.database_url)

    # 1. Extract domain terms from table/column metadata
    metadata = pipeline_results.get("metadata", [])
    table_names = [t.get("name", "") for t in metadata]
    # Collect important column names (dimensions, measures, IDs with business meaning)
    domain_terms: list[str] = []
    column_to_table: dict[str, str] = {}  # column_name -> table_name for cross-refs
    for table in metadata:
        tname = table.get("name", "")
        for col in table.get("columns", []):
            cname = col.get("name", "")
            cname_lower = cname.lower()
            # Skip generic columns (IDs, timestamps, metadata)
            if cname_lower in ("id", "created_at", "updated_at", "row_id"):
                continue
            if cname_lower.endswith("_id") or cname_lower.endswith("_key"):
                continue
            # Business-relevant columns become search terms
            # Convert SNAKE_CASE to space-separated words
            readable = cname.replace("_", " ").strip()
            if len(readable) > 3:
                domain_terms.append(readable)
                column_to_table[cname] = tname

    # 2. Fetch top document chunks by extraction confidence (broad coverage)
    top_chunks: list[dict] = []
    try:
        top_chunks = await _q(
            pool,
            """SELECT c.chunk_text, ud.filename, c.page_no, c.chunk_seq,
                      c.extraction_confidence
               FROM doc_chunks c
               JOIN uploaded_documents ud ON ud.id = c.document_id
               WHERE c.data_product_id = $1::uuid
                 AND COALESCE(ud.is_deleted, false) = false
                 AND c.chunk_text IS NOT NULL
                 AND length(c.chunk_text) > 50
               ORDER BY COALESCE(c.extraction_confidence, 0) DESC, c.chunk_seq ASC
               LIMIT 12""",
            data_product_id,
        )
    except Exception as e:
        logger.warning("Failed to fetch top doc chunks: %s", e)

    if not top_chunks:
        return ""

    # 3. Build a domain-keyword search to find chunks that reference structured concepts
    # Use ILIKE for reliability — search for table names and key column terms in chunks
    cross_refs: list[dict] = []
    search_terms = list(set(
        [t.replace("_", " ") for t in table_names if len(t) > 3]
        + domain_terms[:20]  # Cap to avoid too many
    ))

    for term in search_terms[:15]:  # Limit queries
        try:
            matches = await _q(
                pool,
                """SELECT c.chunk_text, ud.filename, c.page_no
                   FROM doc_chunks c
                   JOIN uploaded_documents ud ON ud.id = c.document_id
                   WHERE c.data_product_id = $1::uuid
                     AND COALESCE(ud.is_deleted, false) = false
                     AND LOWER(c.chunk_text) LIKE $2
                   LIMIT 2""",
                data_product_id,
                f"%{term.lower()}%",
            )
            for m in matches:
                cross_refs.append({
                    "term": term,
                    "filename": m.get("filename", ""),
                    "page_no": m.get("page_no"),
                    "excerpt": (m.get("chunk_text") or "")[:300].strip(),
                })
        except Exception:
            continue

    # 4. Build plain-text output
    lines: list[str] = []
    lines.append("═══════════════════════════════════════════════════════")
    lines.append("DOCUMENT INTELLIGENCE (pre-computed cross-references)")
    lines.append("═══════════════════════════════════════════════════════")
    lines.append(f"Documents: {len(set(c.get('filename','') for c in top_chunks))} files, "
                 f"{doc_chunks_count} total chunks analyzed")
    lines.append("")

    # 4a. Key document excerpts (top by quality)
    lines.append("KEY DOCUMENT EXCERPTS:")
    seen_files: set[str] = set()
    excerpt_count = 0
    for chunk in top_chunks:
        if excerpt_count >= 6:
            break
        fname = chunk.get("filename", "Unknown")
        text = (chunk.get("chunk_text") or "")[:400].strip()
        if not text:
            continue
        page = chunk.get("page_no")
        loc = f"{fname}" + (f" (p{page})" if page else "")
        lines.append(f"\n  [{loc}]:")
        lines.append(f"  {text}")
        seen_files.add(fname)
        excerpt_count += 1

    # 4b. Cross-references between documents and structured data
    if cross_refs:
        lines.append("")
        lines.append("DOCUMENT-TO-TABLE CROSS-REFERENCES:")
        lines.append("(Domain concepts found in BOTH documents and structured tables)")
        # Deduplicate and show most interesting
        seen_xrefs: set[str] = set()
        xref_count = 0
        for xr in cross_refs:
            if xref_count >= 8:
                break
            key = f"{xr['term']}|{xr['filename']}"
            if key in seen_xrefs:
                continue
            seen_xrefs.add(key)
            term = xr["term"]
            fname = xr["filename"]
            page = xr.get("page_no")
            excerpt = xr["excerpt"][:200]
            loc = f"{fname}" + (f" (p{page})" if page else "")
            # Find which table this term maps to
            table_match = ""
            for col, tbl in column_to_table.items():
                if col.replace("_", " ").lower() == term.lower():
                    table_match = f" → maps to {tbl}.{col}"
                    break
            lines.append(f"\n  Concept: \"{term}\"{table_match}")
            lines.append(f"  Document: [{loc}]: {excerpt}")
            xref_count += 1

    lines.append("")
    lines.append("USE THESE CROSS-REFERENCES to ask enriched questions that demonstrate")
    lines.append("understanding of BOTH the structured tables AND the document content.")
    lines.append("Reference specific document findings alongside table columns in your questions.")
    lines.append("═══════════════════════════════════════════════════════")

    return "\n".join(lines)


async def _build_requirements_document_intelligence(
    data_product_id: str,
    workflow_snapshot: dict,
) -> str:
    """Pre-compute document intelligence tuned for BRD requirements capture.

    Unlike discovery's _build_document_intelligence (which cross-refs chunks with
    table metadata), this function fetches structured doc_facts, doc_fact_links,
    and BRD-relevant chunks to give the model-builder concrete metric hints,
    thresholds, and cross-references BEFORE it asks its first question.

    Returns a formatted section to inject into the model-builder context.
    """
    from services.postgres import get_pool as _gp, query as _q
    from config import get_effective_settings

    settings = get_effective_settings()
    pool = await _gp(settings.database_url)

    # ── 1. Extract domain terms from Data Description ────────────────────
    dd_content = workflow_snapshot.get("data_description_content", "")
    domain_terms: list[str] = []
    if dd_content:
        # Parse table/column names from Data Description text
        # Patterns: "TABLE_NAME" headers, "COLUMN_NAME" in column lists
        import re as _re

        # Match words that look like column/table names (UPPER_SNAKE or MixedCase)
        raw_terms = _re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b", dd_content)
        # Filter out generic terms
        _skip = {
            "THE", "AND", "FOR", "NOT", "ARE", "ALL", "HAS", "WAS", "BUT",
            "TABLE", "COLUMN", "TYPE", "NULL", "DATA", "NAME", "VALUE",
            "PRIMARY", "FOREIGN", "KEY", "INDEX", "CREATE", "ALTER",
            "SELECT", "FROM", "WHERE", "ORDER", "GROUP", "HAVING",
            "INSERT", "UPDATE", "DELETE", "DROP", "SECTION", "DESCRIPTION",
            "PRODUCT", "QUALITY", "SCORE", "TOTAL", "COUNT",
        }
        for t in raw_terms:
            if t not in _skip and len(t) > 3:
                readable = t.replace("_", " ").strip().lower()
                if readable and readable not in domain_terms:
                    domain_terms.append(readable)
        domain_terms = domain_terms[:30]  # Cap

    # ── 2. Fetch doc_facts — structured assertions from documents ────────
    metric_hints: list[dict] = []
    threshold_hints: list[dict] = []
    try:
        facts = await _q(
            pool,
            """SELECT f.fact_type, f.subject_key, f.predicate, f.object_value,
                      f.numeric_value, f.currency, f.object_unit, f.confidence,
                      ud.filename, f.source_page
                 FROM doc_facts f
                 JOIN uploaded_documents ud ON ud.id = f.document_id
                WHERE f.data_product_id = $1::uuid
                  AND COALESCE(ud.is_deleted, false) = false
                ORDER BY f.confidence DESC NULLS LAST
                LIMIT 30""",
            data_product_id,
        )
        for f in facts:
            entry = {
                "subject": f.get("subject_key", ""),
                "predicate": f.get("predicate", ""),
                "object": f.get("object_value", ""),
                "numeric": f.get("numeric_value"),
                "unit": f.get("object_unit", ""),
                "currency": f.get("currency", ""),
                "filename": f.get("filename", ""),
                "page": f.get("source_page"),
                "type": f.get("fact_type", ""),
            }
            # Classify: metric hints vs threshold hints
            ftype = (f.get("fact_type") or "").lower()
            pred = (f.get("predicate") or "").lower()
            if any(
                kw in ftype or kw in pred
                for kw in ("metric", "kpi", "formula", "rate", "ratio", "measure", "calculate")
            ):
                metric_hints.append(entry)
            elif f.get("numeric_value") is not None:
                threshold_hints.append(entry)
            else:
                # Default: if it has a numeric value it's a threshold, else metric hint
                metric_hints.append(entry)
    except Exception as e:
        logger.warning("Failed to fetch doc_facts for requirements: %s", e)

    # ── 3. Fetch doc_fact_links — cross-references to structured data ────
    cross_refs: list[dict] = []
    try:
        links = await _q(
            pool,
            """SELECT fl.target_domain, fl.target_key, fl.link_reason,
                      fl.link_confidence, f.subject_key, f.object_value
                 FROM doc_fact_links fl
                 JOIN doc_facts f ON f.id = fl.fact_id
                WHERE f.data_product_id = $1::uuid
                ORDER BY fl.link_confidence DESC NULLS LAST
                LIMIT 20""",
            data_product_id,
        )
        for lk in links:
            cross_refs.append({
                "subject": lk.get("subject_key", ""),
                "object": lk.get("object_value", ""),
                "target_domain": lk.get("target_domain", ""),
                "target_key": lk.get("target_key", ""),
                "reason": lk.get("link_reason", ""),
            })
    except Exception as e:
        logger.warning("Failed to fetch doc_fact_links for requirements: %s", e)

    # ── 4. Fetch BRD-relevant chunks ─────────────────────────────────────
    brd_keywords = [
        "metric", "KPI", "rate", "ratio", "threshold", "compliance",
        "regulation", "target", "benchmark", "formula", "standard",
        "requirement", "criterion", "limit", "tolerance",
    ]
    # Combine with domain terms for richer search
    search_terms = list(set(brd_keywords + domain_terms[:10]))

    brd_chunks: list[dict] = []
    seen_chunk_texts: set[str] = set()
    for term in search_terms[:20]:
        try:
            matches = await _q(
                pool,
                """SELECT c.chunk_text, ud.filename, c.page_no
                     FROM doc_chunks c
                     JOIN uploaded_documents ud ON ud.id = c.document_id
                    WHERE c.data_product_id = $1::uuid
                      AND COALESCE(ud.is_deleted, false) = false
                      AND c.chunk_text IS NOT NULL
                      AND length(c.chunk_text) > 50
                      AND LOWER(c.chunk_text) LIKE $2
                    LIMIT 2""",
                data_product_id,
                f"%{term.lower()}%",
            )
            for m in matches:
                text_key = (m.get("chunk_text") or "")[:100]
                if text_key in seen_chunk_texts:
                    continue
                seen_chunk_texts.add(text_key)
                brd_chunks.append({
                    "filename": m.get("filename", ""),
                    "page": m.get("page_no"),
                    "excerpt": (m.get("chunk_text") or "")[:300].strip(),
                })
        except Exception:
            continue

    # Limit to most relevant
    brd_chunks = brd_chunks[:10]

    # ── 5. Build suggested enrichments ───────────────────────────────────
    enrichments: list[str] = []
    # Derived metrics from ratio/rate patterns in facts
    for mh in metric_hints:
        pred = (mh.get("predicate") or "").lower()
        obj = mh.get("object") or ""
        subj = mh.get("subject") or ""
        fname = mh.get("filename") or ""
        if any(kw in pred or kw in obj.lower() for kw in ("rate", "ratio", "per", "percentage", "/")):
            enrichments.append(
                f'DERIVED METRIC: "{subj}: {obj}" (from {fname})'
            )
        elif any(kw in pred for kw in ("formula", "calculate", "compute")):
            enrichments.append(
                f'DERIVED METRIC: "{subj}: {obj}" (from {fname})'
            )
    # Regulatory thresholds from numeric facts
    for th in threshold_hints:
        subj = th.get("subject") or ""
        num = th.get("numeric")
        unit = th.get("unit") or ""
        fname = th.get("filename") or ""
        if num is not None:
            enrichments.append(
                f'REGULATORY THRESHOLD: "{subj}" = {num} {unit} (from {fname})'
            )
    # Composite dimensions from cross-references
    for xr in cross_refs:
        subj = xr.get("subject") or ""
        domain = xr.get("target_domain") or ""
        key = xr.get("target_key") or ""
        if domain and key:
            enrichments.append(
                f'COMPOSITE DIMENSION: "{subj}" maps to {domain}.{key}'
            )
    enrichments = enrichments[:10]  # Cap

    # ── 6. Format output ─────────────────────────────────────────────────
    if not metric_hints and not threshold_hints and not cross_refs and not brd_chunks:
        return ""

    lines: list[str] = []
    lines.append("═══════════════════════════════════════════════════════")
    lines.append("REQUIREMENTS DOCUMENT INTELLIGENCE (pre-computed)")
    lines.append("═══════════════════════════════════════════════════════")

    def _truncate(s: str, maxlen: int = 80) -> str:
        return s[:maxlen].rstrip() + ("..." if len(s) > maxlen else "")

    if metric_hints:
        lines.append("")
        lines.append("DOCUMENT-SOURCED METRICS AND KPIS:")
        seen_metric_subjects: set[str] = set()
        for mh in metric_hints[:8]:
            subj = _truncate(mh.get("subject") or "?", 80)
            dedup_key = subj.lower()
            if dedup_key in seen_metric_subjects:
                continue
            seen_metric_subjects.add(dedup_key)
            obj = _truncate(mh.get("object") or mh.get("predicate") or "", 120)
            fname = mh.get("filename") or ""
            page = mh.get("page")
            loc = f"from {fname}" + (f" p{page}" if page else "")
            lines.append(f'  - "{subj}": {obj} ({loc})')

    if threshold_hints:
        lines.append("")
        lines.append("DOCUMENT-SOURCED THRESHOLDS AND VALUES:")
        seen_threshold_keys: set[str] = set()
        for th in threshold_hints[:8]:
            subj = _truncate(th.get("subject") or "?", 80)
            num = th.get("numeric")
            unit = th.get("unit") or ""
            dedup_key = f"{subj.lower()}|{num}|{unit}"
            if dedup_key in seen_threshold_keys:
                continue
            seen_threshold_keys.add(dedup_key)
            currency = th.get("currency") or ""
            fname = th.get("filename") or ""
            page = th.get("page")
            loc = f"from {fname}" + (f" p{page}" if page else "")
            val_str = f"{currency}{num} {unit}".strip() if num is not None else "N/A"
            lines.append(f'  - "{subj}": {val_str} ({loc})')

    if cross_refs:
        lines.append("")
        lines.append("DOCUMENT-TO-DATA CROSS-REFERENCES:")
        seen_xref_keys: set[str] = set()
        xref_shown = 0
        for xr in cross_refs:
            if xref_shown >= 8:
                break
            subj = _truncate(xr.get("subject") or "?", 80)
            domain = xr.get("target_domain") or ""
            key = xr.get("target_key") or ""
            dedup_key = f"{subj.lower()}|{domain}|{key}"
            if dedup_key in seen_xref_keys:
                continue
            seen_xref_keys.add(dedup_key)
            reason = _truncate(xr.get("reason") or "", 100)
            target = f"{domain}.{key}" if domain else key
            lines.append(f'  - Fact "{subj}" -> links to {target}' + (f" ({reason})" if reason else ""))
            xref_shown += 1

    if brd_chunks:
        lines.append("")
        lines.append("BRD-RELEVANT EXCERPTS:")
        for ch in brd_chunks[:6]:
            fname = ch.get("filename") or ""
            page = ch.get("page")
            excerpt = ch.get("excerpt") or ""
            loc = f"{fname}" + (f" (p{page})" if page else "")
            lines.append(f"  - [{loc}]: {excerpt}")

    if enrichments:
        lines.append("")
        lines.append("SUGGESTED ENRICHMENTS (verify with user):")
        for e in enrichments:
            lines.append(f"  - {e}")

    lines.append("")
    lines.append("USE THESE FINDINGS to ask informed, domain-specific questions.")
    lines.append("Reference specific document findings by name in your first round.")
    lines.append("PROPOSE at least 1 derived metric and 1 document-informed dimension.")
    lines.append("═══════════════════════════════════════════════════════")

    return "\n".join(lines)


def _looks_like_pbix(filename: str, mime_type: str) -> bool:
    """Return True when a file is likely a Power BI PBIX package."""
    lower = (filename or "").lower()
    return lower.endswith(".pbix") or mime_type == "application/vnd.ms-powerbi"


def _extract_pbix_text_summary(filename: str, base64_data: str) -> str | None:
    """Extract lightweight model clues from PBIX (zip) without binary passthrough."""
    try:
        raw = base64.b64decode(base64_data)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            members = set(zf.namelist())
            layout_text = ""
            if "Report/Layout" in members:
                layout_bytes = zf.read("Report/Layout")
                # Most PBIX layout files are UTF-16LE JSON.
                try:
                    layout_text = layout_bytes.decode("utf-16-le", errors="ignore")
                except (UnicodeDecodeError, Exception):
                    layout_text = layout_bytes.decode("utf-8", errors="ignore")

            query_refs = sorted(set(re.findall(r'"queryRef"\s*:\s*"([^"]+)"', layout_text)))
            visual_types = sorted(set(re.findall(r'"visualType"\s*:\s*"([^"]+)"', layout_text)))
            table_names = sorted({q.split(".", 1)[0] for q in query_refs if "." in q})

            ref_sample = query_refs[:80]
            visuals_sample = visual_types[:20]
            tables_sample = table_names[:40]

            summary_lines = [
                f"[Attached PBIX file: {filename}]",
                "This file is binary; extracted metadata from Report/Layout and package contents:",
                f"- Package members: {len(members)}",
            ]
            if tables_sample:
                summary_lines.append(
                    f"- Referenced tables ({len(table_names)}): {', '.join(tables_sample)}"
                )
            if ref_sample:
                summary_lines.append(
                    f"- Referenced fields/measures ({len(query_refs)}): {', '.join(ref_sample)}"
                )
            if visuals_sample:
                summary_lines.append(
                    f"- Visual types ({len(visual_types)}): {', '.join(visuals_sample)}"
                )
            if not query_refs and not visual_types:
                summary_lines.append(
                    "- No usable model references were extracted from layout; use this as a weak hint only."
                )

            return "\n".join(summary_lines)
    except (ValueError, zipfile.BadZipFile, OSError, Exception):
        logger.warning("Failed to extract PBIX summary for %s", filename, exc_info=True)
        return None


def _decode_data_uri_base64(value: str) -> tuple[str, str] | None:
    """Parse ``data:<mime>;base64,<payload>`` strings."""
    if not isinstance(value, str):
        return None
    if not value.startswith("data:") or ";base64," not in value:
        return None
    header, payload = value.split(";base64,", 1)
    mime = header[5:] if len(header) > 5 else "application/octet-stream"
    return mime or "application/octet-stream", payload


def _is_base64_zip_payload(base64_data: str) -> bool:
    """Return True when a base64 payload decodes to a ZIP header."""
    if not base64_data:
        return False
    try:
        raw = base64.b64decode(base64_data)
        return raw.startswith(b"PK\x03\x04")
    except Exception:
        return False


def _build_multimodal_content(
    text: str,
    file_contents: list | None = None,
) -> str | list[dict]:
    """Build HumanMessage content using OpenAI-compatible content blocks.

    The LiteLLM router validates user message content against OpenAI chat types.
    Use only types accepted across providers:
      - Text files (CSV/TXT/JSON/XML): decode to UTF-8 and append as text.
      - Images: ``{"type": "image_url", ...}``.
      - Audio (mp3/wav): ``{"type": "input_audio", ...}``.
      - PDFs / other binary: ``{"type": "file", "file": {...}}``.

    Returns a plain string when all attachments are text-decodable (most
    compatible path for subagent delegation). Returns a list of content blocks
    when any binary attachment is present.
    """
    if not file_contents:
        return text

    from models.schemas import FileContent

    def _as_file_block(filename: str, mime_type: str, base64_data: str) -> dict[str, Any]:
        safe_name = filename or "attachment"
        return {
            "type": "file",
            "file": {
                "filename": safe_name,
                "file_data": f"data:{mime_type};base64,{base64_data}",
            },
        }

    logger.info("Building multimodal content: %d file(s) attached", len(file_contents))
    for fc in file_contents:
        if isinstance(fc, FileContent):
            logger.info(
                "  File: %s (%s, %d bytes base64)",
                fc.filename,
                fc.content_type,
                len(fc.base64_data),
            )

    # Separate text-decodable files from binary files
    text_parts: list[str] = []
    binary_blocks: list[dict] = []

    for fc in file_contents:
        if not isinstance(fc, FileContent):
            continue

        mime = fc.content_type or "application/octet-stream"

        # --- PBIX: extract layout metadata as text (do not send raw binary) ---
        if _looks_like_pbix(fc.filename, mime):
            pbix_summary = _extract_pbix_text_summary(fc.filename, fc.base64_data)
            if pbix_summary:
                text_parts.append(pbix_summary)
            else:
                text_parts.append(
                    f"[Attached file: {fc.filename}] Binary PBIX file attached. "
                    "Could not extract structured metadata from this file."
                )
            continue

        # --- Images: image_url with base64 data URI ---
        if mime.startswith("image/"):
            binary_blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{fc.base64_data}"},
                }
            )

        # --- Audio: use input_audio only for known supported formats ---
        elif mime.startswith("audio/"):
            audio_subtype = mime.split("/", 1)[1].lower() if "/" in mime else ""
            if audio_subtype in {"wav", "x-wav"}:
                binary_blocks.append(
                    {
                        "type": "input_audio",
                        "input_audio": {"data": fc.base64_data, "format": "wav"},
                    }
                )
            elif audio_subtype in {"mp3", "mpeg"}:
                binary_blocks.append(
                    {
                        "type": "input_audio",
                        "input_audio": {"data": fc.base64_data, "format": "mp3"},
                    }
                )
            else:
                binary_blocks.append(_as_file_block(fc.filename, mime, fc.base64_data))

        # --- PDFs / documents / video: use generic file block ---
        elif mime == "application/pdf":
            binary_blocks.append(_as_file_block(fc.filename, mime, fc.base64_data))
        elif mime.startswith("video/"):
            binary_blocks.append(_as_file_block(fc.filename, mime, fc.base64_data))

        # --- Text-decodable files (CSV, TXT, JSON, XML, etc.) ---
        elif mime.startswith("text/") or mime in (
            "application/json",
            "application/xml",
            "application/csv",
        ):
            try:
                decoded = base64.b64decode(fc.base64_data).decode("utf-8")
                text_parts.append(f"[Attached file: {fc.filename}]\n{decoded[:50000]}")
            except Exception:
                text_parts.append(f"[Attached file: {fc.filename} — could not decode as text]")

        # --- Unknown binary: avoid raw passthrough (provider may reject empty docs) ---
        else:
            try:
                decoded = base64.b64decode(fc.base64_data).decode("utf-8")
                text_parts.append(f"[Attached file: {fc.filename}]\n{decoded[:50000]}")
            except Exception:
                pbix_summary = _extract_pbix_text_summary(
                    fc.filename or "attachment.pbix", fc.base64_data
                )
                if pbix_summary:
                    text_parts.append(pbix_summary)
                else:
                    text_parts.append(
                        f"[Attached file: {fc.filename}] Binary content ({mime}) could not be parsed as text."
                    )

    # If no binary attachments, return a plain string (most compatible,
    # survives Deep Agents task() delegation without losing content)
    if not binary_blocks:
        combined = text
        for tp in text_parts:
            combined += f"\n\n{tp}"
        return combined

    # Build multimodal content blocks
    blocks: list[dict] = [{"type": "text", "text": text}]
    for tp in text_parts:
        blocks.append({"type": "text", "text": tp})
    blocks.extend(binary_blocks)
    logger.info("Multimodal content: %d blocks (%s)", len(blocks), [b["type"] for b in blocks])
    return blocks


def _sanitize_checkpoint_user_content_blocks(content: Any) -> tuple[Any, bool]:
    """Sanitize historical user content blocks for provider compatibility.

    - Legacy ``type=media`` blocks are converted to text placeholders.
    - PBIX file blocks are converted to extracted text summaries (or placeholders),
      because raw PBIX bytes can fail provider-side document validation.
    """
    if not isinstance(content, list):
        return content, False

    changed = False
    normalized: list[Any] = []
    for item in content:
        if not isinstance(item, dict):
            normalized.append(item)
            continue

        block_type = item.get("type")

        # Legacy Anthropic/LangChain style block
        if block_type == "media":
            mime = str(item.get("mime_type") or "application/octet-stream")
            data = item.get("data")
            if isinstance(data, str) and data and _looks_like_pbix("attachment.pbix", mime):
                pbix_summary = _extract_pbix_text_summary("attachment.pbix", data)
                if pbix_summary:
                    normalized.append({"type": "text", "text": pbix_summary})
                else:
                    normalized.append(
                        {
                            "type": "text",
                            "text": "[Legacy PBIX attachment omitted due incompatible history format.]",
                        }
                    )
            else:
                normalized.append(
                    {
                        "type": "text",
                        "text": "[Legacy binary attachment omitted due incompatible format.]",
                    }
                )
            changed = True
            continue

        # OpenAI style file block from previous turns
        if block_type == "file":
            filename = ""
            mime = "application/octet-stream"
            base64_data = ""

            if isinstance(item.get("file"), dict):
                file_obj = item["file"]
                filename = str(file_obj.get("filename") or "")
                parsed = _decode_data_uri_base64(str(file_obj.get("file_data") or ""))
                if parsed:
                    mime, base64_data = parsed
            else:
                # Legacy file shape used by older code paths
                filename = str(item.get("filename") or "")
                mime = str(item.get("mime_type") or mime)
                base64_data = str(item.get("base64") or "")

            if _looks_like_pbix(filename, mime) or (
                mime == "application/octet-stream" and _is_base64_zip_payload(base64_data)
            ):
                pbix_summary = _extract_pbix_text_summary(
                    filename or "attachment.pbix", base64_data
                )
                if pbix_summary:
                    normalized.append({"type": "text", "text": pbix_summary})
                else:
                    normalized.append(
                        {
                            "type": "text",
                            "text": f"[Attached file: {filename or 'attachment.pbix'}] PBIX content unavailable.",
                        }
                    )
                changed = True
                continue

            if mime == "application/octet-stream":
                normalized.append(
                    {
                        "type": "text",
                        "text": f"[Attached file: {filename or 'attachment'}] Binary attachment omitted "
                        "from history due unsupported format.",
                    }
                )
                changed = True
                continue

        normalized.append(item)

    return normalized, changed


async def _run_agent(
    session_id: str,
    message: str,
    data_product_id: str,
    queue: asyncio.Queue[dict | None],
    file_contents: list | None = None,
) -> None:
    """Run the orchestrator agent and push events to the SSE queue."""
    # --- Langfuse scoring ---
    from services.langfuse_scoring import (
        PipelineTimer,
        score_brd_quality,
        score_discovery_quality,
        score_safety_net,
        score_yaml_quality,
    )

    _pipeline_timer = PipelineTimer()
    _pipeline_timer.start()
    _trace_id = session_id  # Use session_id as trace_id for Langfuse
    _yaml_retry_count: int = 0
    _yaml_passed_first: bool = True
    _verification_issues: int = 0

    # Local assistant text buffer for SSE streaming and safety net (BRD detection).
    # Chat history persistence is handled by LangGraph's PostgreSQL checkpointer.
    _assistant_texts: list[str] = []
    current_assistant_content = ""
    # Track which LLM run_id is currently streaming to detect when a new
    # agent starts speaking (prevents concatenating subagent + orchestrator output)
    _current_run_id: str | None = None
    # Orchestrator output gating: only emit tokens from subagent runs (inside task tool).
    # The orchestrator is a router — it delegates to subagents via the `task` tool.
    # All user-facing text comes from subagents; orchestrator text is suppressed.
    # Discovery runs inline (no task call) — orchestrator output IS the user-facing output.
    # For all other phases, only subagent output (inside task) is shown.
    _inside_task: bool = (
        False  # Set to True for discovery below; toggled by task tool for other phases
    )
    _subagent_completed: bool = False  # True after a `task` tool returns
    _previous_run_content: str = ""  # Last subagent run's text (for safety net)
    # Per-run dedup: buffer initial tokens to detect duplicate LLM runs within a task
    _DEDUP_CHECK_LEN = 80
    _run_token_buffer: list[str] = []
    _run_dedup_resolved: bool = True  # True once dedup check is done or not needed
    _run_suppressed: bool = False  # True if current run is a duplicate
    # Safety net: track whether save_data_description was called during discovery conversation
    _dd_tool_called: bool = False
    _erd_build_called: bool = False
    _discovery_conversation_ran: bool = False  # True after discovery-agent runs more than once
    _discovery_invocation_count: int = 0
    # Safety net: track whether save_brd was called during this invocation
    _brd_tool_called: bool = False
    _brd_artifact_uploaded: bool = False
    _transformation_phase_ran: bool = False
    _requirements_phase_ran: bool = False
    _modeling_phase_ran: bool = False
    _gold_layer_registered: bool = False
    # Safety net: track whether save_semantic_view was called during generation
    _yaml_tool_called: bool = False
    _generation_phase_ran: bool = False
    # Diagnostic counters for stream events
    _stream_event_count: int = 0
    _stream_token_count: int = 0
    _stream_llm_calls: int = 0  # on_chat_model_start count
    _stream_llm_completions: int = 0  # on_chat_model_end count
    _stream_raw_chunks: int = 0  # on_chat_model_stream with content (before gating)
    _stream_gated_out: int = 0  # tokens filtered by _inside_task / _subagent_completed
    _stream_dedup_suppressed: int = 0  # tokens suppressed by dedup
    _stream_task_calls: int = 0  # task tool invocations
    _stream_firewall_blocks: int = 0  # tokens/runs blocked by supervisor firewall
    _last_tool_name: str | None = None
    _failure_plan_message: str | None = None
    _publish_completed: bool = False
    _published_agent_fqn: str | None = None
    _publish_tool_error_name: str | None = None
    _publish_tool_error_reason: str | None = None
    _abort_stream_due_publish_error: bool = False
    _tool_contract_hints: list[dict[str, Any]] = []
    _last_tool_inputs: dict[str, Any] = {}
    _tool_call_trace: list[dict[str, Any]] = []
    _query_route_plan: dict[str, Any] | None = None
    _last_model_end_output_text: str = ""
    # Phase tracking: detect subagent transitions
    _SUBAGENT_PHASE_MAP: dict[str, str] = {
        "discovery-agent": "discovery",
        "transformation-agent": "prepare",  # between discovery and requirements
        "modeling-agent": "generation",  # internal — maps to generation phase
        "model-builder": "requirements",  # default; refined by tool detection
        "publishing-agent": "publishing",
        "explorer-agent": "explorer",
    }
    _current_phase: str = "idle"
    _last_reasoning_update: str = ""
    _last_reasoning_emit_at: float = 0.0
    _reasoning_min_interval_sec: float = 4.0
    _llm_reasoning_buffer: str = ""
    _current_run_has_llm_reasoning: bool = False

    async def _emit_reasoning_update(candidate_text: str, *, source: str = "fallback") -> None:
        """Emit a compact assistant reasoning update for the live status card."""
        nonlocal _last_reasoning_update, _last_reasoning_emit_at
        snippet = _extract_reasoning_update(candidate_text)
        if not snippet:
            return
        if snippet == _last_reasoning_update:
            return

        now_ts = asyncio.get_running_loop().time()
        if now_ts - _last_reasoning_emit_at < _reasoning_min_interval_sec:
            return

        await queue.put(
            {
                "type": "reasoning_update",
                "data": {"message": snippet, "source": source},
            }
        )
        _last_reasoning_update = snippet
        _last_reasoning_emit_at = now_ts

    try:
        from agents.orchestrator import get_orchestrator
        from config import get_effective_settings

        workflow_snapshot = await _get_workflow_snapshot(data_product_id)
        _current_phase = str(workflow_snapshot.get("current_phase") or "idle")
        supervisor_forced_phase: str | None = None
        supervisor_transition_reason: str | None = None
        publish_phase, already_published = await _get_publish_gate_context(data_product_id)

        # Check if this is a discovery trigger
        actual_message = message
        is_discovery = message.strip() in (DISCOVERY_TRIGGER, RERUN_DISCOVERY_TRIGGER)
        force_rerun = message.strip() == RERUN_DISCOVERY_TRIGGER
        post_publish_agent_instruction_update_intent = (
            not is_discovery
            and already_published
            and _is_post_publish_agent_instruction_only_intent(message)
        )
        analysis_only_no_publish_intent = not is_discovery and _is_analysis_only_no_publish_intent(
            message
        )
        end_to_end_autopilot_intent = (
            not is_discovery
            and not post_publish_agent_instruction_update_intent
            and not analysis_only_no_publish_intent
            and _is_end_to_end_autopilot_intent(message)
        )
        dp_info: dict | None = None  # Set inside discovery block, used for timeout check
        if is_discovery:
            _inside_task = True  # Discovery: orchestrator interprets summary directly
            logger.info(
                "Discovery trigger detected for session %s (force=%s), running pipeline...",
                session_id,
                force_rerun,
            )
            # Emit phase change to discovery
            _current_phase = "discovery"
            await queue.put(
                {
                    "type": "phase_change",
                    "data": {"from": "idle", "to": "discovery"},
                }
            )

            # 0. Invalidate stale artifacts from prior phases when re-running discovery.
            # A new discovery means old BRD, YAML, Data Description, and ERD are no longer valid.
            if force_rerun:
                try:
                    from services.postgres import get_pool, execute as pg_execute

                    _pool = await get_pool(_settings.database_url)
                    # Delete artifacts (erd, yaml, brd, data_description) — keep data_quality (re-generated by pipeline)
                    await pg_execute(
                        _pool,
                        "DELETE FROM artifacts WHERE data_product_id = $1::uuid AND artifact_type IN ('erd', 'yaml', 'brd', 'data_description')",
                        data_product_id,
                    )
                    # Delete related table rows
                    await pg_execute(
                        _pool,
                        "DELETE FROM business_requirements WHERE data_product_id = $1::uuid",
                        data_product_id,
                    )
                    await pg_execute(
                        _pool,
                        "DELETE FROM semantic_views WHERE data_product_id = $1::uuid",
                        data_product_id,
                    )
                    await pg_execute(
                        _pool,
                        "DELETE FROM data_descriptions WHERE data_product_id = $1::uuid",
                        data_product_id,
                    )
                    # Reset published markers so the new run behaves like an active in-progress lifecycle.
                    await pg_execute(
                        _pool,
                        """UPDATE data_products
                           SET status = 'discovery'::data_product_status,
                               published_at = NULL,
                               published_agent_fqn = NULL,
                               state = jsonb_set(
                                   jsonb_set(COALESCE(state, '{}'::jsonb), '{current_phase}', '"discovery"'::jsonb),
                                   '{published}',
                                   'false'::jsonb
                               ),
                               updated_at = NOW()
                           WHERE id = $1::uuid""",
                        data_product_id,
                    )
                    logger.info(
                        "Invalidated prior-phase artifacts for data product %s", data_product_id
                    )
                except Exception as e:
                    logger.warning("Failed to invalidate prior artifacts: %s", e)

            # Keep persisted phase aligned for both forced and non-forced discovery starts.
            await _persist_phase(data_product_id, "discovery")

            # 1. Get data product details
            dp_info = await _get_data_product_info(data_product_id)
            if dp_info is None:
                actual_message = "Please tell me about the data you want to analyze."
            elif not dp_info["tables"]:
                actual_message = f"I see you created '{dp_info['name']}' but no tables are selected yet. Please add tables in the Data Source Settings."
            else:
                # 2. Run deterministic pipeline (emits progress events to queue)
                from services.discovery_pipeline import run_discovery_pipeline

                pipeline_results = await run_discovery_pipeline(
                    data_product_id=data_product_id,
                    tables=dp_info["tables"],
                    database=dp_info["database"],
                    schemas=dp_info["schemas"],
                    queue=queue,
                    force=force_rerun,
                )

                # 2a. Score discovery quality
                score_discovery_quality(_trace_id, pipeline_results)
                _pipeline_timer.phase_started("discovery")

                # 2b. Ensure artifact events are emitted (cache path skips step 7)
                # Only emit if pipeline returned from cache (no artifact events were sent)
                if pipeline_results.get("_cached_at"):
                    cached_artifacts = pipeline_results.get("artifacts", {})
                    if isinstance(cached_artifacts, dict):
                        artifact_ids = cached_artifacts.get("artifact_ids", {})
                        # Map storage types to frontend types — only quality_report in Phase 1
                        # ERD comes later from the discovery conversation (Phase 2)
                        _ART_TYPE_MAP = {"quality_report": "data_quality"}
                        for art_type, art_id in artifact_ids.items():
                            if art_id and art_type == "quality_report":
                                await queue.put(
                                    {
                                        "type": "artifact",
                                        "data": {
                                            "artifact_id": art_id,
                                            "artifact_type": _ART_TYPE_MAP.get(art_type, art_type),
                                        },
                                    }
                                )

                    # Emit cached maturity tier for frontend phase stepper
                    _cached_maturity = pipeline_results.get("maturity_classifications", {})
                    if _cached_maturity:
                        _cached_tiers = [
                            info.get("maturity", "gold") for info in _cached_maturity.values()
                        ]
                        if "bronze" in _cached_tiers:
                            _cached_tier = "bronze"
                        elif "silver" in _cached_tiers:
                            _cached_tier = "silver"
                        else:
                            _cached_tier = "gold"
                    else:
                        _cached_tier = "gold"
                    await queue.put(
                        {
                            "type": "data_maturity",
                            "data": {"tier": _cached_tier},
                        }
                    )

                    # Persist data_tier for cached path too (gate depends on it)
                    try:
                        from services.postgres import get_pool as _gp2, execute as _ex2

                        _dp_pool2 = await _gp2(_settings.database_url)
                        await _ex2(
                            _dp_pool2,
                            """UPDATE data_products
                               SET state = jsonb_set(COALESCE(state, '{}'::jsonb), '{data_tier}', $1::jsonb)
                               WHERE id = $2::uuid""",
                            f'"{_cached_tier}"',
                            data_product_id,
                        )
                    except Exception as _e:
                        logger.warning("Failed to persist cached data_tier: %s", _e)

                # 3. Build human-readable summary for the LLM
                actual_message = _build_discovery_summary(
                    pipeline_results,
                    dp_info["name"],
                    data_product_id,
                    dp_description=dp_info["description"],
                )

                # 3b. Append document intelligence for hybrid/document products
                _ws = workflow_snapshot
                if _ws.get("has_documents"):
                    try:
                        _doc_intel = await _build_document_intelligence(
                            data_product_id,
                            pipeline_results,
                            doc_chunks_count=_ws.get("doc_chunks_count", 0),
                        )
                    except Exception as e:
                        logger.warning("Document intelligence pre-computation failed: %s", e)
                        _doc_intel = ""

                    if _doc_intel:
                        actual_message += "\n\n" + _doc_intel
                    else:
                        # Fallback: at least flag that documents exist
                        _doc_section = (
                            "\n\n═══════════════════════════════════════════════════════\n"
                            "UPLOADED DOCUMENTS\n"
                            "═══════════════════════════════════════════════════════\n"
                            f"has_documents=True\n"
                            f"doc_chunks_count={_ws.get('doc_chunks_count', 0)}\n"
                            f"product_type={_ws.get('product_type', 'hybrid')}\n\n"
                            "This is a hybrid data product with uploaded documents. "
                            "Call search_document_chunks to explore document content.\n"
                            "═══════════════════════════════════════════════════════"
                        )
                        actual_message += _doc_section

                logger.info("Pipeline complete, summary length: %d chars", len(actual_message))

                # 4. Emit data maturity tier so frontend can adapt the phase stepper.
                # Only emit when NOT cached — cached path already emits above.
                if not pipeline_results.get("_cached_at"):
                    # Aggregate = minimum tier across all tables (conservative).
                    _maturity = pipeline_results.get("maturity_classifications", {})
                    if _maturity:
                        _tiers = [info.get("maturity", "gold") for info in _maturity.values()]
                        if "bronze" in _tiers:
                            _aggregate_tier = "bronze"
                        elif "silver" in _tiers:
                            _aggregate_tier = "silver"
                        else:
                            _aggregate_tier = "gold"
                    else:
                        _aggregate_tier = "gold"

                    await queue.put(
                        {
                            "type": "data_maturity",
                            "data": {"tier": _aggregate_tier},
                        }
                    )

                    # Persist to data_products.state JSONB for session recovery
                    try:
                        from services.postgres import get_pool as _gp, execute as _ex

                        _dp_pool = await _gp(_settings.database_url)
                        await _ex(
                            _dp_pool,
                            """UPDATE data_products
                               SET state = jsonb_set(COALESCE(state, '{}'::jsonb), '{data_tier}', $1::jsonb)
                               WHERE id = $2::uuid""",
                            f'"{_aggregate_tier}"',
                            data_product_id,
                        )
                    except Exception as _e:
                        logger.warning("Failed to persist data_tier: %s", _e)

                # Auto-advance from discovery to requirements when entry
                # conditions are satisfied. This prevents workflow stalls where
                # discovery completed but the phase stepper remains unchanged.
                try:
                    refreshed_snapshot = await _get_workflow_snapshot(data_product_id)
                    is_ready, readiness_reason = _requirements_entry_ready(refreshed_snapshot)
                    if is_ready and _current_phase != "requirements":
                        old_phase = _current_phase
                        _current_phase = "requirements"
                        workflow_snapshot["current_phase"] = "requirements"
                        _pipeline_timer.phase_started("requirements")
                        await queue.put(
                            {
                                "type": "phase_change",
                                "data": {"from": old_phase, "to": "requirements"},
                            }
                        )
                        await _persist_phase(data_product_id, "requirements")
                        logger.info(
                            "Auto-transitioned phase after discovery: %s -> requirements (session=%s reason=%s)",
                            old_phase,
                            session_id,
                            readiness_reason,
                        )
                    else:
                        logger.info(
                            "Requirements auto-transition deferred after discovery (session=%s reason=%s)",
                            session_id,
                            readiness_reason,
                        )
                except Exception as transition_err:
                    logger.warning(
                        "Failed to evaluate discovery -> requirements auto-transition for session %s: %s",
                        session_id,
                        transition_err,
                    )
        else:
            # Deterministic supervisor transition gate (prevents stuck phase when
            # user asks to proceed and prerequisites are already satisfied).
            supervisor_forced_phase, supervisor_transition_reason = _evaluate_supervisor_transition(
                message=message,
                snapshot=workflow_snapshot,
                already_published=already_published,
            )
            if supervisor_forced_phase and supervisor_forced_phase != _current_phase:
                old_phase = _current_phase
                _current_phase = supervisor_forced_phase
                workflow_snapshot["current_phase"] = supervisor_forced_phase
                _pipeline_timer.phase_started(supervisor_forced_phase)
                await queue.put(
                    {
                        "type": "phase_change",
                        "data": {"from": old_phase, "to": supervisor_forced_phase},
                    }
                )
                await _persist_phase(data_product_id, supervisor_forced_phase)
                logger.info(
                    "Supervisor forced phase transition: %s -> %s (session=%s reason=%s)",
                    old_phase,
                    supervisor_forced_phase,
                    session_id,
                    supervisor_transition_reason,
                )
            elif supervisor_transition_reason and _is_requirements_transition_intent(message):
                logger.info(
                    "Supervisor blocked requirements transition for session=%s reason=%s",
                    session_id,
                    supervisor_transition_reason,
                )
            elif supervisor_transition_reason and post_publish_agent_instruction_update_intent:
                logger.info(
                    "Supervisor routing hint (post-publish instruction update) for session=%s reason=%s",
                    session_id,
                    supervisor_transition_reason,
                )

        # Langfuse tracing is now handled at the model level in services/llm.py
        # Each LLM call will be automatically traced with input/output
        settings = get_effective_settings()
        logger.info(
            "Starting agent for session %s (provider: %s, langfuse=%s)",
            session_id,
            settings.llm_provider,
            bool(settings.langfuse_public_key and settings.langfuse_secret_key),
        )

        # --- Data isolation: scope tools to this data product's database ---
        from tools.snowflake_tools import set_data_isolation_context, set_publish_approval_context
        from tools.postgres_tools import set_data_product_context, set_data_product_name_context

        # Set data_product_id contextvar — ensures tools always use the correct UUID
        # even if the LLM truncates or mangles the ID in tool call arguments.
        set_data_product_context(data_product_id)

        dp_info_for_isolation = await _get_data_product_info(data_product_id)
        if dp_info_for_isolation:
            set_data_isolation_context(
                database=dp_info_for_isolation["database"],
                tables=dp_info_for_isolation["tables"],
            )
            set_data_product_name_context(dp_info_for_isolation["name"])
        else:
            set_data_isolation_context(database=None, tables=None)

        # Publish gate: deployment tools only run when user explicitly approved.
        # Approval is scoped to this invocation and reset every turn.
        is_publish_phase = publish_phase == "publishing"
        # Explicit publish wording is valid even for re-publish workflows where
        # the data product already has a published artifact history.
        explicit_publish_preapproval = _is_explicit_publish_approval(
            message,
            allow_bare_ack=False,
        )
        publish_approved = explicit_publish_preapproval
        if not publish_approved:
            publish_approved = is_publish_phase and _is_explicit_publish_approval(
                message, allow_bare_ack=True
            )
        # Allow a direct "publish now" style request from validation, but require
        # explicit publish wording (bare "yes" is not enough before publishing phase).
        if (
            not publish_approved
            and publish_phase == "validation"
            and _is_explicit_publish_approval(message, allow_bare_ack=False)
        ):
            publish_approved = True
        # Post-publish instruction updates (agent behavior/prompt only) are
        # explicit redeploy intents; allow publishing tools for this turn.
        if not publish_approved and post_publish_agent_instruction_update_intent:
            publish_approved = True
        if analysis_only_no_publish_intent:
            publish_approved = False
            explicit_publish_preapproval = False

        set_publish_approval_context(publish_approved)
        logger.info(
            "Publish approval gate for session %s: phase=%s published=%s approved=%s preapproved=%s autopilot=%s instruction_update_intent=%s analysis_only_no_publish=%s",
            session_id,
            publish_phase,
            already_published,
            publish_approved,
            explicit_publish_preapproval,
            end_to_end_autopilot_intent,
            post_publish_agent_instruction_update_intent,
            analysis_only_no_publish_intent,
        )

        agent = await get_orchestrator()
        config = {
            "configurable": {"thread_id": session_id},
            "recursion_limit": _settings.agent_recursion_limit,  # Shared between orchestrator + subagents
        }

        # Fix empty-content AI messages in checkpoint — Gemini rejects messages
        # with empty parts.  These occur when the orchestrator calls tools
        # (e.g. task) with no text content.
        from langchain_core.messages import RemoveMessage as _RM

        _chk_state = await agent.aget_state(config)
        _chk_msgs = (
            _chk_state.values.get("messages", []) if _chk_state and _chk_state.values else []
        )
        _patches: list = []
        for _m in _chk_msgs:
            if _m.type == "ai":
                _c = _m.content
                _is_empty = (
                    not _c or _c == [] or _c == "" or (isinstance(_c, list) and len(_c) == 0)
                )
                if _is_empty:
                    if getattr(_m, "tool_calls", None):
                        _patches.append(AIMessage(content=".", id=_m.id, tool_calls=_m.tool_calls))
                    else:
                        _patches.append(_RM(id=_m.id))
            elif _m.type == "human":
                _normalized_content, _changed = _sanitize_checkpoint_user_content_blocks(_m.content)
                if _changed:
                    _patches.append(
                        HumanMessage(
                            content=_normalized_content,
                            id=_m.id,
                            additional_kwargs=getattr(_m, "additional_kwargs", {}),
                        )
                    )
        if _patches:
            try:
                await agent.aupdate_state(config, {"messages": _patches})
                logger.info(
                    "Patched %d checkpoint messages (empty AI and/or legacy media) for session %s",
                    len(_patches),
                    session_id,
                )
            except (UnboundLocalError, Exception) as patch_err:
                # LangGraph may fail internally when patching certain checkpoint states
                # (e.g. last_ai_index UnboundLocalError). Log and continue — the agent
                # can still function; empty messages may cause Gemini to complain but
                # the fallback/safety nets will handle it.
                logger.warning(
                    "Failed to patch checkpoint messages for session %s: %s. Continuing without patch.",
                    session_id,
                    patch_err,
                )

        # Wire the SSE queue contextvar so build_erd_from_description can emit artifact events
        from tools.discovery_tools import _sse_queue

        _sse_queue.set(queue)

        # With PostgreSQL checkpointer, LangGraph automatically restores
        # conversation history for this thread_id. We only send the new message.
        # Inject supervisor context contract for non-discovery turns only.
        # This keeps subagent routing deterministic without exposing internals to the user.
        document_context_contract: dict[str, Any] | None = None
        if not is_discovery:
            effective_phase = supervisor_forced_phase or _current_phase
            document_context_contract = await _get_document_context_contract(
                data_product_id=data_product_id,
                phase=effective_phase,
            )
            if settings.feature_hybrid_planner:
                _query_route_plan = _build_query_route_plan(
                    message,
                    current_phase=effective_phase,
                    already_published=already_published,
                    has_documents=bool(workflow_snapshot.get("has_documents")),
                )
                logger.info(
                    "Hybrid route plan (session=%s): intent=%s lanes=%s exact_required=%s",
                    session_id,
                    _query_route_plan.get("intent"),
                    _query_route_plan.get("lanes"),
                    _query_route_plan.get("requires_exact_evidence"),
                )
            else:
                _query_route_plan = None
                logger.info(
                    "Hybrid route planner disabled by feature flag for session=%s",
                    session_id,
                )

        if not is_discovery:
            forced_intent_value: str | None = None
            if post_publish_agent_instruction_update_intent:
                forced_intent_value = "post_publish_agent_instruction_update"
            elif analysis_only_no_publish_intent:
                forced_intent_value = "analysis_only_no_publish"
            elif end_to_end_autopilot_intent:
                forced_intent_value = "autopilot_end_to_end"

            forced_subagent_name: str | None = None
            if post_publish_agent_instruction_update_intent:
                forced_subagent_name = "publishing-agent"
            elif analysis_only_no_publish_intent:
                forced_subagent_name = "explorer-agent"

            actual_message = _build_supervisor_contract(
                snapshot=workflow_snapshot,
                user_message=actual_message,
                transition_target=supervisor_forced_phase,
                transition_reason=supervisor_transition_reason,
                data_product_id=data_product_id,
                already_published=already_published,
                forced_subagent=forced_subagent_name,
                forced_intent=forced_intent_value,
                run_mode="autopilot_end_to_end" if end_to_end_autopilot_intent else None,
                publish_preapproved=bool(explicit_publish_preapproval),
                document_context=document_context_contract,
                query_route_plan=_query_route_plan,
            )

            # Inject artifact appendices for deterministic subagent context
            effective_phase = supervisor_forced_phase or _current_phase
            artifact_appendices = _build_artifact_appendices(
                snapshot=workflow_snapshot,
                current_phase=effective_phase,
            )
            if artifact_appendices:
                actual_message += artifact_appendices

            # Inject requirements-specific document intelligence for hybrid/document products
            if (
                workflow_snapshot.get("has_documents")
                and effective_phase == "requirements"
                and workflow_snapshot.get("doc_facts_count", 0) > 0
            ):
                try:
                    req_doc_intel = await _build_requirements_document_intelligence(
                        data_product_id, workflow_snapshot
                    )
                except Exception as e:
                    logger.warning("Requirements document intelligence failed: %s", e)
                    req_doc_intel = ""
                if req_doc_intel:
                    actual_message += "\n\n" + req_doc_intel
                    logger.info(
                        "Injected requirements document intelligence: %d chars",
                        len(req_doc_intel),
                    )

        content = _build_multimodal_content(actual_message, file_contents)
        input_messages = {"messages": [HumanMessage(content=content)]}

        # Stream events from the agent.
        # For discovery of large datasets, apply a timeout to prevent indefinite hangs
        # when the LLM fails to respond to very large summaries.
        # Timeout for large discovery: use table count (available from dp_info), not
        # summary length (already truncated by this point).
        _dp_table_count = len(dp_info.get("tables", [])) if is_discovery and dp_info else 0
        _agent_timeout = (
            180 if is_discovery and _dp_table_count > 15 else None
        )  # 3 min for large datasets
        _agent_stream = agent.astream_events(input_messages, config=config, version="v2")
        if _agent_timeout:
            logger.info(
                "Applying %ds timeout for large discovery summary (%d chars)",
                _agent_timeout,
                len(actual_message),
            )

        # Use wait_for on each iteration to enforce timeout on blocked LLM calls.
        # A simple `async for` blocks if the LLM never responds.
        _iter = _agent_stream.__aiter__()
        while True:
            try:
                if _agent_timeout:
                    event = await asyncio.wait_for(_iter.__anext__(), timeout=_agent_timeout)
                else:
                    event = await _iter.__anext__()
            except StopAsyncIteration:
                break
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning(
                    "Agent stream timed out after %ds for session %s (events=%d, tokens=%d)",
                    _agent_timeout,
                    session_id,
                    _stream_event_count,
                    _stream_token_count,
                )
                break

            _stream_event_count += 1
            kind = event.get("event", "")
            data = event.get("data", {})

            # Track LLM call lifecycle for diagnostics
            if kind == "on_chat_model_start":
                _stream_llm_calls += 1
                _llm_model = event.get("name", "unknown")
                logger.info(
                    "LLM call #%d started (model=%s, session=%s, inside_task=%s, subagent_completed=%s)",
                    _stream_llm_calls,
                    _llm_model,
                    session_id,
                    _inside_task,
                    _subagent_completed,
                )
            elif kind == "on_chat_model_end":
                _stream_llm_completions += 1
                # Log output summary
                _end_output = data.get("output")
                _end_content_len = 0
                _end_tool_calls = 0
                if _end_output:
                    if hasattr(_end_output, "content"):
                        _c = _end_output.content
                        _end_content_len = len(_c) if isinstance(_c, str) else len(str(_c))
                    if hasattr(_end_output, "tool_calls"):
                        _end_tool_calls = len(_end_output.tool_calls or [])
                logger.info(
                    "LLM call #%d completed (content_len=%d, tool_calls=%d, session=%s)",
                    _stream_llm_completions,
                    _end_content_len,
                    _end_tool_calls,
                    session_id,
                )
                if _end_output:
                    _end_text, _end_reasoning = _extract_stream_payloads(_end_output)
                    if _end_text and len(_end_text) > len(_last_model_end_output_text):
                        _last_model_end_output_text = _end_text
                    can_emit_reasoning = _inside_task or (_stream_task_calls == 0)
                    if can_emit_reasoning and _end_reasoning:
                        _current_run_has_llm_reasoning = True
                        _llm_reasoning_buffer = _append_reasoning_buffer(
                            _llm_reasoning_buffer,
                            _end_reasoning,
                            max_chars=2000,
                        )
                        await _emit_reasoning_update(_llm_reasoning_buffer, source="llm")

            if kind == "on_chat_model_stream":
                # Token streaming — only emit tokens from subagent runs.
                # The orchestrator is a router; its text is always suppressed.
                run_id = event.get("run_id", "")
                chunk = data.get("chunk")
                if not chunk:
                    continue

                content, llm_reasoning = _extract_stream_payloads(chunk)
                if not content and not llm_reasoning:
                    continue

                if content:
                    _stream_raw_chunks += 1

                # If a new LLM run starts, emit message_done to close previous bubble.
                if run_id and run_id != _current_run_id:
                    # Flush any dedup buffer from the ending run
                    if _run_token_buffer and not _run_suppressed:
                        buffered_text = "".join(_run_token_buffer)
                        # Short message that didn't reach threshold — check if duplicate
                        if _previous_run_content and _previous_run_content.startswith(
                            buffered_text.strip()
                        ):
                            _run_suppressed = True
                            logger.info("Suppressing short duplicate subagent run")
                        elif _is_internal_reasoning_leak(buffered_text):
                            _run_suppressed = True
                            _stream_firewall_blocks += 1
                            logger.warning(
                                "Supervisor firewall blocked short internal run for session %s",
                                session_id,
                            )
                        else:
                            current_assistant_content = buffered_text
                            if not _current_run_has_llm_reasoning:
                                await _emit_reasoning_update(
                                    current_assistant_content, source="fallback"
                                )
                            for tok in _run_token_buffer:
                                await queue.put(
                                    {
                                        "type": "token",
                                        "data": {"content": tok},
                                    }
                                )
                        _run_token_buffer = []

                    if _current_run_id is not None and current_assistant_content.strip():
                        _finalized = _sanitize_assistant_text(current_assistant_content).strip()
                        if _finalized:
                            _previous_run_content = _finalized
                            _assistant_texts.append(_finalized)
                        current_assistant_content = ""
                        await queue.put(
                            {
                                "type": "message_done",
                                "data": {"content": _finalized},
                            }
                        )
                    _current_run_id = run_id
                    # Reset per-run dedup state
                    _run_token_buffer = []
                    _run_suppressed = False
                    _run_dedup_resolved = not (_inside_task and bool(_previous_run_content))
                    _current_run_has_llm_reasoning = False

                # Gate: only emit chunks from subagent runs (inside task tool)
                # After a subagent completes, allow orchestrator synthesis to flow through —
                # otherwise the final user-facing response is swallowed.
                if not _inside_task and not _subagent_completed:
                    _stream_gated_out += 1
                    continue

                if llm_reasoning:
                    _current_run_has_llm_reasoning = True
                    _llm_reasoning_buffer = _append_reasoning_buffer(
                        _llm_reasoning_buffer,
                        llm_reasoning,
                        max_chars=2000,
                    )
                    await _emit_reasoning_update(_llm_reasoning_buffer, source="llm")

                if not content:
                    continue

                # If current run is already marked as duplicate, suppress
                if _run_suppressed:
                    _stream_dedup_suppressed += 1
                    continue

                # Dedup check: buffer initial tokens and compare with previous run
                if not _run_dedup_resolved:
                    _run_token_buffer.append(content)
                    buffered_text = "".join(_run_token_buffer)
                    if len(buffered_text) >= _DEDUP_CHECK_LEN:
                        _run_dedup_resolved = True
                        if _previous_run_content.startswith(buffered_text.strip()):
                            _run_suppressed = True
                            logger.info(
                                "Suppressing duplicate subagent run (prefix matches previous)"
                            )
                        elif _is_internal_reasoning_leak(buffered_text):
                            _run_suppressed = True
                            _stream_firewall_blocks += 1
                            logger.warning(
                                "Supervisor firewall blocked buffered internal run for session %s",
                                session_id,
                            )
                        else:
                            # Not a duplicate — flush buffered tokens
                            current_assistant_content = buffered_text
                            if not _current_run_has_llm_reasoning:
                                await _emit_reasoning_update(
                                    current_assistant_content, source="fallback"
                                )
                            for tok in _run_token_buffer:
                                await queue.put(
                                    {
                                        "type": "token",
                                        "data": {"content": tok},
                                    }
                                )
                            _run_token_buffer = []
                    continue

                # Normal path — emit token
                current_assistant_content += content

                # Supervisor firewall: suppress internal reasoning/tool leakage.
                if _is_internal_reasoning_leak(current_assistant_content):
                    _run_suppressed = True
                    _stream_firewall_blocks += 1
                    current_assistant_content = ""
                    continue

                if not _current_run_has_llm_reasoning:
                    await _emit_reasoning_update(current_assistant_content, source="fallback")
                _stream_token_count += 1
                await queue.put(
                    {
                        "type": "token",
                        "data": {"content": content},
                    }
                )

            elif kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                tool_input = data.get("input", {})
                _last_tool_name = tool_name
                _last_tool_inputs[tool_name] = (
                    tool_input if isinstance(tool_input, dict) else str(tool_input)
                )
                if tool_name != "task":
                    _tool_call_trace.append(
                        {
                            "event": "start",
                            "tool": tool_name,
                            "phase": _current_phase,
                            "input": _summarize_tool_input_for_trace(tool_name, tool_input),
                        }
                    )
                    if len(_tool_call_trace) > 120:
                        _tool_call_trace = _tool_call_trace[-120:]

                # Track upload_artifact calls and emit artifact event from input
                # (more reliable than parsing output — subagent tool output may not propagate cleanly)
                if tool_name == "upload_artifact" and isinstance(tool_input, dict):
                    art_type = tool_input.get("artifact_type", "")
                    art_filename = tool_input.get("filename", "")
                    # Guard: correct type if filename contradicts it
                    if (
                        "data-description" in art_filename.lower()
                        and art_type != "data_description"
                    ):
                        art_type = "data_description"
                    if art_type == "brd":
                        _brd_artifact_uploaded = True
                    if art_type:
                        # Map backend artifact types to frontend types
                        _ARTIFACT_TYPE_MAP = {"quality_report": "data_quality"}
                        mapped_type = _ARTIFACT_TYPE_MAP.get(art_type, art_type)
                        await queue.put(
                            {
                                "type": "artifact",
                                "data": {
                                    "artifact_id": str(uuid4()),
                                    "artifact_type": mapped_type,
                                },
                            }
                        )
                        logger.info("Emitted artifact event from tool_start: type=%s", mapped_type)

                # Detect subagent delegation via the `task` tool and emit phase_change
                # Deep Agents uses a `task` tool with `subagent_type` parameter
                if tool_name == "task" and isinstance(tool_input, dict):
                    _stream_task_calls += 1
                    _inside_task = True  # Enable token emission for subagent
                    _subagent_completed = False
                    subagent_type = tool_input.get("subagent_type", "")
                    phase_name = _SUBAGENT_PHASE_MAP.get(subagent_type)
                    if subagent_type == "discovery-agent":
                        _discovery_invocation_count += 1
                        if _discovery_invocation_count > 1:
                            _discovery_conversation_ran = True
                    if subagent_type == "transformation-agent":
                        _transformation_phase_ran = True
                    if subagent_type == "modeling-agent":
                        _modeling_phase_ran = True
                    if subagent_type == "model-builder":
                        _requirements_phase_ran = True
                        # Model-builder phase refinement must rely on explicit
                        # mode markers, not generic keywords from copied history.
                        task_desc = str(tool_input.get("description", ""))
                        phase_hint = _infer_model_builder_phase_from_task_description(task_desc)
                        if phase_hint == "generation":
                            phase_name = "generation"
                            if _generation_phase_ran:
                                _yaml_retry_count += 1
                                _yaml_passed_first = False
                            _generation_phase_ran = True
                        elif phase_hint == "validation":
                            phase_name = "validation"
                        elif phase_hint == "requirements":
                            phase_name = "requirements"
                    if phase_name and phase_name != _current_phase:
                        # Prevent noisy phase regressions from delegated discovery
                        # calls after we already advanced into requirements+.
                        # Recovery loops across generation/validation are handled
                        # by tool-level transitions below; this guard targets only
                        # subagent delegation regressions to discovery/prepare.
                        if (
                            _current_phase in {"requirements", "modeling", "generation", "validation", "publishing", "explorer"}
                            and phase_name in {"discovery", "prepare"}
                        ):
                            logger.info(
                                "Phase regression suppressed: %s -> %s (session %s, subagent=%s)",
                                _current_phase,
                                phase_name,
                                session_id,
                                subagent_type,
                            )
                            phase_name = None
                    if phase_name and phase_name != _current_phase:
                        old_phase = _current_phase
                        _current_phase = phase_name
                        _pipeline_timer.phase_started(phase_name)
                        await queue.put(
                            {
                                "type": "phase_change",
                                "data": {"from": old_phase, "to": phase_name},
                            }
                        )
                        logger.info(
                            "Phase change: %s → %s (session %s)", old_phase, phase_name, session_id
                        )
                        # Persist current_phase to data_products.state
                        await _persist_phase(data_product_id, phase_name)

                # Model-builder tool-level phase refinement:
                # model-builder is one agent but shows as 3 phases in the UI
                if tool_name == "save_brd" and _current_phase != "requirements":
                    old_phase = _current_phase
                    _current_phase = "requirements"
                    _pipeline_timer.phase_started("requirements")
                    await queue.put(
                        {"type": "phase_change", "data": {"from": old_phase, "to": "requirements"}}
                    )
                    await _persist_phase(data_product_id, "requirements")
                # Only persisted semantic-view writes should advance to generation.
                # fetch_documentation may run during requirements refinement and
                # must not force a phase jump on confirmation-only turns.
                elif tool_name == "save_semantic_view" and _current_phase == "requirements":
                    old_phase = _current_phase
                    _current_phase = "generation"
                    _generation_phase_ran = True
                    _pipeline_timer.phase_started("generation")
                    await queue.put(
                        {"type": "phase_change", "data": {"from": old_phase, "to": "generation"}}
                    )
                    await _persist_phase(data_product_id, "generation")
                elif tool_name == "validate_semantic_view_yaml" and _current_phase != "validation":
                    old_phase = _current_phase
                    _current_phase = "validation"
                    _pipeline_timer.phase_started("validation")
                    await queue.put(
                        {"type": "phase_change", "data": {"from": old_phase, "to": "validation"}}
                    )
                    await _persist_phase(data_product_id, "validation")

                # Skip tool_call event for internal `task` tool — phase_change events handle this
                if tool_name != "task":
                    await queue.put(
                        {
                            "type": "tool_call",
                            "data": {
                                "tool": tool_name,
                                "input": (
                                    tool_input if isinstance(tool_input, dict) else str(tool_input)
                                ),
                            },
                        }
                    )

            elif kind == "on_tool_end":
                tool_name = event.get("name", "unknown")
                output = data.get("output", "")
                # Extract content from ToolMessage objects (LangChain may return these)
                if hasattr(output, "content"):
                    output = output.content
                output_payload = _coerce_tool_result_payload(output)
                output_error = (
                    _extract_tool_error_from_payload(output_payload)
                    if isinstance(output_payload, dict)
                    else None
                )
                # Truncate long outputs
                truncate_len = _settings.tool_output_truncate_length
                output_str = str(output)[:truncate_len] if output else ""
                if tool_name != "task":
                    end_trace: dict[str, Any] = {
                        "event": "end",
                        "tool": tool_name,
                        "phase": _current_phase,
                        "status": "error" if output_error else "ok",
                    }
                    if isinstance(output_payload, dict):
                        for key in ("status", "row_count", "match_count", "error_type"):
                            if key in output_payload:
                                end_trace[key] = output_payload.get(key)
                    _tool_call_trace.append(end_trace)
                    if len(_tool_call_trace) > 120:
                        _tool_call_trace = _tool_call_trace[-120:]

                if isinstance(output_payload, dict):
                    hint_payload = output_payload.get("answer_contract_hint")
                    if isinstance(hint_payload, dict):
                        _tool_contract_hints.append(hint_payload)

                    payload_citations = output_payload.get("citations")
                    if isinstance(payload_citations, list) and not isinstance(hint_payload, dict):
                        raw_citations = [c for c in payload_citations if isinstance(c, dict)]
                        if raw_citations:
                            _tool_contract_hints.append(
                                {
                                    "source_mode": (
                                        "document"
                                        if tool_name
                                        in {"query_document_facts", "search_document_chunks"}
                                        else "structured"
                                    ),
                                    "exactness_state": "not_applicable",
                                    "confidence_decision": "medium",
                                    "trust_state": "answer_ready",
                                    "evidence_summary": f"{tool_name} returned {len(raw_citations)} citation(s).",
                                    "citations": raw_citations,
                                    "metadata": {"tool": tool_name},
                                }
                            )

                if (
                    tool_name == "execute_rcr_query"
                    and isinstance(output_payload, dict)
                    and not output_error
                ):
                    row_count = 0
                    try:
                        row_count = int(output_payload.get("row_count") or 0)
                    except Exception:
                        row_count = 0

                    raw_sql_input = _last_tool_inputs.get("execute_rcr_query")
                    if isinstance(raw_sql_input, dict):
                        sql_text = str(raw_sql_input.get("sql") or "").strip()
                    else:
                        sql_text = str(raw_sql_input or "").strip()
                    query_hash = (
                        hashlib.sha1(sql_text.encode("utf-8")).hexdigest()[:12]
                        if sql_text
                        else str(uuid4())
                    )
                    sql_citation: dict[str, Any] = {
                        "citation_type": "sql",
                        "reference_id": f"sql-{query_hash}",
                        "label": "Structured query result",
                        "metadata": {
                            "tool": "execute_rcr_query",
                            "row_count": row_count,
                            "query_hash": query_hash,
                        },
                    }
                    if output_payload.get("autocorrected_from") and output_payload.get(
                        "autocorrected_to"
                    ):
                        sql_citation["metadata"].update(
                            {
                                "autocorrected_from": output_payload.get("autocorrected_from"),
                                "autocorrected_to": output_payload.get("autocorrected_to"),
                            }
                        )

                    _tool_contract_hints.append(
                        {
                            "source_mode": "structured",
                            "exactness_state": "not_applicable",
                            "confidence_decision": "medium",
                            "trust_state": "answer_ready",
                            "evidence_summary": f"Structured query returned {row_count} row(s).",
                            "citations": [sql_citation],
                            "metadata": {"tool": "execute_rcr_query", "row_count": row_count},
                        }
                    )

                if tool_name == "query_cortex_agent" and isinstance(output_payload, dict):
                    if _is_tool_payload_success(output_payload):
                        is_non_answer = bool(output_payload.get("is_non_answer"))
                        _tool_contract_hints.append(
                            {
                                "source_mode": "structured",
                                "exactness_state": "not_applicable",
                                "confidence_decision": "low" if is_non_answer else "medium",
                                "trust_state": "insufficient_evidence" if is_non_answer else "answer_ready",
                                "evidence_summary": (
                                    "The published AI agent could not answer from its semantic model. "
                                    "A direct SQL fallback is needed."
                                    if is_non_answer
                                    else "Structured answer path completed via the published AI agent."
                                ),
                                "citations": [],
                                "metadata": {"tool": "query_cortex_agent", "is_non_answer": is_non_answer},
                            }
                        )
                    elif str(output_payload.get("error_type") or "").lower() == "auth":
                        _tool_contract_hints.append(
                            {
                                "source_mode": "structured",
                                "exactness_state": "not_applicable",
                                "confidence_decision": "abstain",
                                "trust_state": "blocked_access",
                                "evidence_summary": "The published AI agent could not be reached due to access or session constraints.",
                                "citations": [],
                                "recovery_actions": [
                                    {
                                        "action": "refresh_access_session",
                                        "description": "Retry with an active Snowflake session and role access to the published AI agent.",
                                        "metadata": {},
                                    }
                                ],
                                "metadata": {"tool": "query_cortex_agent"},
                            }
                        )

                # Track save_data_description for safety net
                if tool_name == "save_data_description":
                    _dd_tool_called = True
                    logger.info("save_data_description completed for session %s", session_id)

                # Track build_erd_from_description for safety net
                if tool_name == "build_erd_from_description":
                    _erd_build_called = True
                    logger.info("build_erd_from_description completed for session %s", session_id)

                # Track save_brd completion for safety net
                if tool_name == "save_brd":
                    _brd_tool_called = True
                    logger.info("save_brd completed for session %s", session_id)
                    # Score BRD quality
                    asyncio.create_task(score_brd_quality(_trace_id, data_product_id))
                    if not output_error:
                        await _persist_artifact_context_snapshot(
                            data_product_id=data_product_id,
                            artifact_type="brd",
                            created_by="ekai-agent",
                            phase=_current_phase,
                            document_context_contract=document_context_contract,
                            snapshot_extra={"tool": "save_brd"},
                        )

                # Track register_gold_layer for modeling phase + emit lineage artifact
                if tool_name == "register_gold_layer":
                    _gold_layer_registered = True
                    logger.info("register_gold_layer completed for session %s", session_id)
                    # Lineage is written to Neo4j inside register_gold_layer —
                    # emit the lineage artifact event so frontend shows it
                    await queue.put(
                        {
                            "type": "artifact",
                            "data": {
                                "artifact_id": str(uuid4()),
                                "artifact_type": "lineage",
                            },
                        }
                    )
                    logger.info("Emitted lineage artifact event for session %s", session_id)
                    if not output_error:
                        await _persist_artifact_context_snapshot(
                            data_product_id=data_product_id,
                            artifact_type="lineage",
                            created_by="ekai-agent",
                            phase=_current_phase,
                            document_context_contract=document_context_contract,
                            snapshot_extra={"tool": "register_gold_layer"},
                        )

                # Emit artifact events for modeling save tools
                _MODELING_TOOL_ARTIFACT_MAP = {
                    "save_data_catalog": "data_catalog",
                    "save_business_glossary": "business_glossary",
                    "save_metrics_definitions": "metrics",
                    "save_validation_rules": "validation_rules",
                    "save_openlineage_artifact": "lineage",
                }
                if tool_name in _MODELING_TOOL_ARTIFACT_MAP:
                    await queue.put(
                        {
                            "type": "artifact",
                            "data": {
                                "artifact_id": str(uuid4()),
                                "artifact_type": _MODELING_TOOL_ARTIFACT_MAP[tool_name],
                            },
                        }
                    )
                    logger.info(
                        "Emitted %s artifact event for session %s",
                        _MODELING_TOOL_ARTIFACT_MAP[tool_name],
                        session_id,
                    )

                # Track save_semantic_view for generation safety net
                if tool_name == "save_semantic_view":
                    _yaml_tool_called = True
                    logger.info("save_semantic_view completed for session %s", session_id)
                    if not output_error:
                        await _persist_artifact_context_snapshot(
                            data_product_id=data_product_id,
                            artifact_type="yaml",
                            created_by="ekai-agent",
                            phase=_current_phase,
                            document_context_contract=document_context_contract,
                            snapshot_extra={"tool": "save_semantic_view"},
                        )

                # Mark when a subagent completes and close the task gate
                if tool_name == "task":
                    _inside_task = False
                    _subagent_completed = True
                    logger.info("Subagent completed for session %s", session_id)

                # Artifact event already emitted from on_tool_start (more reliable).
                # Log the output for debugging but don't emit a second artifact event.
                if tool_name == "upload_artifact" and output_str:
                    logger.info(
                        "upload_artifact output (type=%s): %s",
                        type(output).__name__,
                        output_str[:200],
                    )

                if tool_name in _PUBLISH_DEPLOYMENT_TOOLS:
                    if isinstance(output_payload, dict) and _is_tool_payload_success(
                        output_payload
                    ):
                        if tool_name == "create_semantic_view":
                            await _persist_artifact_context_snapshot(
                                data_product_id=data_product_id,
                                artifact_type="semantic_view",
                                created_by="ekai-agent",
                                phase="publishing",
                                document_context_contract=document_context_contract,
                                snapshot_extra={"tool": "create_semantic_view"},
                            )
                        if tool_name == "create_cortex_agent":
                            _publish_completed = True
                            # Capture the agent FQN from tool output
                            if isinstance(output_payload, dict):
                                _published_agent_fqn = output_payload.get("agent_fqn") or None
                            logger.info(
                                "Publish completed for session %s (create_cortex_agent succeeded, fqn=%s)",
                                session_id,
                                _published_agent_fqn,
                            )
                            await _persist_artifact_context_snapshot(
                                data_product_id=data_product_id,
                                artifact_type="published_agent",
                                created_by="ekai-agent",
                                phase="publishing",
                                document_context_contract=document_context_contract,
                                snapshot_extra={"tool": "create_cortex_agent"},
                            )
                        _publish_tool_error_name = None
                        _publish_tool_error_reason = None
                        _abort_stream_due_publish_error = False

                    if output_error:
                        if _is_non_fatal_publish_tool_error(
                            tool_name=tool_name,
                            error_text=output_error,
                            publish_completed=_publish_completed,
                        ):
                            logger.info(
                                "Non-fatal publish tool error for session %s: tool=%s error=%s",
                                session_id,
                                tool_name,
                                output_error[:200],
                            )
                        else:
                            _publish_tool_error_name = tool_name
                            _publish_tool_error_reason = output_error
                            if _current_phase != "publishing":
                                old_phase = _current_phase
                                _current_phase = "publishing"
                                _pipeline_timer.phase_started("publishing")
                                await queue.put(
                                    {
                                        "type": "phase_change",
                                        "data": {"from": old_phase, "to": "publishing"},
                                    }
                                )
                                await _persist_phase(data_product_id, "publishing")
                            if not _failure_plan_message:
                                _failure_plan_message = _compose_failure_recovery_plan(
                                    phase="publishing",
                                    reason=output_error,
                                    timed_out=False,
                                    last_tool=tool_name,
                                )
                            _abort_stream_due_publish_error = True
                            logger.warning(
                                "Fatal publishing tool failure for session %s: tool=%s error=%s",
                                session_id,
                                tool_name,
                                output_error[:300],
                            )

                await queue.put(
                    {
                        "type": "tool_result",
                        "data": {
                            "tool": tool_name,
                            "output": output_str,
                        },
                    }
                )

                if _abort_stream_due_publish_error:
                    logger.warning(
                        "Stopping agent stream early after publishing failure (session=%s tool=%s)",
                        session_id,
                        _publish_tool_error_name or tool_name,
                    )
                    break

            elif kind == "on_chain_end" and event.get("name") == "ekaix-orchestrator":
                # Final orchestrator response — suppress entirely.
                # All user-facing content comes from subagent runs (inside task tool).
                pass

    except (asyncio.TimeoutError, TimeoutError) as e:
        logger.warning(
            "Agent timed out for session %s after streaming %d events, %d tokens. "
            "Large discovery summary may have caused the LLM to hang.",
            session_id,
            _stream_event_count,
            _stream_token_count,
        )
        _failure_plan_message = _compose_failure_recovery_plan(
            phase=_current_phase,
            reason=str(e),
            timed_out=True,
            last_tool=_last_tool_name,
        )

    except ValueError as e:
        # LangChain raises ValueError("No generations found in stream") when an LLM
        # produces an empty response (e.g. orchestrator after subagent did all the work).
        # This is benign — the subagent already delivered content.
        if "no generations" in str(e).lower():
            if is_discovery and _stream_token_count == 0:
                logger.warning(
                    "Agent %s: no generations during discovery (summary_len=%d) — fallback will fire in finally",
                    session_id,
                    len(actual_message),
                )
            elif _stream_token_count == 0 and not _subagent_completed and not _assistant_texts:
                logger.warning(
                    "Agent %s: no generations and no visible output; emitting recovery plan",
                    session_id,
                )
                _failure_plan_message = _compose_failure_recovery_plan(
                    phase=_current_phase,
                    reason=str(e),
                    timed_out=False,
                    last_tool=_last_tool_name,
                )
            else:
                logger.info(
                    "Agent %s produced empty response (expected after subagent delegation)",
                    session_id,
                )
        else:
            logger.exception("Agent execution failed for session %s: %s", session_id, e)
            _failure_plan_message = _compose_failure_recovery_plan(
                phase=_current_phase,
                reason=str(e),
                timed_out=False,
                last_tool=_last_tool_name,
            )
    except Exception as e:
        # LiteLLM Router handles retries and fallback automatically when enabled.
        # Just log and emit the error to the user.
        logger.exception("Agent execution failed for session %s: %s", session_id, e)
        _failure_plan_message = _compose_failure_recovery_plan(
            phase=_current_phase,
            reason=str(e),
            timed_out=False,
            last_tool=_last_tool_name,
        )

    finally:
        # Flush any remaining dedup buffer from the last run
        if _run_token_buffer and not _run_suppressed:
            current_assistant_content = "".join(_run_token_buffer)
            _run_token_buffer = []
        current_assistant_content = _sanitize_assistant_text(current_assistant_content)

        # If orchestrator produced a direct answer (no task delegation), surface it.
        if (
            not is_discovery
            and _stream_token_count == 0
            and not current_assistant_content.strip()
            and _last_model_end_output_text
            and not _failure_plan_message
        ):
            recovered_text = _sanitize_assistant_text(_last_model_end_output_text)
            if recovered_text and not _is_internal_reasoning_leak(recovered_text):
                current_assistant_content = recovered_text
                await queue.put(
                    {
                        "type": "token",
                        "data": {"content": recovered_text},
                    }
                )
                await queue.put(
                    {
                        "type": "message_done",
                        "data": {"content": recovered_text},
                    }
                )
                _stream_token_count += 1
                logger.warning(
                    "Recovered direct model answer for session %s (no task delegation, %d chars)",
                    session_id,
                    len(recovered_text),
                )

        # Diagnostic: log stream statistics — full pipeline for root-cause analysis
        logger.info(
            "Agent stream completed for session %s: events=%d, llm_calls=%d, llm_completions=%d, "
            "raw_chunks=%d, gated_out=%d, dedup_suppressed=%d, firewall_blocks=%d, tokens_emitted=%d, "
            "task_calls=%d, assistant_texts=%d",
            session_id,
            _stream_event_count,
            _stream_llm_calls,
            _stream_llm_completions,
            _stream_raw_chunks,
            _stream_gated_out,
            _stream_dedup_suppressed,
            _stream_firewall_blocks,
            _stream_token_count,
            _stream_task_calls,
            len(_assistant_texts) + (1 if current_assistant_content.strip() else 0),
        )

        # --- Zero-output fallback for discovery ---
        # If the LLM produced zero visible tokens during discovery, the user sees nothing.
        # Send a fallback message so the user can still interact.
        if (
            is_discovery
            and _stream_token_count == 0
            and not current_assistant_content.strip()
            and not _failure_plan_message
        ):
            logger.warning(
                "ZERO-OUTPUT FALLBACK: Discovery agent produced no tokens for session %s "
                "(events=%d, summary_len=%d). Sending fallback message.",
                session_id,
                _stream_event_count,
                len(actual_message),
            )
            fallback_msg = (
                "I've analyzed your data and completed the initial profiling. "
                "I found some interesting patterns across the tables. "
                "Could you tell me more about what you're looking to accomplish with this data? "
                "That will help me tailor my analysis to your specific needs."
            )
            current_assistant_content = _sanitize_assistant_text(fallback_msg)
            for token_chunk in [fallback_msg]:
                await queue.put(
                    {
                        "type": "token",
                        "data": {"content": _sanitize_assistant_text(token_chunk)},
                    }
                )
            await queue.put(
                {
                    "type": "message_done",
                    "data": {"content": _sanitize_assistant_text(fallback_msg)},
                }
            )

        if not _failure_plan_message and _publish_tool_error_reason and not _publish_completed:
            _failure_plan_message = _compose_failure_recovery_plan(
                phase="publishing",
                reason=_publish_tool_error_reason,
                timed_out=False,
                last_tool=_publish_tool_error_name or _last_tool_name,
            )

        status_contract_payload = _build_answer_contract_payload(
            phase=_current_phase,
            assistant_text=current_assistant_content
            or (_assistant_texts[-1] if _assistant_texts else ""),
            failure_message=_failure_plan_message,
            last_tool=_last_tool_name,
            tool_contract_hints=_tool_contract_hints,
            query_route_plan=_query_route_plan,
        )
        _trust_contract_enabled = True
        try:
            from config import get_effective_settings as _get_effective_settings

            _trust_contract_enabled = _get_effective_settings().feature_trust_ux_contract
        except Exception:
            _trust_contract_enabled = True
        if _query_route_plan:
            metadata = status_contract_payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["route_plan"] = _query_route_plan
            metadata["tool_trace_count"] = len(_tool_call_trace)
            status_contract_payload["metadata"] = metadata

        # ── System-level document evidence guarantee ─────────────────────
        if (
            isinstance(_query_route_plan, dict)
            and "document_chunks" in (_query_route_plan.get("lanes") or [])
            and workflow_snapshot.get("has_documents")
        ):
            _got_doc_evidence = False
            for trace in _tool_call_trace:
                if trace.get("tool") == "query_cortex_agent":
                    try:
                        _trace_result = json.loads(trace.get("result", "{}"))
                        if _trace_result.get("has_doc_search") or _trace_result.get("citations"):
                            _got_doc_evidence = True
                            break
                    except Exception:
                        pass

            if not _got_doc_evidence:
                try:
                    from tools.snowflake_tools import _search_preview
                    from tools.postgres_tools import get_data_product_name
                    dp_name = get_data_product_name()
                    if dp_name:
                        fallback_chunks = _search_preview(dp_name, message, limit=10)
                        if fallback_chunks:
                            await queue.put({
                                "type": "document_evidence",
                                "data": {
                                    "source": "system_search_preview_fallback",
                                    "chunks": fallback_chunks,
                                    "message": f"Found {len(fallback_chunks)} relevant document excerpts.",
                                },
                            })
                            logger.info(
                                "System SEARCH_PREVIEW fallback: found %d chunks for session=%s",
                                len(fallback_chunks), session_id,
                            )
                except Exception as e:
                    logger.warning("System doc search fallback failed: %s", e)

        if _failure_plan_message:
            await queue.put(
                {
                    "type": "status",
                    "data": {
                        "message": _failure_plan_message,
                        **(
                            {"answer_contract": status_contract_payload}
                            if _trust_contract_enabled
                            else {}
                        ),
                    },
                }
            )

        # Non-discovery fallback when supervisor firewall blocked leaked internals.
        if (
            not is_discovery
            and _stream_token_count == 0
            and not current_assistant_content.strip()
            and _stream_firewall_blocks > 0
            and not _failure_plan_message
        ):
            fallback_msg = (
                "I completed the step, but filtered internal execution details from the response. "
                "Please review the latest artifacts and tell me to continue."
            )
            current_assistant_content = fallback_msg
            await queue.put(
                {
                    "type": "token",
                    "data": {"content": fallback_msg},
                }
            )
            await queue.put(
                {
                    "type": "message_done",
                    "data": {"content": _sanitize_assistant_text(fallback_msg)},
                }
            )

        # Add final assistant content to local buffer for safety net
        if current_assistant_content.strip():
            safe_final = _sanitize_assistant_text(current_assistant_content)
            if safe_final:
                _assistant_texts.append(safe_final)

        # Emit a final trust contract for the UI even when no explicit failure occurred.
        if not _failure_plan_message and _trust_contract_enabled:
            await queue.put(
                {
                    "type": "status",
                    "data": {
                        "message": "",
                        "answer_contract": status_contract_payload,
                    },
                }
            )

        # --- Safety net: save Data Description if discovery agent produced text but didn't call save_data_description ---
        if _discovery_conversation_ran and not _dd_tool_called:
            dd_content = ""
            for text in _assistant_texts:
                if len(text) > len(dd_content):
                    dd_content = text
            _DD_MARKERS = (
                "[1] System Architecture",
                "[2] Business Context",
                "---BEGIN DATA DESCRIPTION---",
                "[6] Data Map",
            )
            has_dd_markers = any(marker in dd_content for marker in _DD_MARKERS)
            if len(dd_content) > 1000 and has_dd_markers:
                logger.warning(
                    "Safety net: discovery agent did not call save_data_description for session %s — saving programmatically",
                    session_id,
                )
                score_safety_net(_trace_id, "data_description_save")
                try:
                    from tools.postgres_tools import _get_pool
                    from services import postgres as _pg_svc
                    from uuid import uuid4 as _uuid4

                    pool = await _get_pool()
                    dd_id = str(_uuid4())
                    clean_json = json.dumps({"document": dd_content})
                    sql = """
                    INSERT INTO data_descriptions (id, data_product_id, description_json, created_by)
                    VALUES ($1::uuid, $2::uuid, $3::jsonb, $4)
                    """
                    await _pg_svc.execute(pool, sql, dd_id, data_product_id, clean_json, "ai-agent")
                    logger.info("Safety net: Data Description saved (dd_id=%s)", dd_id)
                    _dd_tool_called = True

                    # Upload artifact
                    from tools.minio_tools import upload_artifact_programmatic

                    await upload_artifact_programmatic(
                        data_product_id=data_product_id,
                        artifact_type="data_description",
                        filename="data-description.json",
                        content=dd_content,
                    )
                    await queue.put(
                        {
                            "type": "artifact",
                            "data": {
                                "artifact_id": dd_id,
                                "artifact_type": "data_description",
                            },
                        }
                    )

                    # Trigger ERD build if not already done
                    if not _erd_build_called:
                        try:
                            from services.discovery_pipeline import run_erd_pipeline

                            erd_result = await run_erd_pipeline(
                                data_product_id, {"document": dd_content}
                            )
                            erd_artifact_id = erd_result.get("erd_artifact_id")
                            if erd_artifact_id:
                                await queue.put(
                                    {
                                        "type": "artifact",
                                        "data": {
                                            "artifact_id": erd_artifact_id,
                                            "artifact_type": "erd",
                                        },
                                    }
                                )
                            logger.info("Safety net: ERD built from Data Description")
                        except Exception as erd_err:
                            logger.error("Safety net: failed to build ERD: %s", erd_err)

                except Exception as e:
                    logger.error("Safety net: failed to save Data Description: %s", e)
            else:
                logger.info(
                    "Safety net: skipped — no Data Description content detected (longest msg: %d chars, markers: %s)",
                    len(dd_content),
                    has_dd_markers,
                )

        # BRD and YAML safety nets removed — model-builder has full context and
        # verification tools (verify_brd_completeness, verify_yaml_against_brd)
        # to self-correct. Langfuse scoring tracks safety_net_activations (should be 0).

        # --- Langfuse: score YAML quality and pipeline duration ---
        if _generation_phase_ran:
            score_yaml_quality(
                _trace_id, _yaml_passed_first, _yaml_retry_count, _verification_issues
            )
        _pipeline_timer.finish()

        # Update data product state with session_id so frontend knows which thread to recover.
        # Chat history is now persisted automatically by LangGraph's PostgreSQL checkpointer.
        try:
            from services.postgres import get_pool, execute
            from config import get_settings

            settings = get_settings()
            pool = await get_pool(settings.database_url)
            await execute(
                pool,
                """UPDATE data_products
                   SET state = jsonb_set(state, '{session_id}', $1::jsonb)
                   WHERE id = $2::uuid""",
                f'"{session_id}"',
                data_product_id,
            )
            logger.info("Updated data product %s with session_id %s", data_product_id, session_id)
        except Exception as e:
            logger.warning("Failed to update data product session_id: %s", e)

        # Transition to explorer phase when we're in publishing and no hard
        # failure occurred. Auto-repair runs unconditionally — it checks for
        # agent/service existence internally, so it handles both "LLM created
        # everything" and "LLM skipped tools entirely" cases.
        if _current_phase == "publishing" and not _failure_plan_message:
            # ── Post-publish auto-repair ──────────────────────────────────
            # Deterministic safety net: ensure Cortex Search Service and
            # Cortex Agent exist even if the LLM skipped a tool call.
            # NOT gated on _publish_completed — that's the whole point.
            try:
                await _post_publish_auto_repair(
                    data_product_id=data_product_id,
                    published_agent_fqn=_published_agent_fqn,
                )
            except Exception as _repair_err:
                logger.warning(
                    "Post-publish auto-repair failed for %s: %s",
                    data_product_id,
                    _repair_err,
                )
            # ──────────────────────────────────────────────────────────────

            await queue.put(
                {
                    "type": "phase_change",
                    "data": {"from": _current_phase, "to": "explorer"},
                }
            )
            logger.info(
                "Phase change: %s → explorer (session %s, stream end, publish_completed=%s)",
                _current_phase, session_id, _publish_completed,
            )
            await _persist_phase(data_product_id, "explorer")

        # Persist answer evidence packet for auditability and trust UX playback.
        try:
            from config import get_effective_settings as _get_effective_settings
            from services.postgres import execute as _pg_execute
            from services.postgres import get_pool as _pg_get_pool
            from services.postgres import query as _pg_query

            _pool = await _pg_get_pool(_settings.database_url)
            _effective_settings = _get_effective_settings()
            _model_signature = _resolve_llm_signature_for_audit(_effective_settings)
            _exists_rows = await _pg_query(
                _pool,
                "SELECT to_regclass('public.qa_evidence') AS rel",
            )
            _qa_evidence_rel = (
                (_exists_rows[0].get("rel") if _exists_rows else None)
                if isinstance(_exists_rows, list)
                else None
            )
            _ops_alert_exists_rows = await _pg_query(
                _pool,
                "SELECT to_regclass('public.ops_alert_events') AS rel",
            )
            _ops_alert_rel = (
                (_ops_alert_exists_rows[0].get("rel") if _ops_alert_exists_rows else None)
                if isinstance(_ops_alert_exists_rows, list)
                else None
            )
            if not _qa_evidence_rel:
                logger.info(
                    "Skipping qa_evidence persistence for session %s: table missing",
                    session_id,
                )
            else:
                _citations = status_contract_payload.get("citations", [])
                _sql_refs = [
                    c for c in _citations if isinstance(c, dict) and c.get("citation_type") == "sql"
                ]
                _fact_refs = [
                    c
                    for c in _citations
                    if isinstance(c, dict) and c.get("citation_type") == "document_fact"
                ]
                _chunk_refs = [
                    c
                    for c in _citations
                    if isinstance(c, dict) and c.get("citation_type") == "document_chunk"
                ]
                _recovery_actions = status_contract_payload.get("recovery_actions", [])
                _trust_state = str(status_contract_payload.get("trust_state") or "answer_ready")
                _tool_calls_payload: list[dict[str, Any]] = []
                _tool_calls_payload.append(
                    {
                        "type": "model",
                        "provider": _model_signature["provider"],
                        "model": _model_signature["model"],
                        "model_hash": _model_signature["model_hash"],
                    }
                )
                if _query_route_plan:
                    _tool_calls_payload.append({"type": "route_plan", "plan": _query_route_plan})
                _tool_calls_payload.extend(_tool_call_trace[-80:])

                # Citation check only applies to explorer phase — pipeline phases
                # (discovery/requirements/generation/validation/publishing) use artifacts,
                # not live document queries, so citation_missing is a false positive there.
                _citation_check_phases = {"explorer", ""}
                if (
                    _trust_state in {"answer_ready", "answer_with_warnings"}
                    and not (_sql_refs or _fact_refs or _chunk_refs)
                    and _current_phase in _citation_check_phases
                ):
                    logger.warning(
                        "OPS_ALERT[citation_missing] session=%s source_mode=%s trust_state=%s phase=%s",
                        session_id,
                        status_contract_payload.get("source_mode"),
                        _trust_state,
                        _current_phase,
                    )
                    if _ops_alert_rel:
                        try:
                            await _pg_execute(
                                _pool,
                                """INSERT INTO ops_alert_events
                                   (id, data_product_id, signal, severity, message, source_service,
                                    source_route, session_id, metadata, created_by)
                                   VALUES
                                   ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)""",
                                str(uuid4()),
                                data_product_id,
                                "citation_missing_answers",
                                "high",
                                "Answer marked ready/review without citations",
                                "ai-service",
                                "/agent/stream",
                                session_id,
                                json.dumps(
                                    {
                                        "source_mode": str(
                                            status_contract_payload.get("source_mode") or "unknown"
                                        ),
                                        "trust_state": _trust_state,
                                    }
                                ),
                                "ekaix-agent",
                            )
                        except Exception as alert_err:
                            logger.debug(
                                "Failed to persist ops_alert_events citation_missing for session %s: %s",
                                session_id,
                                alert_err,
                            )

                await _pg_execute(
                    _pool,
                    """INSERT INTO qa_evidence
                       (id, data_product_id, query_id, source_mode, confidence, exactness_state,
                        tool_calls, sql_refs, fact_refs, chunk_refs, conflicts, recovery_plan,
                        final_decision, created_by)
                       VALUES
                       ($1::uuid, $2::uuid, $3, $4, $5, $6,
                        $7::jsonb, $8::jsonb, $9::jsonb, $10::jsonb, $11::jsonb, $12::jsonb,
                        $13, $14)""",
                    str(uuid4()),
                    data_product_id,
                    f"{session_id}:{uuid4()}",
                    str(status_contract_payload.get("source_mode") or "unknown"),
                    str(status_contract_payload.get("confidence_decision") or "medium"),
                    str(status_contract_payload.get("exactness_state") or "not_applicable"),
                    json.dumps(_tool_calls_payload),
                    json.dumps(_sql_refs),
                    json.dumps(_fact_refs),
                    json.dumps(_chunk_refs),
                    json.dumps(status_contract_payload.get("conflict_notes", [])),
                    json.dumps({"actions": _recovery_actions}),
                    _trust_state[:32],
                    "ekaix-agent",
                )
        except Exception as e:
            logger.warning("Failed to persist qa_evidence for session %s: %s", session_id, e)

        # Signal stream end
        await queue.put(
            {
                "type": "done",
                "data": {"message": "Agent processing complete"},
            }
        )
        await queue.put(None)  # Sentinel to close the generator


async def _event_generator(session_id: str) -> AsyncGenerator[str, None]:
    """Generate SSE-formatted events for a given agent session."""
    queue = _active_streams.get(session_id)

    if queue is None:
        # No active stream — send a waiting message then done
        event = AgentStreamEvent(
            type="done",
            data={"message": "No active processing for this session. Send a message first."},
            timestamp=datetime.now(tz=timezone.utc),
        )
        yield f"event: {event.type}\ndata: {json.dumps(event.model_dump(mode='json'))}\n\n"
        return

    # Send keepalive comment
    yield ": keepalive\n\n"

    try:
        while True:
            try:
                # Wait for events with timeout for keepalive
                item = await asyncio.wait_for(queue.get(), timeout=_settings.agent_stream_timeout)
            except asyncio.TimeoutError:
                # Send keepalive ping
                yield ": ping\n\n"
                continue

            if item is None:
                # Sentinel: stream is done
                break

            event = AgentStreamEvent(
                type=item["type"],
                data=item["data"],
                timestamp=datetime.now(tz=timezone.utc),
            )
            yield f"event: {event.type}\ndata: {json.dumps(event.model_dump(mode='json'))}\n\n"

            if item["type"] == "done":
                break

    finally:
        # Clean up the stream
        _active_streams.pop(session_id, None)


@router.get("/stream/{session_id}")
async def stream_response(session_id: str) -> StreamingResponse:
    """Subscribe to the agent response stream via Server-Sent Events."""
    return StreamingResponse(
        _event_generator(session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/interrupt/{session_id}")
async def interrupt_agent(session_id: str) -> dict[str, str]:
    """Interrupt a running agent session."""
    logger.info("Interrupt requested for session %s", session_id)

    # Push an error event and close the stream
    queue = _active_streams.get(session_id)
    if queue:
        await queue.put(
            {
                "type": "error",
                "data": {"message": "Interrupted by user"},
            }
        )
        await queue.put(
            {
                "type": "done",
                "data": {"message": "Session interrupted by user"},
            }
        )
        await queue.put(None)

    return {"status": "interrupted", "session_id": session_id}


@router.post("/approve")
async def approve_action(request: ApproveRequest) -> dict[str, str]:
    """Approve or reject a pending agent action (e.g., publishing)."""
    logger.info(
        "Approval for session %s: approved=%s",
        request.session_id,
        request.approved,
    )

    # Resume the agent with the approval decision
    queue = _active_streams.get(request.session_id)
    if queue:
        status = "approved" if request.approved else "rejected"
        await queue.put(
            {
                "type": "approval_response",
                "data": {"approved": request.approved, "status": status},
            }
        )

    status = "approved" if request.approved else "rejected"
    return {"status": status, "session_id": request.session_id}


@router.post("/retry")
async def retry_message(request: RetryRequest) -> InvokeResponse:
    """Retry or edit a message using LangGraph checkpoint time-travel.

    Finds the appropriate checkpoint, forks from it, and re-runs the agent.
    For edits, replaces the user message content before re-running.
    """
    session_id = request.session_id
    message_id = str(uuid4())
    logger.info(
        "Retry requested for session %s (target_message=%s, edited=%s)",
        session_id,
        request.message_id,
        request.edited_content is not None,
    )

    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    _active_streams[session_id] = queue

    asyncio.create_task(
        _run_agent_from_checkpoint(
            session_id=session_id,
            data_product_id=str(request.data_product_id),
            target_message_id=request.message_id,
            edited_content=request.edited_content,
            original_content=request.original_content,
            queue=queue,
        )
    )

    return InvokeResponse(
        session_id=session_id,
        message_id=message_id,
        status="processing",
    )


async def _run_agent_from_checkpoint(
    session_id: str,
    data_product_id: str,
    target_message_id: str | None,
    edited_content: str | None,
    original_content: str | None,
    queue: asyncio.Queue[dict | None],
) -> None:
    """Run the agent from a specific point for retry/edit.

    Uses LangGraph's aupdate_state + RemoveMessage to trim messages
    after the target point, then re-invokes the agent normally.
    This avoids checkpoint forking issues with Gemini's empty-parts restriction.
    """
    try:
        from agents.orchestrator import get_orchestrator
        from langchain_core.messages import RemoveMessage
        from tools.snowflake_tools import set_data_isolation_context
        from tools.postgres_tools import set_data_product_context

        # Set data_product_id contextvar for tools
        set_data_product_context(data_product_id)

        # Set up data isolation
        dp_info = await _get_data_product_info(data_product_id)
        if dp_info:
            set_data_isolation_context(database=dp_info["database"], tables=dp_info["tables"])
        else:
            set_data_isolation_context(database=None, tables=None)

        agent = await get_orchestrator()
        config = {"configurable": {"thread_id": session_id}}

        def _normalize_content(content: str | list) -> str:
            """Normalize message content (str or list of blocks) to a plain string."""
            if isinstance(content, list):
                return "".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in content
                )
            return content

        # Get current state
        current_state = await agent.aget_state(config)
        all_messages = (
            current_state.values.get("messages", [])
            if current_state and current_state.values
            else []
        )

        if not all_messages:
            await queue.put(
                {
                    "type": "error",
                    "data": {"message": "No messages found to retry."},
                }
            )
            await queue.put({"type": "done", "data": {"message": "Retry failed"}})
            await queue.put(None)
            return

        # Resolve the target message ID
        real_msg_id = target_message_id
        if target_message_id and target_message_id.startswith("recovered-"):
            parts = target_message_id.split("-")
            try:
                visible_idx = int(parts[1])
                visible_count = 0
                for msg in all_messages:
                    content_str = _normalize_content(msg.content) if hasattr(msg, "content") else ""
                    if "[INTERNAL CONTEXT" in content_str:
                        continue
                    if msg.type not in ("human", "ai"):
                        continue
                    if visible_count == visible_idx:
                        real_msg_id = msg.id
                        break
                    visible_count += 1
            except (ValueError, IndexError):
                pass

        # Find the target message in the state and determine what to replay
        replay_content: str | None = None
        cut_index: int | None = None  # Index AFTER which to remove messages

        if real_msg_id:
            for i, msg in enumerate(all_messages):
                if msg.id == real_msg_id:
                    if msg.type == "human":
                        # User message retry/edit: keep everything BEFORE this msg,
                        # remove this msg and everything after, then re-send
                        replay_content = edited_content or _normalize_content(msg.content)
                        cut_index = i  # Remove from index i onwards
                    else:
                        # Agent message retry: find preceding user message,
                        # remove from that user msg onwards
                        for j in range(i, -1, -1):
                            if all_messages[j].type == "human":
                                replay_content = _normalize_content(all_messages[j].content)
                                cut_index = j
                                break
                    break
        else:
            # No message_id: retry the last user message
            for i in range(len(all_messages) - 1, -1, -1):
                if all_messages[i].type == "human":
                    replay_content = _normalize_content(all_messages[i].content)
                    cut_index = i
                    break

        # Fallback: if ID-based match failed, try matching by content.
        # Frontend-generated UUIDs (crypto.randomUUID) don't match LangGraph's
        # internal message IDs, so we match by content as a fallback.
        if cut_index is None and original_content:
            search_content = original_content[:300]
            for i, msg in enumerate(all_messages):
                if msg.type == "human":
                    msg_text = _normalize_content(msg.content)
                    if msg_text[:300] == search_content:
                        replay_content = edited_content or msg_text
                        cut_index = i
                        logger.info("Retry: matched message by content at index %d", i)
                        break

        # Last resort: use the last human message
        if cut_index is None:
            for i in range(len(all_messages) - 1, -1, -1):
                if all_messages[i].type == "human":
                    replay_content = edited_content or _normalize_content(all_messages[i].content)
                    cut_index = i
                    logger.info("Retry: fell back to last human message at index %d", i)
                    break

        if replay_content is None or cut_index is None:
            await queue.put(
                {
                    "type": "error",
                    "data": {"message": "Could not find the message to retry."},
                }
            )
            await queue.put({"type": "done", "data": {"message": "Retry failed"}})
            await queue.put(None)
            return

        # Remove messages from cut_index onwards using RemoveMessage
        msgs_to_remove = all_messages[cut_index:]
        if msgs_to_remove:
            remove_ops = [RemoveMessage(id=m.id) for m in msgs_to_remove]
            await agent.aupdate_state(config, {"messages": remove_ops})
            logger.info("Retry: removed %d messages from index %d", len(msgs_to_remove), cut_index)

        # Fix empty-content AI messages — Gemini rejects messages with empty parts.
        # These occur when the orchestrator calls tools (e.g. task) with no text content.
        refreshed_state = await agent.aget_state(config)
        remaining_msgs = (
            refreshed_state.values.get("messages", [])
            if refreshed_state and refreshed_state.values
            else []
        )
        patches = []
        extra_removes = []
        for m in remaining_msgs:
            if m.type == "ai":
                c = m.content
                is_empty = not c or c == [] or c == "" or (isinstance(c, list) and len(c) == 0)
                if is_empty:
                    if getattr(m, "tool_calls", None):
                        # Has tool calls — patch content with placeholder
                        patches.append(
                            AIMessage(
                                content=".",
                                id=m.id,
                                tool_calls=m.tool_calls,
                            )
                        )
                    else:
                        # No content AND no tool calls — remove entirely
                        extra_removes.append(RemoveMessage(id=m.id))
        updates = patches + extra_removes
        if updates:
            await agent.aupdate_state(config, {"messages": updates})
            logger.info(
                "Retry: fixed %d patched + %d removed empty AI messages",
                len(patches),
                len(extra_removes),
            )

        logger.info("Retry: replaying %d chars for session %s", len(replay_content), session_id)

        # Now run the agent normally — the state has been trimmed
        await _run_agent(
            session_id=session_id,
            message=replay_content,
            data_product_id=data_product_id,
            queue=queue,
        )

    except Exception as e:
        logger.exception("Retry failed for session %s: %s", session_id, e)
        await queue.put(
            {
                "type": "error",
                "data": {"message": _sanitize_error_for_user(e)},
            }
        )
        await queue.put({"type": "done", "data": {"message": "Retry failed"}})
        await queue.put(None)


@router.get("/checkpoints/{session_id}")
async def list_checkpoints(session_id: str) -> dict:
    """List checkpoints for a session (for retry/edit message mapping)."""
    try:
        from agents.orchestrator import get_orchestrator

        agent = await get_orchestrator()
        config = {"configurable": {"thread_id": session_id}}
        checkpoints = []
        async for state in agent.aget_state_history(config, limit=50):
            messages = state.values.get("messages", [])
            last_msg_id = messages[-1].id if messages else None
            checkpoints.append(
                {
                    "checkpoint_id": state.config["configurable"].get("checkpoint_id"),
                    "message_count": len(messages),
                    "last_message_id": last_msg_id,
                    "created_at": state.created_at,
                    "next": list(state.next) if state.next else [],
                }
            )
        return {"session_id": session_id, "checkpoints": checkpoints}
    except Exception as e:
        logger.error("Failed to list checkpoints for session %s: %s", session_id, e)
        return {"session_id": session_id, "checkpoints": [], "error": str(e)}


@router.post("/rollback/{checkpoint_id}")
async def rollback_to_checkpoint(checkpoint_id: str, session_id: str = Query(...)) -> dict:
    """Rollback conversation to a specific checkpoint.

    Restores the agent state to the given checkpoint, trimming all
    messages that came after it.
    """
    try:
        from agents.orchestrator import get_orchestrator

        agent = await get_orchestrator()
        config = {"configurable": {"thread_id": session_id}}

        # Find the target checkpoint in history
        target_state = None
        async for state in agent.aget_state_history(config, limit=100):
            cp_id = state.config["configurable"].get("checkpoint_id")
            if cp_id == checkpoint_id:
                target_state = state
                break

        if target_state is None:
            raise HTTPException(
                status_code=404,
                detail=f"Checkpoint {checkpoint_id} not found for session {session_id}",
            )

        # Restore by updating state to the checkpoint's values
        target_config = target_state.config
        messages = target_state.values.get("messages", [])

        await agent.aupdate_state(
            config,
            values=target_state.values,
            as_node="__start__",
        )

        return {
            "status": "restored",
            "checkpoint_id": checkpoint_id,
            "session_id": session_id,
            "message_count": len(messages),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Rollback failed for session %s checkpoint %s: %s", session_id, checkpoint_id, e)
        raise HTTPException(status_code=500, detail=f"Rollback failed: {e}")


async def _infer_phase(session_id: str, all_messages: list) -> str:
    """Infer the current pipeline phase from tool calls AND database state.

    Strategy:
    1. Check tool calls in checkpoint messages (most reliable for current session)
    2. Fall back to querying the database for artifacts/semantic views
       (handles cases where checkpoints were truncated or rebuilt)

    Phase mapping (ordered by pipeline progress):
      create_cortex_agent / grant_agent_access → explorer (publishing done)
      update_validation_status                 → publishing (validation done)
      save_semantic_view                       → generation (YAML generated)
      register_gold_layer / save_data_catalog  → modeling (Gold layer done)
      save_brd                                 → requirements (BRD done)
      register_transformed_layer               → transformation (Silver done)
      otherwise                                → discovery
    """
    phase_rank = {
        "discovery": 0,
        "prepare": 1,
        "transformation": 1,
        "requirements": 2,
        "modeling": 3,
        "generation": 4,
        "publishing": 5,
        "explorer": 6,
    }
    tool_to_phase = {
        "save_data_description": "discovery",
        "build_erd_from_description": "discovery",
        "register_transformed_layer": "transformation",
        "save_brd": "requirements",
        "register_gold_layer": "modeling",
        "save_data_catalog": "modeling",
        "save_semantic_view": "generation",
        "update_validation_status": "publishing",
        "create_cortex_agent": "explorer",
        "grant_agent_access": "explorer",
    }

    best_phase = "discovery"
    best_rank = 0

    # 1. Check persisted current_phase in data_products.state (most reliable,
    #    updated by _persist_phase on every phase transition).
    dp_published = False
    try:
        from services import postgres as pg_service

        pool = pg_service._pool
        dp_rows = await pg_service.query(
            pool,
            "SELECT id, state->>'current_phase' AS persisted_phase, (state->>'published')::boolean AS published FROM data_products WHERE state->>'session_id' = $1 LIMIT 1",
            session_id,
        )
        if dp_rows:
            dp_id = str(dp_rows[0]["id"])
            dp_published = bool(dp_rows[0].get("published"))
            persisted = dp_rows[0].get("persisted_phase")
            if persisted and persisted in phase_rank:
                p_rank = phase_rank[persisted]
                if p_rank > best_rank:
                    best_phase = persisted
                    best_rank = p_rank
    except Exception as e:
        logger.warning("Phase inference persisted-phase lookup failed: %s", e)
        dp_id = None

    # 2. Scan checkpoint messages for tool calls
    for msg in all_messages:
        tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in tool_calls:
            name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
            mapped = tool_to_phase.get(name)
            if mapped and phase_rank.get(mapped, 0) > best_rank:
                best_phase = mapped
                best_rank = phase_rank[mapped]

    # 3. Cross-check with database artifacts unless we already know we're at
    #    explorer (covers truncated checkpoints, manual publishing, restarts).
    if best_rank < phase_rank["explorer"]:
        try:
            from services import postgres as pg_service

            pool = pg_service._pool

            if not dp_id:
                dp_rows = await pg_service.query(
                    pool,
                    "SELECT id FROM data_products WHERE state->>'session_id' = $1 LIMIT 1",
                    session_id,
                )
                dp_id = str(dp_rows[0]["id"]) if dp_rows else None

            if dp_id:
                # Check for semantic views (most definitive)
                sv_rows = await pg_service.query(
                    pool,
                    "SELECT validation_status FROM semantic_views WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
                    dp_id,
                )
                if sv_rows:
                    status = sv_rows[0].get("validation_status")
                    if status == "valid":
                        # Check audit log OR state.published flag (covers manual publishing)
                        if dp_published:
                            db_phase = "explorer"
                        else:
                            log_rows = await pg_service.query(
                                pool,
                                "SELECT 1 FROM audit_logs WHERE data_product_id = $1::uuid AND action_type = 'publish' LIMIT 1",
                                dp_id,
                            )
                            if log_rows:
                                db_phase = "explorer"
                            else:
                                db_phase = "publishing"
                    elif status == "invalid":
                        db_phase = "generation"
                    else:
                        db_phase = "generation"
                else:
                    # No semantic view — check for Gold layer (data_catalog table)
                    catalog_rows = await pg_service.query(
                        pool,
                        "SELECT 1 FROM data_catalog WHERE data_product_id = $1::uuid LIMIT 1",
                        dp_id,
                    )
                    if catalog_rows:
                        db_phase = "modeling"
                    else:
                        # No Gold layer — check for BRD
                        brd_rows = await pg_service.query(
                            pool,
                            "SELECT 1 FROM business_requirements WHERE data_product_id = $1::uuid LIMIT 1",
                            dp_id,
                        )
                        if brd_rows:
                            db_phase = "requirements"
                        else:
                            db_phase = None

                # Only promote, never demote
                if db_phase and phase_rank.get(db_phase, 0) > best_rank:
                    best_phase = db_phase
                    best_rank = phase_rank[db_phase]
        except Exception as e:
            logger.warning("Phase inference DB fallback failed: %s", e)

    return best_phase


def _parse_jsonish(value: Any, fallback: Any) -> Any:
    """Parse JSON-ish values from Postgres JSON/JSONB columns safely."""
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return fallback
    return fallback


def _normalize_history_citations(raw_value: Any, default_type: str) -> list[dict[str, Any]]:
    """Convert citation JSON payload into UI-ready citation objects."""
    parsed = _parse_jsonish(raw_value, [])
    if not isinstance(parsed, list):
        return []

    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(parsed):
        if not isinstance(item, dict):
            continue
        reference_id = str(item.get("reference_id") or item.get("id") or "").strip()
        if not reference_id:
            reference_id = f"{default_type}-{idx + 1}"
        citation_type = str(item.get("citation_type") or default_type).strip() or default_type

        entry: dict[str, Any] = {**item}
        entry["citation_type"] = citation_type
        entry["reference_id"] = reference_id
        normalized.append(entry)
    return normalized


def _build_history_answer_contract(row: dict[str, Any]) -> dict[str, Any]:
    """Build an answer contract object from qa_evidence rows for chat history replay."""
    citations: list[dict[str, Any]] = []
    citations.extend(_normalize_history_citations(row.get("sql_refs"), "sql"))
    citations.extend(_normalize_history_citations(row.get("fact_refs"), "document_fact"))
    citations.extend(_normalize_history_citations(row.get("chunk_refs"), "document_chunk"))

    conflicts = _parse_jsonish(row.get("conflicts"), [])
    if not isinstance(conflicts, list):
        conflicts = []

    recovery_plan = _parse_jsonish(row.get("recovery_plan"), {})
    if not isinstance(recovery_plan, dict):
        recovery_plan = {}
    raw_actions = recovery_plan.get("actions", [])
    recovery_actions = raw_actions if isinstance(raw_actions, list) else []

    created_at = row.get("created_at")
    created_at_iso = (
        created_at.isoformat()
        if hasattr(created_at, "isoformat")
        else (str(created_at) if created_at else None)
    )

    trust_state = str(row.get("final_decision") or "answer_ready").strip() or "answer_ready"
    source_mode = str(row.get("source_mode") or "unknown").strip() or "unknown"
    confidence = str(row.get("confidence") or "medium").strip() or "medium"
    exactness = str(row.get("exactness_state") or "not_applicable").strip() or "not_applicable"

    evidence_summary = "Evidence replayed from persisted audit trace."
    if trust_state.startswith("abstained"):
        evidence_summary = "The answer was abstained due to missing or conflicting evidence."
    elif trust_state.startswith("failed") or trust_state == "blocked_access":
        evidence_summary = "The answer required recovery actions based on execution status."

    return {
        "source_mode": source_mode,
        "exactness_state": exactness,
        "confidence_decision": confidence,
        "trust_state": trust_state,
        "evidence_summary": evidence_summary,
        "conflict_notes": [str(item) for item in conflicts if isinstance(item, str)],
        "citations": citations,
        "recovery_actions": [item for item in recovery_actions if isinstance(item, dict)],
        "metadata": {
            "query_id": str(row.get("query_id") or ""),
            "evidence_created_at": created_at_iso,
            "history_replay": True,
        },
    }


@router.get("/history/{session_id}")
async def get_history(session_id: str) -> dict:
    """Get conversation history from LangGraph's PostgreSQL checkpointer."""
    try:
        from agents.orchestrator import get_orchestrator

        agent = await get_orchestrator()
        config = {"configurable": {"thread_id": session_id}}
        state = await agent.aget_state(config)
        if state and state.values and "messages" in state.values:
            raw_messages = state.values["messages"]
            messages = []
            for msg in raw_messages:
                if not hasattr(msg, "content") or not hasattr(msg, "type"):
                    continue
                # Skip system messages
                if msg.type == "system":
                    continue
                content = msg.content
                # Normalize list/dict content to string
                if isinstance(content, list):
                    text_parts = [
                        block.get("text", "") if isinstance(block, dict) else str(block)
                        for block in content
                    ]
                    content = "".join(text_parts)
                elif isinstance(content, dict):
                    content = content.get("text", str(content))
                # Skip empty or placeholder messages (tool-call-only turns, retry patches)
                if not content or not content.strip() or content.strip() == ".":
                    continue
                # Strip internal context payloads from persisted history.
                # LangGraph stores the full orchestrator input; user-facing history must not.
                if "[INTERNAL CONTEXT" in content:
                    continue
                recovered_user = _extract_user_message_from_supervisor_contract(content)
                if recovered_user is not None:
                    content = recovered_user
                # Skip tool cancellation messages (internal Deep Agents noise)
                if "was cancelled" in content and "tool call" in content.lower():
                    continue
                # Map roles: human -> user, everything else (ai, tool) -> assistant
                # Deep Agents stores subagent responses as tool messages
                role = "user" if msg.type == "human" else "assistant"
                if role == "assistant":
                    content = _sanitize_assistant_text(content)
                    if not content:
                        continue
                messages.append({"role": role, "content": content, "id": msg.id})

            # Filter orchestrator internal monologue that leaked into checkpoint.
            # These messages were gated during streaming but still saved to state.
            def _is_orchestrator_garbage(m: dict) -> bool:
                if m["role"] != "assistant":
                    return False
                c = m["content"]
                # Tool/subagent references — never user-facing
                if _is_internal_reasoning_leak(c):
                    return True
                # Repeated "Wait" pattern from Gemini auto-chain failures
                if c.lower().count("wait") >= 3:
                    return True
                # Repetitive text — same sentence 3+ times (Gemini loop)
                sentences = [s.strip() for s in c.split("\n") if s.strip()]
                if len(sentences) >= 3:
                    from collections import Counter

                    counts = Counter(sentences)
                    if counts.most_common(1)[0][1] >= 3:
                        return True
                return False

            messages = [m for m in messages if not _is_orchestrator_garbage(m)]

            # Deduplicate messages with identical role+content (keep first occurrence).
            # Non-adjacent duplicates can occur from failed retry attempts that
            # appended duplicate HumanMessages to the checkpoint state.
            seen: set[str] = set()
            deduped: list[dict] = []
            for m in messages:
                fingerprint = f"{m['role']}:{m['content'][:500]}"
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                deduped.append(m)

            # Infer the pipeline phase from tool calls + database state
            phase = await _infer_phase(session_id, raw_messages)

            # Replay persisted trust contracts so history answers keep
            # source/confidence/exactness signals after page reload.
            replay_contracts: list[dict[str, Any]] = []
            try:
                from services.postgres import get_pool as _pg_get_pool
                from services.postgres import query as _pg_query

                _pool = await _pg_get_pool(_settings.database_url)
                evidence_rows = await _pg_query(
                    _pool,
                    """SELECT query_id, source_mode, confidence, exactness_state, final_decision,
                              sql_refs, fact_refs, chunk_refs, conflicts, recovery_plan, created_at
                       FROM qa_evidence
                       WHERE query_id LIKE $1
                       ORDER BY created_at ASC""",
                    f"{session_id}:%",
                )
                if isinstance(evidence_rows, list):
                    for row in evidence_rows:
                        candidate: dict[str, Any] | None = None
                        if isinstance(row, dict):
                            candidate = row
                        else:
                            try:
                                candidate = dict(row)
                            except Exception:
                                candidate = None
                        if candidate is not None:
                            replay_contracts.append(_build_history_answer_contract(candidate))
            except Exception as replay_err:
                logger.debug(
                    "History trust replay unavailable for session %s: %s",
                    session_id,
                    replay_err,
                )

            assistant_indexes = [
                idx for idx, message in enumerate(deduped) if message.get("role") == "assistant"
            ]
            contract_idx = len(replay_contracts) - 1
            for message_idx in reversed(assistant_indexes):
                if contract_idx < 0:
                    break
                deduped[message_idx]["answer_contract"] = replay_contracts[contract_idx]
                contract_idx -= 1

            # Ensure all assistant messages have a lightweight contract fallback.
            # This guarantees consistent trust badges in history replay UX.
            for message_idx in assistant_indexes:
                if "answer_contract" in deduped[message_idx]:
                    continue
                deduped[message_idx]["answer_contract"] = {
                    "source_mode": "unknown",
                    "exactness_state": "not_applicable",
                    "confidence_decision": "medium",
                    "trust_state": "answer_with_warnings",
                    "evidence_summary": "No persisted evidence envelope found for this historical answer.",
                    "conflict_notes": [],
                    "citations": [],
                    "recovery_actions": [],
                    "metadata": {"history_replay": True, "fallback_contract": True},
                }

            return {"session_id": session_id, "messages": deduped, "phase": phase}
    except Exception as e:
        logger.error("Failed to get history for session %s: %s", session_id, e)

    return {"session_id": session_id, "messages": [], "phase": "discovery"}
