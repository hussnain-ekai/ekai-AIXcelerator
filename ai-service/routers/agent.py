"""Agent conversation endpoints — message handling, SSE streaming, and control actions."""

import asyncio
import base64
import json
import logging
import re
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from config import get_settings
from models.schemas import (
    AgentStreamEvent,
    ApproveRequest,
    InvokeRequest,
    InvokeResponse,
    RetryRequest,
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
    return "Something went wrong while processing your request. Please try again or contact support."


def _extract_yaml_from_text(text: str) -> str | None:
    """Extract YAML content from LLM text output (safety net helper)."""
    # Try markdown yaml block
    match = re.search(r'```ya?ml\s*\n(.*?)\n\s*```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Try generic code block with YAML content
    match = re.search(r'```\s*\n(.*?)\n\s*```', text, re.DOTALL)
    if match:
        content = match.group(1).strip()
        if "tables:" in content and "base_table:" in content:
            return content
    # Try raw YAML (look for name: + tables: pattern)
    match = re.search(r'(name:\s+\S+.*?tables:.*)', text, re.DOTALL)
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
        _run_agent(session_id, request.message, str(request.data_product_id), queue,
                   file_contents=request.file_contents)
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
        """SELECT name, description, database_reference, schemas, tables
           FROM data_products WHERE id = $1""",
        data_product_id,
    )
    if not rows:
        return None

    r = rows[0]
    return {
        "name": r["name"],
        "description": r["description"] or "No description provided",
        "database": r["database_reference"],
        "schemas": r["schemas"] or [],
        "tables": r["tables"] or [],
    }


def _simplify_type(data_type: str) -> str:
    """Simplify Snowflake data type to business-friendly category."""
    dt = data_type.upper().strip()
    if dt in ("NUMBER", "FLOAT", "DECIMAL", "INTEGER", "INT", "BIGINT",
              "SMALLINT", "TINYINT", "DOUBLE", "REAL", "NUMERIC"):
        return "numeric"
    if dt in ("VARCHAR", "TEXT", "STRING", "CHAR", "NCHAR", "NVARCHAR",
              "CLOB", "NCLOB"):
        return "text"
    if dt in ("TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ",
              "TIMESTAMP", "DATE", "DATETIME", "TIME"):
        return "date/time"
    if dt == "BOOLEAN":
        return "boolean"
    if dt in ("VARIANT", "OBJECT", "ARRAY"):
        return "structured"
    return "text"


def _suggest_field_role(col_name: str, simplified_type: str,
                        is_pk: bool, distinct_count: int | None,
                        null_pct: float | None) -> str:
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
    if any(kw in name_lower for kw in ("description", "comment", "note",
                                        "text", "body", "message", "remark")):
        return "descriptive"

    return ""


def _build_maturity_section(
    maturity: dict[str, dict],
    metadata: list[dict],
) -> str:
    """Build a human-readable maturity classification section for the LLM context."""
    if not maturity:
        return "Not available (pipeline may be cached from before maturity classification was added)."

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
    """Persist current_phase to data_products.state JSONB."""
    try:
        from services.postgres import get_pool as _gp, execute as _ex
        from config import get_effective_settings
        _settings = get_effective_settings()
        _pool = await _gp(_settings.database_url)
        await _ex(
            _pool,
            """UPDATE data_products
               SET state = jsonb_set(COALESCE(state, '{}'::jsonb), '{current_phase}', $1::jsonb)
               WHERE id = $2::uuid""",
            f'"{phase}"',
            data_product_id,
        )
    except Exception as e:
        logger.warning("Failed to persist current_phase=%s: %s", phase, e)


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
5. Using the FIELD ANALYSIS above, PROPOSE 2-3 specific metrics this data
   could support. Use fields tagged "potential measure" for metrics and
   fields tagged "potential dimension" for grouping options.
6. Ask 2-3 validation questions. For each: state your inference, then ask the
   user to confirm. Example: "I suspect X connects to Y through Z — does that
   match your understanding? If you're not sure, I'll proceed with my analysis."

RULES:
- Do NOT call any tools on this first message — everything is in the context above
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
            len(summary), len(metadata), _MAX_SUMMARY_CHARS,
        )
        # Find where table sections end and truncate
        marker = "═══════════════════════════════════════════════════════\nDATA QUALITY"
        marker_pos = summary.find(marker)
        if marker_pos > 0:
            # Get prefix (before tables) and suffix (quality + task sections)
            prefix_end = summary.find("═══════════════════════════════════════════════════════\nTABLE DETAILS")
            suffix = summary[marker_pos:]
            prefix = summary[:prefix_end] if prefix_end > 0 else ""
            # Available space for table sections
            available = _MAX_SUMMARY_CHARS - len(prefix) - len(suffix) - 200
            table_text = chr(10).join(table_sections)
            if len(table_text) > available:
                # Truncate table text and add note
                table_text = table_text[:available] + f"\n\n  ... ({len(metadata)} tables total — showing key columns only)"
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


def _build_multimodal_content(
    text: str,
    file_contents: list | None = None,
) -> str | list[dict]:
    """Build HumanMessage content using standard LangChain content blocks.

    Uses the native LangChain multimodal format (langchain-google-genai v4+):
      - Text files (CSV, TXT, JSON): decoded to UTF-8, appended as text.
      - Images:  ``{"type": "image_url", ...}`` with base64 data URI.
      - PDFs:    ``{"type": "file", "base64": ..., "mime_type": ...}``.
      - Audio:   ``{"type": "media", "data": ..., "mime_type": ...}``.
      - Video:   ``{"type": "media", "data": ..., "mime_type": ...}``.
      - Other:   ``{"type": "media", "data": ..., "mime_type": ...}``.

    Returns a plain string when all attachments are text-decodable (no
    multimodal blocks needed). Returns a list of content block dicts when
    binary attachments (images, PDFs, audio, video) are present.

    The orchestrator LLM (Gemini) processes binary attachments natively.
    When delegating to subagents via Deep Agents ``task()``, the orchestrator
    includes relevant file content in the task description text.
    """
    if not file_contents:
        return text

    from models.schemas import FileContent

    logger.info("Building multimodal content: %d file(s) attached", len(file_contents))
    for fc in file_contents:
        if isinstance(fc, FileContent):
            logger.info("  File: %s (%s, %d bytes base64)",
                        fc.filename, fc.content_type, len(fc.base64_data))

    # Separate text-decodable files from binary files
    text_parts: list[str] = []
    binary_blocks: list[dict] = []

    for fc in file_contents:
        if not isinstance(fc, FileContent):
            continue

        mime = fc.content_type or "application/octet-stream"

        # --- Images: image_url with base64 data URI ---
        if mime.startswith("image/"):
            binary_blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{fc.base64_data}"},
            })

        # --- PDFs / documents: file block with base64 + mime_type ---
        elif mime == "application/pdf":
            binary_blocks.append({
                "type": "file",
                "base64": fc.base64_data,
                "mime_type": mime,
            })

        # --- Audio / video: media block ---
        elif mime.startswith("audio/") or mime.startswith("video/"):
            binary_blocks.append({
                "type": "media",
                "data": fc.base64_data,
                "mime_type": mime,
            })

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

        # --- Unknown binary: try text decode, fall back to media block ---
        else:
            try:
                decoded = base64.b64decode(fc.base64_data).decode("utf-8")
                text_parts.append(f"[Attached file: {fc.filename}]\n{decoded[:50000]}")
            except Exception:
                binary_blocks.append({
                    "type": "media",
                    "data": fc.base64_data,
                    "mime_type": mime,
                })

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
    logger.info("Multimodal content: %d blocks (%s)",
                len(blocks), [b["type"] for b in blocks])
    return blocks


async def _run_agent(
    session_id: str,
    message: str,
    data_product_id: str,
    queue: asyncio.Queue[dict | None],
    file_contents: list | None = None,
) -> None:
    """Run the orchestrator agent and push events to the SSE queue."""
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
    _inside_task: bool = False  # Set to True for discovery below; toggled by task tool for other phases
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
    _stream_llm_calls: int = 0          # on_chat_model_start count
    _stream_llm_completions: int = 0    # on_chat_model_end count
    _stream_raw_chunks: int = 0         # on_chat_model_stream with content (before gating)
    _stream_gated_out: int = 0          # tokens filtered by _inside_task / _subagent_completed
    _stream_dedup_suppressed: int = 0   # tokens suppressed by dedup
    _stream_task_calls: int = 0         # task tool invocations
    # Phase tracking: detect subagent transitions
    _SUBAGENT_PHASE_MAP: dict[str, str] = {
        "discovery-agent": "discovery",
        "transformation-agent": "prepare",
        "requirements-agent": "requirements",
        "modeling-agent": "modeling",
        "generation-agent": "generation",
        "validation-agent": "validation",
        "publishing-agent": "publishing",
        "explorer-agent": "explorer",
    }
    _current_phase: str = "idle"

    try:
        from agents.orchestrator import get_orchestrator
        from config import get_effective_settings

        # Check if this is a discovery trigger
        actual_message = message
        is_discovery = message.strip() in (DISCOVERY_TRIGGER, RERUN_DISCOVERY_TRIGGER)
        force_rerun = message.strip() == RERUN_DISCOVERY_TRIGGER
        dp_info: dict | None = None  # Set inside discovery block, used for timeout check
        if is_discovery:
            _inside_task = True  # Discovery: orchestrator interprets summary directly
            logger.info("Discovery trigger detected for session %s (force=%s), running pipeline...", session_id, force_rerun)
            # Emit phase change to discovery
            _current_phase = "discovery"
            await queue.put({
                "type": "phase_change",
                "data": {"from": "idle", "to": "discovery"},
            })

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
                    await pg_execute(_pool, "DELETE FROM business_requirements WHERE data_product_id = $1::uuid", data_product_id)
                    await pg_execute(_pool, "DELETE FROM semantic_views WHERE data_product_id = $1::uuid", data_product_id)
                    await pg_execute(_pool, "DELETE FROM data_descriptions WHERE data_product_id = $1::uuid", data_product_id)
                    logger.info("Invalidated prior-phase artifacts for data product %s", data_product_id)
                except Exception as e:
                    logger.warning("Failed to invalidate prior artifacts: %s", e)

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
                                await queue.put({
                                    "type": "artifact",
                                    "data": {
                                        "artifact_id": art_id,
                                        "artifact_type": _ART_TYPE_MAP.get(art_type, art_type),
                                    },
                                })

                    # Emit cached maturity tier for frontend phase stepper
                    _cached_maturity = pipeline_results.get("maturity_classifications", {})
                    if _cached_maturity:
                        _cached_tiers = [info.get("maturity", "gold") for info in _cached_maturity.values()]
                        if "bronze" in _cached_tiers:
                            _cached_tier = "bronze"
                        elif "silver" in _cached_tiers:
                            _cached_tier = "silver"
                        else:
                            _cached_tier = "gold"
                    else:
                        _cached_tier = "gold"
                    await queue.put({
                        "type": "data_maturity",
                        "data": {"tier": _cached_tier},
                    })

                # 3. Build human-readable summary for the LLM
                actual_message = _build_discovery_summary(
                    pipeline_results, dp_info["name"], data_product_id,
                    dp_description=dp_info["description"],
                )
                logger.info("Pipeline complete, summary length: %d chars", len(actual_message))

                # 4. Emit data maturity tier so frontend can adapt the phase stepper.
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

                await queue.put({
                    "type": "data_maturity",
                    "data": {"tier": _aggregate_tier},
                })

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
        from tools.snowflake_tools import set_data_isolation_context
        from tools.postgres_tools import set_data_product_context

        # Set data_product_id contextvar — ensures tools always use the correct UUID
        # even if the LLM truncates or mangles the ID in tool call arguments.
        set_data_product_context(data_product_id)

        dp_info_for_isolation = await _get_data_product_info(data_product_id)
        if dp_info_for_isolation:
            set_data_isolation_context(
                database=dp_info_for_isolation["database"],
                tables=dp_info_for_isolation["tables"],
            )
        else:
            set_data_isolation_context(database=None, tables=None)

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
        _chk_msgs = (_chk_state.values.get("messages", [])
                     if _chk_state and _chk_state.values else [])
        _patches: list = []
        for _m in _chk_msgs:
            if _m.type == "ai":
                _c = _m.content
                _is_empty = (not _c or _c == [] or _c == "" or
                             (isinstance(_c, list) and len(_c) == 0))
                if _is_empty:
                    if getattr(_m, "tool_calls", None):
                        _patches.append(AIMessage(content=".", id=_m.id,
                                                  tool_calls=_m.tool_calls))
                    else:
                        _patches.append(_RM(id=_m.id))
        if _patches:
            try:
                await agent.aupdate_state(config, {"messages": _patches})
                logger.info("Patched %d empty AI messages in checkpoint for session %s",
                            len(_patches), session_id)
            except (UnboundLocalError, Exception) as patch_err:
                # LangGraph may fail internally when patching certain checkpoint states
                # (e.g. last_ai_index UnboundLocalError). Log and continue — the agent
                # can still function; empty messages may cause Gemini to complain but
                # the fallback/safety nets will handle it.
                logger.warning(
                    "Failed to patch empty AI messages for session %s: %s. Continuing without patch.",
                    session_id, patch_err,
                )

        # Wire the SSE queue contextvar so build_erd_from_description can emit artifact events
        from tools.discovery_tools import _sse_queue
        _sse_queue.set(queue)

        # With PostgreSQL checkpointer, LangGraph automatically restores
        # conversation history for this thread_id. We only send the new message.
        content = _build_multimodal_content(actual_message, file_contents)
        input_messages = {"messages": [HumanMessage(content=content)]}

        # Stream events from the agent.
        # For discovery of large datasets, apply a timeout to prevent indefinite hangs
        # when the LLM fails to respond to very large summaries.
        # Timeout for large discovery: use table count (available from dp_info), not
        # summary length (already truncated by this point).
        _dp_table_count = len(dp_info.get("tables", [])) if is_discovery and dp_info else 0
        _agent_timeout = 180 if is_discovery and _dp_table_count > 15 else None  # 3 min for large datasets
        _agent_stream = agent.astream_events(input_messages, config=config, version="v2")
        _stream_timed_out = False
        if _agent_timeout:
            logger.info("Applying %ds timeout for large discovery summary (%d chars)", _agent_timeout, len(actual_message))

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
                _stream_timed_out = True
                logger.warning(
                    "Agent stream timed out after %ds for session %s (events=%d, tokens=%d)",
                    _agent_timeout, session_id, _stream_event_count, _stream_token_count,
                )
                break

            _stream_event_count += 1
            kind = event.get("event", "")
            data = event.get("data", {})

            # Track LLM call lifecycle for diagnostics
            if kind == "on_chat_model_start":
                _stream_llm_calls += 1
                _llm_model = event.get("name", "unknown")
                logger.info("LLM call #%d started (model=%s, session=%s, inside_task=%s, subagent_completed=%s)",
                            _stream_llm_calls, _llm_model, session_id, _inside_task, _subagent_completed)
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
                logger.info("LLM call #%d completed (content_len=%d, tool_calls=%d, session=%s)",
                            _stream_llm_completions, _end_content_len, _end_tool_calls, session_id)

            if kind == "on_chat_model_stream":
                # Token streaming — only emit tokens from subagent runs.
                # The orchestrator is a router; its text is always suppressed.
                run_id = event.get("run_id", "")
                chunk = data.get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    _stream_raw_chunks += 1
                    # If a new LLM run starts, emit message_done to close previous bubble
                    if run_id and run_id != _current_run_id:
                        # Flush any dedup buffer from the ending run
                        if _run_token_buffer and not _run_suppressed:
                            buffered_text = "".join(_run_token_buffer)
                            # Short message that didn't reach threshold — check if duplicate
                            if _previous_run_content and _previous_run_content.startswith(buffered_text.strip()):
                                _run_suppressed = True
                                logger.info("Suppressing short duplicate subagent run")
                            else:
                                current_assistant_content = buffered_text
                                for tok in _run_token_buffer:
                                    await queue.put({
                                        "type": "token",
                                        "data": {"content": tok},
                                    })
                            _run_token_buffer = []

                        if _current_run_id is not None and current_assistant_content.strip():
                            _previous_run_content = current_assistant_content.strip()
                            _assistant_texts.append(current_assistant_content)
                            current_assistant_content = ""
                            await queue.put({
                                "type": "message_done",
                                "data": {"content": ""},
                            })
                        _current_run_id = run_id
                        # Reset per-run dedup state
                        _run_token_buffer = []
                        _run_suppressed = False
                        _run_dedup_resolved = not (_inside_task and bool(_previous_run_content))

                    # Gate: only emit tokens from subagent runs (inside task tool)
                    if not _inside_task:
                        _stream_gated_out += 1
                        continue

                    # Extract text from structured content blocks if needed
                    content = chunk.content
                    if isinstance(content, list):
                        text_parts = [
                            block.get("text", "") if isinstance(block, dict) else str(block)
                            for block in content
                        ]
                        content = "".join(text_parts)
                    elif isinstance(content, dict):
                        content = content.get("text", str(content))

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
                                logger.info("Suppressing duplicate subagent run (prefix matches previous)")
                            else:
                                # Not a duplicate — flush buffered tokens
                                current_assistant_content = buffered_text
                                for tok in _run_token_buffer:
                                    await queue.put({
                                        "type": "token",
                                        "data": {"content": tok},
                                    })
                                _run_token_buffer = []
                        continue

                    # Normal path — emit token
                    current_assistant_content += content

                    # Suppress [INTERNAL] sections that LLMs sometimes leak
                    if "[INTERNAL" in current_assistant_content:
                        continue

                    _stream_token_count += 1
                    await queue.put({
                        "type": "token",
                        "data": {"content": content},
                    })

            elif kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                tool_input = data.get("input", {})

                # Track upload_artifact calls and emit artifact event from input
                # (more reliable than parsing output — subagent tool output may not propagate cleanly)
                if tool_name == "upload_artifact" and isinstance(tool_input, dict):
                    art_type = tool_input.get("artifact_type", "")
                    art_filename = tool_input.get("filename", "")
                    # Guard: correct type if filename contradicts it
                    if "data-description" in art_filename.lower() and art_type != "data_description":
                        art_type = "data_description"
                    if art_type == "brd":
                        _brd_artifact_uploaded = True
                    if art_type:
                        # Map backend artifact types to frontend types
                        _ARTIFACT_TYPE_MAP = {"quality_report": "data_quality"}
                        mapped_type = _ARTIFACT_TYPE_MAP.get(art_type, art_type)
                        await queue.put({
                            "type": "artifact",
                            "data": {
                                "artifact_id": str(uuid4()),
                                "artifact_type": mapped_type,
                            },
                        })
                        logger.info("Emitted artifact event from tool_start: type=%s", mapped_type)

                # Detect subagent delegation via the `task` tool and emit phase_change
                # Deep Agents uses a `task` tool with `subagent_type` parameter
                if tool_name == "task" and isinstance(tool_input, dict):
                    _stream_task_calls += 1
                    _inside_task = True  # Enable token emission for subagent
                    _subagent_completed = False
                    subagent_type = tool_input.get("subagent_type", "")
                    phase_name = _SUBAGENT_PHASE_MAP.get(subagent_type)
                    if phase_name == "discovery":
                        _discovery_invocation_count += 1
                        if _discovery_invocation_count > 1:
                            _discovery_conversation_ran = True
                    if phase_name == "transformation":
                        _transformation_phase_ran = True
                    if phase_name == "requirements":
                        _requirements_phase_ran = True
                    if phase_name == "modeling":
                        _modeling_phase_ran = True
                    if phase_name == "generation":
                        _generation_phase_ran = True
                    if phase_name and phase_name != _current_phase:
                        old_phase = _current_phase
                        _current_phase = phase_name
                        await queue.put({
                            "type": "phase_change",
                            "data": {"from": old_phase, "to": phase_name},
                        })
                        logger.info("Phase change: %s → %s (session %s)", old_phase, phase_name, session_id)
                        # Persist current_phase to data_products.state
                        await _persist_phase(data_product_id, phase_name)

                # Skip tool_call event for internal `task` tool — phase_change events handle this
                if tool_name != "task":
                    await queue.put({
                        "type": "tool_call",
                        "data": {
                            "tool": tool_name,
                            "input": tool_input if isinstance(tool_input, dict) else str(tool_input),
                        },
                    })

            elif kind == "on_tool_end":
                tool_name = event.get("name", "unknown")
                output = data.get("output", "")
                # Extract content from ToolMessage objects (LangChain may return these)
                if hasattr(output, "content"):
                    output = output.content
                # Truncate long outputs
                truncate_len = _settings.tool_output_truncate_length
                output_str = str(output)[:truncate_len] if output else ""

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

                # Track register_gold_layer for modeling phase
                if tool_name == "register_gold_layer":
                    _gold_layer_registered = True
                    logger.info("register_gold_layer completed for session %s", session_id)

                # Track save_semantic_view for generation safety net
                if tool_name == "save_semantic_view":
                    _yaml_tool_called = True
                    logger.info("save_semantic_view completed for session %s", session_id)

                # Mark when a subagent completes and close the task gate
                if tool_name == "task":
                    _inside_task = False
                    _subagent_completed = True
                    logger.info("Subagent completed for session %s", session_id)

                # Artifact event already emitted from on_tool_start (more reliable).
                # Log the output for debugging but don't emit a second artifact event.
                if tool_name == "upload_artifact" and output_str:
                    logger.info("upload_artifact output (type=%s): %s", type(output).__name__, output_str[:200])

                await queue.put({
                    "type": "tool_result",
                    "data": {
                        "tool": tool_name,
                        "output": output_str,
                    },
                })

            elif kind == "on_chain_end" and event.get("name") == "ekaix-orchestrator":
                # Final orchestrator response — suppress entirely.
                # All user-facing content comes from subagent runs (inside task tool).
                pass

    except (asyncio.TimeoutError, TimeoutError) as e:
        logger.warning(
            "Agent timed out for session %s after streaming %d events, %d tokens. "
            "Large discovery summary may have caused the LLM to hang.",
            session_id, _stream_event_count, _stream_token_count,
        )
        # The fallback in the finally block will handle sending a message to the user.

    except ValueError as e:
        # LangChain raises ValueError("No generations found in stream") when an LLM
        # produces an empty response (e.g. orchestrator after subagent did all the work).
        # This is benign — the subagent already delivered content.
        if "no generations" in str(e).lower():
            if is_discovery and _stream_token_count == 0:
                logger.warning("Agent %s: no generations during discovery (summary_len=%d) — fallback will fire in finally",
                               session_id, len(actual_message))
            else:
                logger.info("Agent %s produced empty response (expected after subagent delegation)", session_id)
        else:
            logger.exception("Agent execution failed for session %s: %s", session_id, e)
            await queue.put({
                "type": "error",
                "data": {"message": _sanitize_error_for_user(e)},
            })
    except Exception as e:
        # LiteLLM Router handles retries and fallback automatically when enabled.
        # Just log and emit the error to the user.
        logger.exception("Agent execution failed for session %s: %s", session_id, e)
        await queue.put({
            "type": "error",
            "data": {"message": _sanitize_error_for_user(e)},
        })

    finally:
        # Flush any remaining dedup buffer from the last run
        if _run_token_buffer and not _run_suppressed:
            current_assistant_content = "".join(_run_token_buffer)
            _run_token_buffer = []

        # Diagnostic: log stream statistics — full pipeline for root-cause analysis
        logger.info(
            "Agent stream completed for session %s: events=%d, llm_calls=%d, llm_completions=%d, "
            "raw_chunks=%d, gated_out=%d, dedup_suppressed=%d, tokens_emitted=%d, "
            "task_calls=%d, assistant_texts=%d",
            session_id, _stream_event_count, _stream_llm_calls, _stream_llm_completions,
            _stream_raw_chunks, _stream_gated_out, _stream_dedup_suppressed, _stream_token_count,
            _stream_task_calls, len(_assistant_texts) + (1 if current_assistant_content.strip() else 0),
        )

        # --- Zero-output fallback for discovery ---
        # If the LLM produced zero visible tokens during discovery, the user sees nothing.
        # Send a fallback message so the user can still interact.
        if is_discovery and _stream_token_count == 0 and not current_assistant_content.strip():
            logger.warning(
                "ZERO-OUTPUT FALLBACK: Discovery agent produced no tokens for session %s "
                "(events=%d, summary_len=%d). Sending fallback message.",
                session_id, _stream_event_count, len(actual_message),
            )
            fallback_msg = (
                "I've analyzed your data and completed the initial profiling. "
                "I found some interesting patterns across the tables. "
                "Could you tell me more about what you're looking to accomplish with this data? "
                "That will help me tailor my analysis to your specific needs."
            )
            current_assistant_content = fallback_msg
            for token_chunk in [fallback_msg]:
                await queue.put({
                    "type": "token",
                    "data": {"content": token_chunk},
                })
            await queue.put({
                "type": "message_done",
                "data": {"content": ""},
            })

        # Add final assistant content to local buffer for safety net
        if current_assistant_content.strip():
            _assistant_texts.append(current_assistant_content)

        # --- Safety net: save Data Description if discovery agent produced text but didn't call save_data_description ---
        if _discovery_conversation_ran and not _dd_tool_called:
            dd_content = ""
            for text in _assistant_texts:
                if len(text) > len(dd_content):
                    dd_content = text
            _DD_MARKERS = ("[1] System Architecture", "[2] Business Context",
                           "---BEGIN DATA DESCRIPTION---", "[6] Data Map")
            has_dd_markers = any(marker in dd_content for marker in _DD_MARKERS)
            if len(dd_content) > 1000 and has_dd_markers:
                logger.warning(
                    "Safety net: discovery agent did not call save_data_description for session %s — saving programmatically",
                    session_id,
                )
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
                    await queue.put({
                        "type": "artifact",
                        "data": {
                            "artifact_id": dd_id,
                            "artifact_type": "data_description",
                        },
                    })

                    # Trigger ERD build if not already done
                    if not _erd_build_called:
                        try:
                            from services.discovery_pipeline import run_erd_pipeline
                            erd_result = await run_erd_pipeline(data_product_id, {"document": dd_content})
                            erd_artifact_id = erd_result.get("erd_artifact_id")
                            if erd_artifact_id:
                                await queue.put({
                                    "type": "artifact",
                                    "data": {
                                        "artifact_id": erd_artifact_id,
                                        "artifact_type": "erd",
                                    },
                                })
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

        # --- Safety net: save BRD if requirements agent produced text but didn't call save_brd ---
        if _requirements_phase_ran and not _brd_tool_called:
            # Find the longest assistant message — likely the BRD
            brd_content = ""
            for text in _assistant_texts:
                if len(text) > len(brd_content):
                    brd_content = text
            # Only save if it looks like a BRD (> 2000 chars AND contains BRD section markers)
            _BRD_MARKERS = ("SECTION 1:", "EXECUTIVE SUMMARY", "METRICS AND CALCULATIONS",
                            "DATA PRODUCT:", "---BEGIN BRD---", "SECTION 2:")
            has_brd_markers = any(marker in brd_content for marker in _BRD_MARKERS)
            if len(brd_content) > 2000 and has_brd_markers:
                logger.warning(
                    "Safety net: requirements agent did not call save_brd for session %s — saving programmatically",
                    session_id,
                )
                try:
                    from tools.postgres_tools import _get_pool
                    from services import postgres as _pg_svc
                    from uuid import uuid4 as _uuid4

                    pool = await _get_pool()
                    brd_id = str(_uuid4())
                    clean_json = json.dumps({"document": brd_content})
                    sql = """
                    INSERT INTO business_requirements (id, data_product_id, brd_json, created_by, created_at)
                    VALUES ($1::uuid, $2::uuid, $3::jsonb, $4, NOW())
                    """
                    await _pg_svc.execute(pool, sql, brd_id, data_product_id, clean_json, "ai-agent")
                    logger.info("Safety net: BRD saved to PostgreSQL (brd_id=%s)", brd_id)
                    _brd_tool_called = True

                    # Also upload as artifact
                    if not _brd_artifact_uploaded:
                        from tools.minio_tools import upload_artifact_programmatic
                        await upload_artifact_programmatic(
                            data_product_id=data_product_id,
                            artifact_type="brd",
                            filename="business-requirements.md",
                            content=brd_content,
                        )
                        logger.info("Safety net: BRD artifact uploaded for %s", data_product_id)
                        # Emit artifact event so frontend shows the card
                        await queue.put({
                            "type": "artifact",
                            "data": {
                                "artifact_id": brd_id,
                                "artifact_type": "brd",
                            },
                        })
                except Exception as e:
                    logger.error("Safety net: failed to save BRD: %s", e)
            else:
                logger.info(
                    "Safety net: skipped — no BRD content detected (longest msg: %d chars, markers: %s)",
                    len(brd_content),
                    has_brd_markers,
                )

        # --- Safety net: save YAML if generation agent produced content but didn't call save_semantic_view ---
        if _generation_phase_ran and not _yaml_tool_called:
            # 1. Try extracting JSON structure first (preferred — goes through full assembler)
            yaml_content = ""
            for text in _assistant_texts:
                if '"tables"' in text and ('"facts"' in text or '"dimensions"' in text):
                    try:
                        from agents.generation import extract_json_from_text, assemble_semantic_view_yaml, build_table_metadata, _build_fqn_sample_values, build_working_layer_map
                        structure = extract_json_from_text(text)
                        if structure and "tables" in structure:
                            meta = await build_table_metadata(data_product_id, structure)
                            sv_map = await _build_fqn_sample_values(data_product_id)
                            wl_map = await build_working_layer_map(data_product_id)
                            yaml_content = assemble_semantic_view_yaml(structure, table_metadata=meta, sample_values_map=sv_map, working_layer_map=wl_map)
                            break
                    except Exception as e:
                        logger.warning("Generation safety net: failed to assemble YAML from JSON: %s", e)

            # 2. Fallback: try extracting YAML from assistant output
            if not yaml_content:
                for text in _assistant_texts:
                    if "tables:" in text and "base_table:" in text:
                        yaml_text = _extract_yaml_from_text(text)
                        if yaml_text and len(yaml_text) > 100:
                            try:
                                from agents.generation import quote_columns_in_yaml_str
                                yaml_content = await quote_columns_in_yaml_str(yaml_text, data_product_id)
                                logger.info("Generation safety net: extracted YAML from assistant output (%d chars)", len(yaml_content))
                            except Exception as e:
                                logger.warning("Generation safety net: YAML quoting failed, using raw: %s", e)
                                yaml_content = yaml_text
                            break

            if yaml_content and len(yaml_content) > 100:
                logger.warning(
                    "Safety net: generation agent did not call save_semantic_view for session %s — saving programmatically",
                    session_id,
                )
                try:
                    from tools.postgres_tools import _get_pool
                    from services import postgres as _pg_svc
                    from uuid import uuid4 as _uuid4

                    pool = await _get_pool()
                    sv_id = str(_uuid4())
                    sql = """
                    INSERT INTO semantic_views (id, data_product_id, yaml_content, created_by, created_at)
                    VALUES ($1::uuid, $2::uuid, $3, $4, NOW())
                    """
                    await _pg_svc.execute(pool, sql, sv_id, data_product_id, yaml_content, "ai-agent")
                    logger.info("Safety net: YAML saved to PostgreSQL (sv_id=%s)", sv_id)
                    _yaml_tool_called = True

                    # Upload as artifact
                    from tools.minio_tools import upload_artifact_programmatic
                    await upload_artifact_programmatic(
                        data_product_id=data_product_id,
                        artifact_type="yaml",
                        filename="semantic-view.yaml",
                        content=yaml_content,
                    )
                    logger.info("Safety net: YAML artifact uploaded for %s", data_product_id)
                    await queue.put({
                        "type": "artifact",
                        "data": {
                            "artifact_id": sv_id,
                            "artifact_type": "yaml",
                        },
                    })
                except Exception as e:
                    logger.error("Safety net: failed to save YAML: %s", e)
            else:
                logger.info(
                    "Generation safety net: skipped — no valid JSON or YAML structure found in assistant output",
                )

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

        # Transition to explorer phase ONLY when the full pipeline completed
        # (i.e. publishing was the last active phase). Otherwise the stepper
        # incorrectly shows all phases as completed when pausing mid-pipeline.
        if _current_phase == "publishing":
            await queue.put({
                "type": "phase_change",
                "data": {"from": _current_phase, "to": "explorer"},
            })
            logger.info("Phase change: %s → explorer (session %s, stream end)", _current_phase, session_id)
            await _persist_phase(data_product_id, "explorer")

        # Signal stream end
        await queue.put({
            "type": "done",
            "data": {"message": "Agent processing complete"},
        })
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
        await queue.put({
            "type": "error",
            "data": {"message": "Interrupted by user"},
        })
        await queue.put({
            "type": "done",
            "data": {"message": "Session interrupted by user"},
        })
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
        await queue.put({
            "type": "approval_response",
            "data": {"approved": request.approved, "status": status},
        })

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
        all_messages = (current_state.values.get("messages", [])
                        if current_state and current_state.values else [])

        if not all_messages:
            await queue.put({
                "type": "error",
                "data": {"message": "No messages found to retry."},
            })
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
            await queue.put({
                "type": "error",
                "data": {"message": "Could not find the message to retry."},
            })
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
        remaining_msgs = (refreshed_state.values.get("messages", [])
                          if refreshed_state and refreshed_state.values else [])
        patches = []
        extra_removes = []
        for m in remaining_msgs:
            if m.type == "ai":
                c = m.content
                is_empty = (not c or c == [] or c == "" or
                            (isinstance(c, list) and len(c) == 0))
                if is_empty:
                    if getattr(m, "tool_calls", None):
                        # Has tool calls — patch content with placeholder
                        patches.append(AIMessage(
                            content=".",
                            id=m.id,
                            tool_calls=m.tool_calls,
                        ))
                    else:
                        # No content AND no tool calls — remove entirely
                        extra_removes.append(RemoveMessage(id=m.id))
        updates = patches + extra_removes
        if updates:
            await agent.aupdate_state(config, {"messages": updates})
            logger.info("Retry: fixed %d patched + %d removed empty AI messages", len(patches), len(extra_removes))

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
        await queue.put({
            "type": "error",
            "data": {"message": _sanitize_error_for_user(e)},
        })
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
            checkpoints.append({
                "checkpoint_id": state.config["configurable"].get("checkpoint_id"),
                "message_count": len(messages),
                "last_message_id": last_msg_id,
                "created_at": state.created_at,
                "next": list(state.next) if state.next else [],
            })
        return {"session_id": session_id, "checkpoints": checkpoints}
    except Exception as e:
        logger.error("Failed to list checkpoints for session %s: %s", session_id, e)
        return {"session_id": session_id, "checkpoints": [], "error": str(e)}


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
                # Map roles: human -> user, everything else (ai, tool) -> assistant
                # Deep Agents stores subagent responses as tool messages
                role = "user" if msg.type == "human" else "assistant"
                messages.append({"role": role, "content": content, "id": msg.id})

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

            return {"session_id": session_id, "messages": deduped, "phase": phase}
    except Exception as e:
        logger.error("Failed to get history for session %s: %s", session_id, e)

    return {"session_id": session_id, "messages": [], "phase": "discovery"}
