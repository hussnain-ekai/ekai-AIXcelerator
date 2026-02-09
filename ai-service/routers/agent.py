"""Agent conversation endpoints — message handling, SSE streaming, and control actions."""

import asyncio
import base64
import json
import logging
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

    # Build profile lookup: fqn -> {column -> profile_data}
    profile_lookup: dict[str, dict[str, dict]] = {}
    for p in profiles:
        table_fqn = p.get("table", "")
        col_map: dict[str, dict] = {}
        for col in p.get("columns", []):
            col_map[col.get("column", "")] = col
        profile_lookup[table_fqn] = col_map

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

        # Build field analysis lines
        field_lines = []
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

            # Build description parts
            parts = [simple_type]
            if is_pk:
                parts.append("unique identifier")
            if distinct is not None and simple_type == "text" and distinct <= 100:
                parts.append(f"{distinct} values")
            if null_pct is not None and null_pct > 5:
                parts.append(f"{100 - null_pct:.0f}% complete")
            if role and role not in ("identifier", "descriptive", ""):
                parts.append(role)

            field_lines.append(f"    - {col_name} ({', '.join(parts)})")

        section = f"  {name} ({biz_type}{row_str})\n" + "\n".join(field_lines)
        table_sections.append(section)

    # Build relationship summaries
    rel_lines = []
    name_map = {t["fqn"]: t["name"] for t in metadata}
    for rel in relationships:
        src = name_map.get(rel["from_table"], rel["from_table"].split(".")[-1])
        tgt = name_map.get(rel["to_table"], rel["to_table"].split(".")[-1])
        via = rel.get("from_column", "")
        confidence = rel.get("confidence", 0)
        conf_str = "strong" if confidence >= 0.9 else "likely"
        rel_lines.append(f"  - {src} connects to {tgt} via {via} ({conf_str})")

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
CONNECTIONS
═══════════════════════════════════════════════════════
{chr(10).join(rel_lines) if rel_lines else '  (none detected)'}

═══════════════════════════════════════════════════════
DATA QUALITY
═══════════════════════════════════════════════════════
Score: {score}/100 (average completeness: {completeness:.0f}%)
{issue_summary}

═══════════════════════════════════════════════════════
YOUR TASK
═══════════════════════════════════════════════════════
All profiling, classification, relationship detection, data map construction,
and quality checks are ALREADY DONE. Artifacts are ALREADY saved.

Your job is to:
1. Interpret these results in natural business language
2. Recognize the business domain from table/field naming patterns
3. Mention the quality score naturally
4. Using the FIELD ANALYSIS above, PROPOSE 2-3 specific analytical questions or metrics
   this data could support. Use fields tagged "potential measure" for metrics and
   fields tagged "potential dimension" for grouping options.
5. If the user's description above states their goal, tailor your suggestions to it.
   Do NOT re-ask what they want to do — they already told you. Confirm understanding instead.

RULES:
- Do NOT call any tools — everything is already computed and saved
- Do NOT repeat the data above verbatim — interpret it in business language
- Refer to the data product as "{dp_name}"
- Use table short names (e.g. "your Customers table") not FQNs
- Your suggested metrics MUST reference actual field names from the analysis above
  Use format: business name (FIELD_NAME) — e.g. "average reading value (VALUE)"
- DATA ISOLATION: ONLY discuss the tables listed above. You know NOTHING about
  any other databases, schemas, or tables in this Snowflake account. They do not
  exist to you. NEVER mention or speculate about any other datasets.
══════════════════════════════════════════════════════════════════"""

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
    # Safety net: track whether save_brd was called during this invocation
    _brd_tool_called: bool = False
    _brd_artifact_uploaded: bool = False
    _requirements_phase_ran: bool = False
    # Safety net: track whether save_semantic_view was called during generation
    _yaml_tool_called: bool = False
    _generation_phase_ran: bool = False
    # Phase tracking: detect subagent transitions
    _SUBAGENT_PHASE_MAP: dict[str, str] = {
        "discovery-agent": "discovery",
        "requirements-agent": "requirements",
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
        if is_discovery:
            _inside_task = True  # Discovery: orchestrator interprets summary directly
            logger.info("Discovery trigger detected for session %s (force=%s), running pipeline...", session_id, force_rerun)
            # Emit phase change to discovery
            _current_phase = "discovery"
            await queue.put({
                "type": "phase_change",
                "data": {"from": "idle", "to": "discovery"},
            })

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
                        # Map storage types to frontend types
                        _ART_TYPE_MAP = {"quality_report": "data_quality"}
                        for art_type, art_id in artifact_ids.items():
                            if art_id and art_type in ("erd", "quality_report"):
                                await queue.put({
                                    "type": "artifact",
                                    "data": {
                                        "artifact_id": art_id,
                                        "artifact_type": _ART_TYPE_MAP.get(art_type, art_type),
                                    },
                                })

                # 3. Build human-readable summary for the LLM
                actual_message = _build_discovery_summary(
                    pipeline_results, dp_info["name"], data_product_id,
                    dp_description=dp_info["description"],
                )
                logger.info("Pipeline complete, summary length: %d chars", len(actual_message))

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
            await agent.aupdate_state(config, {"messages": _patches})
            logger.info("Patched %d empty AI messages in checkpoint for session %s",
                        len(_patches), session_id)

        # With PostgreSQL checkpointer, LangGraph automatically restores
        # conversation history for this thread_id. We only send the new message.
        content = _build_multimodal_content(actual_message, file_contents)
        input_messages = {"messages": [HumanMessage(content=content)]}

        # Stream events from the agent
        async for event in agent.astream_events(input_messages, config=config, version="v2"):
            kind = event.get("event", "")
            data = event.get("data", {})

            if kind == "on_chat_model_stream":
                # Token streaming — only emit tokens from subagent runs.
                # The orchestrator is a router; its text is always suppressed.
                run_id = event.get("run_id", "")
                chunk = data.get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
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
                    _inside_task = True  # Enable token emission for subagent
                    _subagent_completed = False
                    subagent_type = tool_input.get("subagent_type", "")
                    phase_name = _SUBAGENT_PHASE_MAP.get(subagent_type)
                    if phase_name == "requirements":
                        _requirements_phase_ran = True
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

                # Track save_brd completion for safety net
                if tool_name == "save_brd":
                    _brd_tool_called = True
                    logger.info("save_brd completed for session %s", session_id)

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

    except ValueError as e:
        # LangChain raises ValueError("No generations found in stream") when an LLM
        # produces an empty response (e.g. orchestrator after subagent did all the work).
        # This is benign — the subagent already delivered content.
        if "no generations" in str(e).lower():
            logger.info("Agent %s produced empty response (expected after subagent delegation)", session_id)
        else:
            logger.exception("Agent execution failed for session %s: %s", session_id, e)
            await queue.put({
                "type": "error",
                "data": {"message": _sanitize_error_for_user(e)},
            })
    except Exception as e:
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

        # Add final assistant content to local buffer for safety net
        if current_assistant_content.strip():
            _assistant_texts.append(current_assistant_content)

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

        # --- Safety net: save YAML if generation agent produced JSON but didn't call save_semantic_view ---
        if _generation_phase_ran and not _yaml_tool_called:
            # Look for JSON structure in assistant messages
            yaml_content = ""
            for text in _assistant_texts:
                if '"tables"' in text and ('"facts"' in text or '"dimensions"' in text):
                    try:
                        from agents.generation import extract_json_from_text, assemble_semantic_view_yaml
                        structure = extract_json_from_text(text)
                        if structure and "tables" in structure:
                            yaml_content = assemble_semantic_view_yaml(structure)
                            break
                    except Exception as e:
                        logger.warning("Generation safety net: failed to assemble YAML from JSON: %s", e)

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
                    "Generation safety net: skipped — no valid JSON structure found in assistant output",
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
      save_brd                                 → requirements (BRD done)
      otherwise                                → discovery
    """
    phase_rank = {
        "discovery": 0,
        "requirements": 1,
        "generation": 2,
        "publishing": 3,
        "explorer": 4,
    }
    tool_to_phase = {
        "save_brd": "requirements",
        "save_semantic_view": "generation",
        "update_validation_status": "publishing",
        "create_cortex_agent": "explorer",
        "grant_agent_access": "explorer",
    }

    best_phase = "discovery"
    best_rank = 0

    # 1. Scan checkpoint messages for tool calls
    for msg in all_messages:
        tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in tool_calls:
            name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
            mapped = tool_to_phase.get(name)
            if mapped and phase_rank.get(mapped, 0) > best_rank:
                best_phase = mapped
                best_rank = phase_rank[mapped]

    # 2. If checkpoint alone suggests early phase, cross-check with database
    #    (covers truncated checkpoints from service restarts/testing)
    if best_rank < phase_rank["generation"]:
        try:
            from services import postgres as pg_service
            pool = pg_service._pool

            # Look up data_product_id from session_id
            dp_rows = await pg_service.query(
                pool,
                "SELECT id FROM data_products WHERE state->>'session_id' = $1 LIMIT 1",
                session_id,
            )
            if dp_rows:
                dp_id = str(dp_rows[0]["id"])

                # Check for semantic views (most definitive)
                sv_rows = await pg_service.query(
                    pool,
                    "SELECT validation_status FROM semantic_views WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
                    dp_id,
                )
                if sv_rows:
                    status = sv_rows[0].get("validation_status")
                    if status == "valid":
                        # Check if Cortex Agent was also created (artifacts with type 'yaml' after validation)
                        # Use audit_logs or just assume publishing since we have valid yaml
                        log_rows = await pg_service.query(
                            pool,
                            "SELECT 1 FROM audit_logs WHERE data_product_id = $1::uuid AND action_type = 'publish' LIMIT 1",
                            dp_id,
                        )
                        if log_rows:
                            best_phase = "explorer"
                        else:
                            best_phase = "publishing"
                    elif status == "invalid":
                        best_phase = "generation"
                    else:
                        best_phase = "generation"
                else:
                    # No semantic view — check for BRD
                    brd_rows = await pg_service.query(
                        pool,
                        "SELECT 1 FROM business_requirements WHERE data_product_id = $1::uuid LIMIT 1",
                        dp_id,
                    )
                    if brd_rows:
                        best_phase = "requirements"
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
