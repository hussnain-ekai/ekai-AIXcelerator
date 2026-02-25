"""Tests for supervisor-level workflow guards and output sanitization."""

from services.supervisor_guardrails import (
    build_failure_recovery_message,
    build_supervisor_contract,
    classify_failure_category,
    evaluate_supervisor_transition,
    is_end_to_end_autopilot_intent,
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


def test_end_to_end_autopilot_intent_detects_no_pause_requests() -> None:
    assert is_end_to_end_autopilot_intent(
        "Proceed end-to-end now and do not stop for confirmation."
    )
    assert is_end_to_end_autopilot_intent(
        "Run full workflow without pause and publish when done."
    )


def test_end_to_end_autopilot_intent_rejects_general_requests() -> None:
    assert not is_end_to_end_autopilot_intent("Proceed to requirements.")
    assert not is_end_to_end_autopilot_intent("Show me the generated BRD.")


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


def test_sanitize_assistant_text_rewrites_modeling_jargon() -> None:
    text = (
        "I have generated the semantic model based on your requirements. "
        "It covers 2 tables with 1 fact table, 2 dimension tables, "
        "1 time dimension, and 1 metric. The YAML is ready for review."
    )
    cleaned = sanitize_assistant_text(text)
    assert "semantic model" not in cleaned.lower()
    assert "fact table" not in cleaned.lower()
    assert "dimension table" not in cleaned.lower()
    assert "time dimension" not in cleaned.lower()
    assert "YAML" not in cleaned
    assert "data model" in cleaned.lower()
    assert "core data" in cleaned.lower()
    assert "reference data" in cleaned.lower()
    assert "time period" in cleaned.lower()
    assert "model definition" in cleaned.lower()


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


def test_supervisor_contract_includes_autopilot_and_publish_flags() -> None:
    contract = build_supervisor_contract(
        snapshot={"current_phase": "requirements"},
        user_message="Proceed end-to-end without pause.",
        transition_target=None,
        transition_reason=None,
        run_mode="autopilot_end_to_end",
        publish_preapproved=True,
    )
    assert "run_mode=autopilot_end_to_end" in contract
    assert "pause_policy=skip_optional_review_pauses" in contract
    assert "publish_approval=preapproved" in contract


def test_supervisor_contract_includes_product_type_and_documents() -> None:
    contract = build_supervisor_contract(
        snapshot={
            "current_phase": "requirements",
            "product_type": "document",
            "has_documents": True,
            "brd_exists": True,
            "data_product_name": "FDA Drug Safety Manuals",
            "target_schema_marts": "EKAIX.FDA_DRUG_SAFETY_MANUALS_MARTS",
            "target_schema_docs": "EKAIX.FDA_DRUG_SAFETY_MANUALS_DOCS",
        },
        user_message="Publish the search agent.",
        transition_target=None,
        transition_reason=None,
    )
    assert "product_type=document" in contract
    assert "has_documents=True" in contract
    assert "brd_exists=True" in contract
    assert "data_product_name=FDA Drug Safety Manuals" in contract
    assert "target_schema_marts=EKAIX.FDA_DRUG_SAFETY_MANUALS_MARTS" in contract
    assert "target_schema_docs=EKAIX.FDA_DRUG_SAFETY_MANUALS_DOCS" in contract
