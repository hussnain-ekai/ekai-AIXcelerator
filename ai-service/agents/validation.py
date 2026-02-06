"""Validation subagent — tests generated YAML against real Snowflake data.

Responsibilities:
    - Verify table and column existence via RCR
    - Run EXPLAIN on generated SQL to check compilation
    - Execute measure aggregations and verify results
    - Validate join cardinality (1:1, 1:N, N:M)
    - Check for orphaned keys in joins
    - Generate validation report (pass/warning/fail)
"""

from __future__ import annotations

import logging
from typing import Any

from agents.prompts import VALIDATION_PROMPT

logger = logging.getLogger(__name__)


def create_validation_report() -> dict[str, Any]:
    """Create an empty validation report structure.

    Returns:
        Empty validation report dict
    """
    return {
        "status": "pending",
        "checks": [],
        "errors": [],
        "warnings": [],
        "passed": [],
    }


def add_check_result(
    report: dict[str, Any],
    check_name: str,
    passed: bool,
    message: str,
    severity: str = "error",
) -> None:
    """Add a check result to the validation report.

    Args:
        report: The validation report dict (mutated)
        check_name: Name of the check
        passed: Whether the check passed
        message: Description of the result
        severity: 'error', 'warning', or 'info'
    """
    result = {
        "name": check_name,
        "passed": passed,
        "message": message,
        "severity": severity,
    }
    report["checks"].append(result)

    if passed:
        report["passed"].append(result)
    elif severity == "error":
        report["errors"].append(result)
    else:
        report["warnings"].append(result)


def compute_validation_status(report: dict[str, Any]) -> str:
    """Determine overall validation status from check results.

    Args:
        report: The validation report with check results

    Returns:
        'valid', 'warning', or 'invalid'
    """
    if report["errors"]:
        return "invalid"
    if report["warnings"]:
        return "warning"
    return "valid"


# Subagent configuration
VALIDATION_CONFIG = {
    "name": "validation",
    "system_prompt": VALIDATION_PROMPT,
    "tools": [
        "validate_sql",
        "execute_rcr_query",
        "save_semantic_view",
        "upload_artifact",
    ],
}
