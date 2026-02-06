"""Explorer subagent — ad-hoc data queries during any conversation phase.

Responsibilities:
    - Answer data questions by running read-only queries
    - Query the ERD graph to show relationships
    - Profile tables or columns on demand
    - Explain data patterns in simple language
"""

from __future__ import annotations

import logging

from agents.prompts import EXPLORER_PROMPT

logger = logging.getLogger(__name__)

# Subagent configuration
EXPLORER_CONFIG = {
    "name": "explorer",
    "system_prompt": EXPLORER_PROMPT,
    "tools": [
        "execute_rcr_query",
        "query_erd_graph",
        "profile_table",
    ],
}
