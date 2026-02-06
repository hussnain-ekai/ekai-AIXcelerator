"""Generation subagent — semantic view YAML generation from BRD.

Responsibilities:
    - Load the BRD from the requirements phase
    - Load the ERD to verify table/column references
    - Generate Snowflake semantic view YAML
    - Ensure all table references use fully qualified names
    - Verify all column references exist in the ERD graph
"""

from __future__ import annotations

import logging
from typing import Any

from agents.prompts import GENERATION_PROMPT

logger = logging.getLogger(__name__)


def generate_yaml_skeleton(brd: dict[str, Any], erd_tables: list[dict[str, Any]]) -> str:
    """Generate a YAML skeleton from BRD and ERD data.

    This creates the initial YAML structure that the LLM agent will refine.

    Args:
        brd: The Business Requirements Document
        erd_tables: Table metadata from the ERD graph

    Returns:
        YAML string skeleton
    """
    lines: list[str] = []
    lines.append("name: semantic_model")
    lines.append("tables:")

    for join in brd.get("joins", []):
        fact_table = join.get("fact", "")
        if fact_table:
            lines.append(f"  - name: {fact_table}")

            table_measures = [
                m for m in brd.get("measures", []) if m.get("table", "") == fact_table
            ]
            if table_measures:
                lines.append("    measures:")
                for measure in table_measures:
                    lines.append(f"      - name: {measure.get('name', '')}")
                    lines.append(f"        expr: \"{measure.get('expression', '')}\"")
                    lines.append(f"        description: \"{measure.get('description', '')}\"")

            table_dims = [
                d for d in brd.get("dimensions", []) if d.get("table", "") == fact_table
            ]
            if table_dims:
                lines.append("    dimensions:")
                for dim in table_dims:
                    lines.append(f"      - name: {dim.get('name', '')}")
                    lines.append(f"        expr: {dim.get('column', '')}")
                    lines.append(f"        description: \"{dim.get('description', '')}\"")

            table_time_dims = [
                td for td in brd.get("time_dimensions", []) if td.get("table", "") == fact_table
            ]
            if table_time_dims:
                lines.append("    time_dimensions:")
                for td in table_time_dims:
                    lines.append(f"      - name: {td.get('name', '')}")
                    lines.append(f"        expr: {td.get('column', '')}")
                    lines.append(f"        description: \"{td.get('description', '')}\"")

    if brd.get("filters"):
        lines.append("filters:")
        for f in brd["filters"]:
            lines.append(f"  - name: {f.get('name', '')}")
            lines.append(f"    expr: \"{f.get('expression', '')}\"")

    return "\n".join(lines)


# Subagent configuration
GENERATION_CONFIG = {
    "name": "generation",
    "system_prompt": GENERATION_PROMPT,
    "tools": [
        "query_erd_graph",
        "load_workspace_state",
        "save_semantic_view",
        "upload_artifact",
    ],
}
