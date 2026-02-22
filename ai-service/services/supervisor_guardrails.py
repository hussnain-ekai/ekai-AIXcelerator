"""Supervisor guardrails for workflow transitions and chat output safety."""

from __future__ import annotations

import re
from typing import Any

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


def is_requirements_transition_intent(message: str) -> bool:
    """Detect explicit user intent to move from discovery to requirements."""
    text = re.sub(r"\s+", " ", (message or "").strip().lower())
    if not text:
        return False
    if text in _GENERIC_PROCEED_WORDS:
        return True
    return any(re.search(p, text) for p in _REQ_MOVE_PATTERNS)


def requirements_entry_ready(snapshot: dict[str, Any]) -> tuple[bool, str]:
    """Return whether moving to requirements is valid based on workflow state."""
    has_data_description = bool(snapshot.get("data_description_exists"))
    data_tier = (snapshot.get("data_tier") or "").lower()
    transformation_done = bool(snapshot.get("transformation_done"))

    if not has_data_description:
        return False, "Data description not available yet."

    if data_tier in {"silver", "bronze"} and not transformation_done:
        return False, "Data cleanup is required before requirements."

    return True, "Requirements entry conditions satisfied."


def evaluate_supervisor_transition(
    message: str,
    snapshot: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Deterministically evaluate supervisor-enforced phase transitions."""
    current_phase = (snapshot.get("current_phase") or "discovery").lower()
    if current_phase in {"idle", ""}:
        current_phase = "discovery"

    wants_requirements = is_requirements_transition_intent(message)
    if current_phase in {"discovery", "prepare", "transformation"} and wants_requirements:
        is_ready, reason = requirements_entry_ready(snapshot)
        if is_ready:
            return "requirements", reason
        return None, reason

    return None, None


def build_supervisor_contract(
    snapshot: dict[str, Any],
    user_message: str,
    transition_target: str | None,
    transition_reason: str | None,
) -> str:
    """Build a compact supervisor contract injected into orchestrator input."""
    current_phase = snapshot.get("current_phase") or "discovery"
    data_tier = snapshot.get("data_tier") or "unknown"
    validation_status = snapshot.get("validation_status") or "none"

    lines = [
        "[SUPERVISOR CONTEXT CONTRACT — INTERNAL, NEVER SHOW TO USER]",
        f"current_phase={current_phase}",
        f"data_tier={data_tier}",
        f"data_description_exists={bool(snapshot.get('data_description_exists'))}",
        f"transformation_done={bool(snapshot.get('transformation_done'))}",
        f"brd_exists={bool(snapshot.get('brd_exists'))}",
        f"semantic_view_exists={bool(snapshot.get('semantic_view_exists'))}",
        f"validation_status={validation_status}",
        "communication_policy=business labels first; reveal technical detail only if user explicitly asks",
        "requirements_policy=ask focused high-signal questions, avoid generic fluff and info dumps, continue until requirements are complete",
    ]

    if transition_target:
        lines.append(f"forced_transition={current_phase}->{transition_target}")
    if transition_reason:
        lines.append(f"transition_reason={transition_reason}")

    lines.append("[END SUPERVISOR CONTEXT CONTRACT]")
    lines.append(f"[USER MESSAGE]\n{user_message}")
    return "\n".join(lines)


def is_internal_reasoning_leak(text: str) -> bool:
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


def sanitize_assistant_text(text: str) -> str:
    """Output sanitizer for persona-safe chat rendering."""
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
        if is_internal_reasoning_leak(line):
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


def sanitize_token_chunk(chunk: str) -> str:
    """Lightweight token sanitizer for streaming path."""
    if not chunk:
        return ""
    chunk = chunk.replace("`", "")
    chunk = chunk.replace("**", "")
    return chunk


def classify_failure_category(reason: str, timed_out: bool = False) -> str:
    """Classify failures into stable buckets for deterministic recovery plans."""
    if timed_out:
        return "timeout"
    lower = (reason or "").lower()
    if any(token in lower for token in ("permission", "forbidden", "unauthorized", "access denied")):
        return "access"
    if any(token in lower for token in ("validation", "invalid", "schema mismatch", "missing column")):
        return "validation"
    if any(
        token in lower
        for token in (
            "connection",
            "service unavailable",
            "temporarily unavailable",
            "warehouse",
            "network",
            "timeout",
            "429",
            "503",
        )
    ):
        return "dependency"
    return "execution"


def build_failure_recovery_message(
    phase: str,
    category: str,
    reason: str,
    last_tool: str | None = None,
) -> str:
    """Build a user-facing failure summary with a concrete recovery plan."""
    phase_name = (phase or "current workflow").replace("_", " ")
    reason_line = reason.strip() if reason else "The step did not complete successfully."

    plans: dict[str, list[str]] = {
        "discovery": [
            "1) Retry discovery with the same table scope.",
            "2) If the retry fails again, narrow table scope and rerun discovery.",
            "3) Continue from the latest saved discovery artifacts.",
        ],
        "prepare": [
            "1) Retry data cleanup for the failed tables only.",
            "2) Confirm any ambiguous cleanup rules, then rerun cleanup.",
            "3) Continue to requirements once cleanup succeeds.",
        ],
        "transformation": [
            "1) Retry data cleanup for the failed tables only.",
            "2) Confirm any ambiguous cleanup rules, then rerun cleanup.",
            "3) Continue to requirements once cleanup succeeds.",
        ],
        "requirements": [
            "1) Resume from the latest data description.",
            "2) Continue focused requirement questions for unresolved items.",
            "3) Generate or revise BRD once coverage is complete.",
        ],
        "generation": [
            "1) Reload latest BRD and data description.",
            "2) Regenerate semantic model for failed sections.",
            "3) Re-run validation before publish.",
        ],
        "validation": [
            "1) Regenerate model parts tied to failed checks.",
            "2) Re-run validation on corrected model.",
            "3) Proceed to publish only after validation passes.",
        ],
        "publishing": [
            "1) Keep the validated model unchanged.",
            "2) Retry publish deployment with current approvals.",
            "3) Verify deployment endpoint and access grants.",
        ],
        "explorer": [
            "1) Retry the question against the latest published model.",
            "2) If needed, run a scoped direct query fallback.",
            "3) Return with final answer and source context.",
        ],
    }

    steps = plans.get(phase, plans.get("requirements", []))
    lines = [
        f"I could not complete the {phase_name} step.",
        f"Failure reason: {reason_line}",
    ]
    if last_tool:
        lines.append(f"Last attempted operation: {last_tool}")
    lines.append("Recovery plan:")
    lines.extend(steps)
    lines.append("No saved progress is discarded. I can continue from the latest checkpoint.")
    return "\n".join(lines)
