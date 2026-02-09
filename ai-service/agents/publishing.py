"""Publishing subagent — deploys semantic view and Cortex Agent to Snowflake.

Responsibilities:
    - Load validated YAML
    - Request explicit user approval before publishing
    - Create semantic view in target schema
    - Create Cortex Agent referencing the semantic view
    - Grant access to caller's role
    - Append data quality disclaimer to agent system prompt
    - Log all actions to audit trail
"""

from __future__ import annotations

import logging
from typing import Any

from agents.prompts import PUBLISHING_PROMPT

logger = logging.getLogger(__name__)

DATA_QUALITY_DISCLAIMER = (
    "IMPORTANT: This Cortex Agent is powered by a semantic model created by ekaiX. "
    "The accuracy of responses depends on the quality of the underlying source data. "
    "Always verify critical business decisions against the original data sources."
)


def create_publish_summary(
    yaml_name: str,
    table_count: int,
    fact_count: int,
    dimension_count: int,
    metric_count: int,
    relationship_count: int = 0,
    health_score: int | None = None,
) -> str:
    """Create a human-readable summary for the approval request.

    Args:
        yaml_name: Name of the semantic model
        table_count: Number of tables referenced
        fact_count: Number of facts defined
        dimension_count: Number of dimensions defined
        metric_count: Number of metrics defined
        relationship_count: Number of relationships defined
        health_score: Data quality health score
    """
    lines = [
        f"Semantic Model: {yaml_name}",
        f"Tables: {table_count}",
        f"Facts: {fact_count} row-level data points",
        f"Dimensions: {dimension_count} grouping options",
        f"Metrics: {metric_count} calculated measures",
    ]

    if relationship_count > 0:
        lines.append(f"Relationships: {relationship_count} table connections")

    if health_score is not None:
        quality = "Healthy" if health_score >= 70 else "Needs Attention" if health_score >= 40 else "Critical"
        lines.append(f"Data Quality: {quality} ({health_score}%)")

    return "\n".join(lines)


def create_approval_request(summary: str) -> dict[str, Any]:
    """Create an approval request event to send to the frontend."""
    return {
        "type": "approval_request",
        "data": {
            "action": "publish_cortex_agent",
            "summary": summary,
            "disclaimer": DATA_QUALITY_DISCLAIMER,
            "message": (
                "I'm ready to publish this semantic model and create an AI agent. "
                "Please review the summary above and confirm to proceed."
            ),
        },
    }


# Subagent configuration
PUBLISHING_CONFIG = {
    "name": "publishing",
    "system_prompt": PUBLISHING_PROMPT,
    "tools": [
        "get_latest_semantic_view",
        "create_semantic_view",
        "create_cortex_agent",
        "grant_agent_access",
        "log_agent_action",
        "upload_artifact",
    ],
}
