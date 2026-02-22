"""Tests for supervisor-level workflow guards and output sanitization."""

from services.supervisor_guardrails import (
    build_failure_recovery_message,
    classify_failure_category,
    evaluate_supervisor_transition,
    is_internal_reasoning_leak,
    is_requirements_transition_intent,
    requirements_entry_ready,
    sanitize_assistant_text,
)


def test_requirements_transition_intent_detects_explicit_requests() -> None:
    assert is_requirements_transition_intent("Let's move to requirements.")
    assert is_requirements_transition_intent("Proceed to BRD now")
    assert is_requirements_transition_intent("please proceed")


def test_requirements_transition_intent_rejects_unrelated_text() -> None:
    assert not is_requirements_transition_intent("Show sample records first")
    assert not is_requirements_transition_intent("")


def test_requirements_entry_ready_requires_discovery_and_cleanup_when_needed() -> None:
    ready, reason = requirements_entry_ready(
        {
            "data_description_exists": True,
            "data_tier": "gold",
            "transformation_done": False,
        }
    )
    assert ready
    assert "satisfied" in reason.lower()

    ready, reason = requirements_entry_ready(
        {
            "data_description_exists": True,
            "data_tier": "silver",
            "transformation_done": False,
        }
    )
    assert not ready
    assert "cleanup" in reason.lower()


def test_supervisor_transition_moves_to_requirements_when_ready() -> None:
    target, reason = evaluate_supervisor_transition(
        "Looks good. Please move to requirements.",
        {
            "current_phase": "discovery",
            "data_description_exists": True,
            "data_tier": "gold",
            "transformation_done": False,
        },
    )
    assert target == "requirements"
    assert reason is not None


def test_supervisor_transition_blocks_when_cleanup_missing() -> None:
    target, reason = evaluate_supervisor_transition(
        "Proceed to requirements",
        {
            "current_phase": "discovery",
            "data_description_exists": True,
            "data_tier": "bronze",
            "transformation_done": False,
        },
    )
    assert target is None
    assert reason is not None
    assert "cleanup" in reason.lower()


def test_internal_leak_detector_flags_orchestration_text() -> None:
    assert is_internal_reasoning_leak("I will call task() now")
    assert is_internal_reasoning_leak("Rule 6 says pause")
    assert not is_internal_reasoning_leak("Your data quality looks strong")


def test_sanitize_assistant_text_removes_internal_lines_and_rewrites_jargon() -> None:
    text = (
        "The transformation plan is ready.\n"
        "I will call task() now.\n"
        "This includes SQL and VARCHAR checks.\n"
    )
    cleaned = sanitize_assistant_text(text)
    assert "task()" not in cleaned
    assert "SQL" not in cleaned
    assert "VARCHAR" not in cleaned
    assert "query logic" in cleaned.lower()
    assert "text" in cleaned.lower()


def test_classify_failure_category_timeout_takes_priority() -> None:
    assert classify_failure_category("warehouse timeout", timed_out=True) == "timeout"


def test_build_failure_recovery_message_contains_phase_plan_and_checkpoint_note() -> None:
    msg = build_failure_recovery_message(
        phase="validation",
        category="validation",
        reason="Model validation did not complete.",
        last_tool="validate_semantic_view_yaml",
    )
    assert "validation step" in msg.lower()
    assert "recovery plan" in msg.lower()
    assert "last attempted operation" in msg.lower()
    assert "checkpoint" in msg.lower()
