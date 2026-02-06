"""Requirements subagent — interactive BRD capture through conversation.

Responsibilities:
    - Review the ERD from Discovery to understand available data
    - Ask questions one at a time to capture business requirements
    - Show actual data examples during conversation
    - Build the BRD incrementally, confirming each section
    - Maximum 15 conversation turns
"""

from __future__ import annotations

import logging
from typing import Any

from agents.prompts import REQUIREMENTS_PROMPT
from config import get_settings

logger = logging.getLogger(__name__)


def _get_max_conversation_turns() -> int:
    return get_settings().max_requirements_turns


def create_empty_brd() -> dict[str, Any]:
    """Create an empty BRD structure.

    Returns:
        Empty BRD dict with all required sections
    """
    return {
        "measures": [],
        "dimensions": [],
        "time_dimensions": [],
        "filters": [],
        "business_rules": [],
        "joins": [],
        "metadata": {
            "version": 1,
            "turns_used": 0,
            "is_complete": False,
        },
    }


def validate_brd(brd: dict[str, Any]) -> list[str]:
    """Validate that a BRD has minimum required content.

    Args:
        brd: The BRD dict to validate

    Returns:
        List of validation issues (empty if valid)
    """
    issues: list[str] = []

    if not brd.get("measures"):
        issues.append("At least one measure is required")

    if not brd.get("dimensions"):
        issues.append("At least one dimension is required")

    if not brd.get("time_dimensions"):
        issues.append("At least one time dimension is recommended")

    return issues


def is_turn_limit_reached(brd: dict[str, Any]) -> bool:
    """Check if the maximum conversation turns have been reached.

    Args:
        brd: The BRD dict with metadata

    Returns:
        True if the turn limit is reached
    """
    metadata = brd.get("metadata", {})
    turns_used = metadata.get("turns_used", 0)
    return turns_used >= _get_max_conversation_turns()


# Subagent configuration
REQUIREMENTS_CONFIG = {
    "name": "requirements",
    "system_prompt": REQUIREMENTS_PROMPT,
    "tools": [
        "query_erd_graph",
        "execute_rcr_query",
        "save_brd",
        "upload_artifact",
    ],
}
