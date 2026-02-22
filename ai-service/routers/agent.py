"""Agent conversation endpoints — message handling, SSE streaming, and control actions."""

import asyncio
import base64
import json
import logging
import re
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
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
    return "Something went wrong while processing your request. Please try again or contact support."


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
        "modeling": "generation",   # internal phase folded into external generation status
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


def _is_requirements_transition_intent(message: str) -> bool:
    """Detect explicit user intent to move from discovery to requirements."""
    text = _normalize_user_text(message)
    if not text:
        return False
    if text in _GENERIC_PROCEED_WORDS:
        return True
    return any(re.search(p, text) for p in _REQ_MOVE_PATTERNS)


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
    has_data_description = bool(snapshot.get("data_description_exists"))
    data_tier = (snapshot.get("data_tier") or "").lower()
    transformation_done = bool(snapshot.get("transformation_done"))

    if not has_data_description:
        return False, "Data description not available yet."

    if data_tier in {"silver", "bronze"} and not transformation_done:
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
        f"data_description_exists={bool(snapshot.get('data_description_exists'))}",
        f"transformation_done={bool(snapshot.get('transformation_done'))}",
        f"brd_exists={bool(snapshot.get('brd_exists'))}",
        f"semantic_view_exists={bool(snapshot.get('semantic_view_exists'))}",
        f"validation_status={validation_status}",
        "internal_ids_available=true",
        "id_request_policy=never ask user for data_product_id/session_id/uuid; use context values silently",
        (
            "communication_policy=business labels first; reveal technical detail only if user explicitly asks"
        ),
        (
            "requirements_policy=ask focused high-signal questions, avoid generic fluff and info dumps, continue until requirements are complete"
        ),
    ]

    if transition_target:
        lines.append(f"forced_transition={current_phase}->{transition_target}")
    if transition_reason:
        lines.append(f"transition_reason={transition_reason}")
    if forced_subagent:
        lines.append(f"forced_subagent={forced_subagent}")
    if forced_intent:
        lines.append(f"forced_intent={forced_intent}")

    lines.append("[END SUPERVISOR CONTEXT CONTRACT]")
    lines.append(f"[USER MESSAGE]\n{user_message}")
    return "\n".join(lines)


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
    )
    return any(p in lower for p in leak_patterns)


def _sanitize_assistant_text(text: str) -> str:
    """Supervisor-level output sanitizer for persona-safe chat rendering."""
    if not text:
        return ""

    # Strip common markdown that occasionally leaks through.
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # Drop lines that expose orchestration internals.
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        if _is_internal_reasoning_leak(line):
            continue
        if re.search(r"\b(data[_ ]product[_ ]id|session[_ ]id|uuid)\b", line, flags=re.IGNORECASE):
            continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    replacements: tuple[tuple[str, str], ...] = (
        (r"\bUUID\b", "internal ID"),
        (r"\bFQN\b", "table reference"),
        (r"\bDDL\b", "schema instruction"),
        (r"\bSQL\b", "query logic"),
        (r"\bVARCHAR\b", "text"),
        (r"\bTIMESTAMP_NTZ\b", "timestamp"),
        (r"\bTABLESAMPLE\b", "sampling"),
        (r"\bINFORMATION_SCHEMA\b", "metadata catalog"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Collapse pathological "Wait..." loops.
    text = re.sub(r"(\bWait\b[^\n]*\n?){2,}", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


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
        window = window[first_space + 1:]
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
                    for key in ("reasoning_content", "reasoning", "thinking", "thoughts", "analysis"):
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
                if any(marker in block_type for marker in ("reason", "think", "analysis", "thought")):
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

    recovered = content[idx + len(marker):].strip()
    return recovered or None


async def _get_workflow_snapshot(data_product_id: str) -> dict[str, Any]:
    """Load workflow state used by supervisor guards and context contract."""
    snapshot: dict[str, Any] = {
        "current_phase": "discovery",
        "data_tier": None,
        "transformation_done": False,
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
                   state->>'current_phase' AS current_phase,
                   state->>'data_tier' AS data_tier,
                   state->'working_layer' AS working_layer
               FROM data_products
               WHERE id = $1::uuid""",
            data_product_id,
        )
        if dp_rows:
            row = dp_rows[0]
            snapshot["current_phase"] = row.get("current_phase") or "discovery"
            snapshot["data_tier"] = row.get("data_tier")
            working_layer = row.get("working_layer")
            if isinstance(working_layer, str):
                try:
                    working_layer = json.loads(working_layer)
                except Exception:
                    working_layer = None
            snapshot["transformation_done"] = isinstance(working_layer, dict) and len(working_layer) > 0

        dd_rows = await _q(
            _pool,
            "SELECT 1 FROM data_descriptions WHERE data_product_id = $1::uuid LIMIT 1",
            data_product_id,
        )
        snapshot["data_description_exists"] = bool(dd_rows)

        brd_rows = await _q(
            _pool,
            "SELECT 1 FROM business_requirements WHERE data_product_id = $1::uuid LIMIT 1",
            data_product_id,
        )
        snapshot["brd_exists"] = bool(brd_rows)

        sv_rows = await _q(
            _pool,
            """SELECT validation_status
               FROM semantic_views
               WHERE data_product_id = $1::uuid
               ORDER BY version DESC
               LIMIT 1""",
            data_product_id,
        )
        snapshot["semantic_view_exists"] = bool(sv_rows)
        if sv_rows:
            snapshot["validation_status"] = sv_rows[0].get("validation_status")
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
        already_published = bool(row.get("state_published")) or row.get("status") == "published" or bool(row.get("published_at"))
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
    _stream_firewall_blocks: int = 0    # tokens/runs blocked by supervisor firewall
    _last_tool_name: str | None = None
    _failure_plan_message: str | None = None
    _last_model_end_output_text: str = ""
    # Phase tracking: detect subagent transitions
    _SUBAGENT_PHASE_MAP: dict[str, str] = {
        "discovery-agent": "discovery",
        "transformation-agent": "prepare",      # between discovery and requirements
        "modeling-agent": "generation",         # internal — maps to generation phase
        "model-builder": "requirements",        # default; refined by tool detection
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

        await queue.put({
            "type": "reasoning_update",
            "data": {"message": snippet, "source": source},
        })
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
                await queue.put({
                    "type": "phase_change",
                    "data": {"from": old_phase, "to": supervisor_forced_phase},
                })
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
        publish_approved = (
            is_publish_phase
            and not already_published
            and _is_explicit_publish_approval(message, allow_bare_ack=True)
        )
        # Allow a direct "publish now" style request from validation, but require
        # explicit publish wording (bare "yes" is not enough before publishing phase).
        if (
            not publish_approved
            and publish_phase == "validation"
            and not already_published
            and _is_explicit_publish_approval(message, allow_bare_ack=False)
        ):
            publish_approved = True
        # Post-publish instruction updates (agent behavior/prompt only) are
        # explicit redeploy intents; allow publishing tools for this turn.
        if not publish_approved and post_publish_agent_instruction_update_intent:
            publish_approved = True

        set_publish_approval_context(publish_approved)
        logger.info(
            "Publish approval gate for session %s: phase=%s published=%s approved=%s instruction_update_intent=%s",
            session_id,
            publish_phase,
            already_published,
            publish_approved,
            post_publish_agent_instruction_update_intent,
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
        # Inject supervisor context contract for non-discovery turns only.
        # This keeps subagent routing deterministic without exposing internals to the user.
        if not is_discovery:
            actual_message = _build_supervisor_contract(
                snapshot=workflow_snapshot,
                user_message=actual_message,
                transition_target=supervisor_forced_phase,
                transition_reason=supervisor_transition_reason,
                data_product_id=data_product_id,
                already_published=already_published,
                forced_subagent="publishing-agent" if post_publish_agent_instruction_update_intent else None,
                forced_intent=(
                    "post_publish_agent_instruction_update"
                    if post_publish_agent_instruction_update_intent
                    else None
                ),
            )

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
                        if _previous_run_content and _previous_run_content.startswith(buffered_text.strip()):
                            _run_suppressed = True
                            logger.info("Suppressing short duplicate subagent run")
                        elif _is_internal_reasoning_leak(buffered_text):
                            _run_suppressed = True
                            _stream_firewall_blocks += 1
                            logger.warning("Supervisor firewall blocked short internal run for session %s", session_id)
                        else:
                            current_assistant_content = buffered_text
                            if not _current_run_has_llm_reasoning:
                                await _emit_reasoning_update(current_assistant_content, source="fallback")
                            for tok in _run_token_buffer:
                                await queue.put({
                                    "type": "token",
                                    "data": {"content": tok},
                                })
                        _run_token_buffer = []

                    if _current_run_id is not None and current_assistant_content.strip():
                        _finalized = _sanitize_assistant_text(current_assistant_content).strip()
                        if _finalized:
                            _previous_run_content = _finalized
                            _assistant_texts.append(_finalized)
                        current_assistant_content = ""
                        await queue.put({
                            "type": "message_done",
                            "data": {"content": _finalized},
                        })
                    _current_run_id = run_id
                    # Reset per-run dedup state
                    _run_token_buffer = []
                    _run_suppressed = False
                    _run_dedup_resolved = not (_inside_task and bool(_previous_run_content))
                    _current_run_has_llm_reasoning = False

                # Gate: only emit chunks from subagent runs (inside task tool)
                if not _inside_task:
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
                            logger.info("Suppressing duplicate subagent run (prefix matches previous)")
                        elif _is_internal_reasoning_leak(buffered_text):
                            _run_suppressed = True
                            _stream_firewall_blocks += 1
                            logger.warning("Supervisor firewall blocked buffered internal run for session %s", session_id)
                        else:
                            # Not a duplicate — flush buffered tokens
                            current_assistant_content = buffered_text
                            if not _current_run_has_llm_reasoning:
                                await _emit_reasoning_update(current_assistant_content, source="fallback")
                            for tok in _run_token_buffer:
                                await queue.put({
                                    "type": "token",
                                    "data": {"content": tok},
                                })
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
                await queue.put({
                    "type": "token",
                    "data": {"content": content},
                })

            elif kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                tool_input = data.get("input", {})
                _last_tool_name = tool_name

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
                        old_phase = _current_phase
                        _current_phase = phase_name
                        _pipeline_timer.phase_started(phase_name)
                        await queue.put({
                            "type": "phase_change",
                            "data": {"from": old_phase, "to": phase_name},
                        })
                        logger.info("Phase change: %s → %s (session %s)", old_phase, phase_name, session_id)
                        # Persist current_phase to data_products.state
                        await _persist_phase(data_product_id, phase_name)

                # Model-builder tool-level phase refinement:
                # model-builder is one agent but shows as 3 phases in the UI
                if tool_name == "save_brd" and _current_phase != "requirements":
                    old_phase = _current_phase
                    _current_phase = "requirements"
                    _pipeline_timer.phase_started("requirements")
                    await queue.put({"type": "phase_change", "data": {"from": old_phase, "to": "requirements"}})
                    await _persist_phase(data_product_id, "requirements")
                elif tool_name in ("save_semantic_view", "fetch_documentation") and _current_phase == "requirements":
                    old_phase = _current_phase
                    _current_phase = "generation"
                    _generation_phase_ran = True
                    _pipeline_timer.phase_started("generation")
                    await queue.put({"type": "phase_change", "data": {"from": old_phase, "to": "generation"}})
                    await _persist_phase(data_product_id, "generation")
                elif tool_name == "validate_semantic_view_yaml" and _current_phase != "validation":
                    old_phase = _current_phase
                    _current_phase = "validation"
                    _pipeline_timer.phase_started("validation")
                    await queue.put({"type": "phase_change", "data": {"from": old_phase, "to": "validation"}})
                    await _persist_phase(data_product_id, "validation")

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
                    # Score BRD quality
                    asyncio.create_task(score_brd_quality(_trace_id, data_product_id))

                # Track register_gold_layer for modeling phase + emit lineage artifact
                if tool_name == "register_gold_layer":
                    _gold_layer_registered = True
                    logger.info("register_gold_layer completed for session %s", session_id)
                    # Lineage is written to Neo4j inside register_gold_layer —
                    # emit the lineage artifact event so frontend shows it
                    await queue.put({
                        "type": "artifact",
                        "data": {
                            "artifact_id": str(uuid4()),
                            "artifact_type": "lineage",
                        },
                    })
                    logger.info("Emitted lineage artifact event for session %s", session_id)

                # Emit artifact events for modeling save tools
                _MODELING_TOOL_ARTIFACT_MAP = {
                    "save_data_catalog": "data_catalog",
                    "save_business_glossary": "business_glossary",
                    "save_metrics_definitions": "metrics",
                    "save_validation_rules": "validation_rules",
                    "save_openlineage_artifact": "lineage",
                }
                if tool_name in _MODELING_TOOL_ARTIFACT_MAP:
                    await queue.put({
                        "type": "artifact",
                        "data": {
                            "artifact_id": str(uuid4()),
                            "artifact_type": _MODELING_TOOL_ARTIFACT_MAP[tool_name],
                        },
                    })
                    logger.info("Emitted %s artifact event for session %s", _MODELING_TOOL_ARTIFACT_MAP[tool_name], session_id)

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
                logger.warning("Agent %s: no generations during discovery (summary_len=%d) — fallback will fire in finally",
                               session_id, len(actual_message))
            elif _stream_token_count == 0 and not _subagent_completed and not _assistant_texts:
                logger.warning("Agent %s: no generations and no visible output; emitting recovery plan", session_id)
                _failure_plan_message = _compose_failure_recovery_plan(
                    phase=_current_phase,
                    reason=str(e),
                    timed_out=False,
                    last_tool=_last_tool_name,
                )
            else:
                logger.info("Agent %s produced empty response (expected after subagent delegation)", session_id)
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
                await queue.put({
                    "type": "token",
                    "data": {"content": recovered_text},
                })
                await queue.put({
                    "type": "message_done",
                    "data": {"content": recovered_text},
                })
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
            session_id, _stream_event_count, _stream_llm_calls, _stream_llm_completions,
            _stream_raw_chunks, _stream_gated_out, _stream_dedup_suppressed, _stream_firewall_blocks, _stream_token_count,
            _stream_task_calls, len(_assistant_texts) + (1 if current_assistant_content.strip() else 0),
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
                session_id, _stream_event_count, len(actual_message),
            )
            fallback_msg = (
                "I've analyzed your data and completed the initial profiling. "
                "I found some interesting patterns across the tables. "
                "Could you tell me more about what you're looking to accomplish with this data? "
                "That will help me tailor my analysis to your specific needs."
            )
            current_assistant_content = _sanitize_assistant_text(fallback_msg)
            for token_chunk in [fallback_msg]:
                await queue.put({
                    "type": "token",
                    "data": {"content": _sanitize_assistant_text(token_chunk)},
                })
            await queue.put({
                "type": "message_done",
                "data": {"content": _sanitize_assistant_text(fallback_msg)},
            })

        if _failure_plan_message:
            await queue.put({
                "type": "status",
                "data": {"message": _failure_plan_message},
            })

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
            await queue.put({
                "type": "token",
                "data": {"content": fallback_msg},
            })
            await queue.put({
                "type": "message_done",
                "data": {"content": _sanitize_assistant_text(fallback_msg)},
            })

        # Add final assistant content to local buffer for safety net
        if current_assistant_content.strip():
            safe_final = _sanitize_assistant_text(current_assistant_content)
            if safe_final:
                _assistant_texts.append(safe_final)

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

        # BRD and YAML safety nets removed — model-builder has full context and
        # verification tools (verify_brd_completeness, verify_yaml_against_brd)
        # to self-correct. Langfuse scoring tracks safety_net_activations (should be 0).

        # --- Langfuse: score YAML quality and pipeline duration ---
        if _generation_phase_ran:
            score_yaml_quality(_trace_id, _yaml_passed_first, _yaml_retry_count, _verification_issues)
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

            return {"session_id": session_id, "messages": deduped, "phase": phase}
    except Exception as e:
        logger.error("Failed to get history for session %s: %s", session_id, e)

    return {"session_id": session_id, "messages": [], "phase": "discovery"}
