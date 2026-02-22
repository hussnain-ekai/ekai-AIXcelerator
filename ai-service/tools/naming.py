"""Shared naming utilities for EKAIX database schema derivation.

All ekaiX-created objects (VIEWs, Dynamic Tables, Semantic Views, Cortex Agents)
live in a dedicated EKAIX database. Each data product gets two schemas:
    - {DP_NAME}_CURATED  — VIEWs referencing source tables (was "silver")
    - {DP_NAME}_MARTS    — Dynamic Tables, Semantic View, Agent (was "gold")
"""

import logging
import re

from services.snowflake import get_connection

logger = logging.getLogger(__name__)

EKAIX_DATABASE = "EKAIX"

_ekaix_db_ensured = False


def sanitize_dp_name(dp_name: str) -> str:
    """Data product name -> Snowflake-safe schema component.

    Spaces -> _, strip special chars, collapse underscores, uppercase, max 200 chars.
    """
    result = dp_name.strip().replace(" ", "_")
    result = re.sub(r"[^A-Za-z0-9_]", "", result)
    result = re.sub(r"_+", "_", result).strip("_").upper()
    return result[:200] or "DEFAULT"


def curated_schema(dp_name: str) -> str:
    """Return the curated (VIEW) schema name for a data product."""
    return f"{sanitize_dp_name(dp_name)}_CURATED"


def marts_schema(dp_name: str) -> str:
    """Return the marts (Dynamic Table / Semantic View) schema name."""
    return f"{sanitize_dp_name(dp_name)}_MARTS"


def ensure_ekaix_database() -> None:
    """Create the EKAIX database if it doesn't already exist."""
    global _ekaix_db_ensured
    if _ekaix_db_ensured:
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f'CREATE DATABASE IF NOT EXISTS "{EKAIX_DATABASE}"')
    cur.close()
    _ekaix_db_ensured = True


def ensure_schema(schema_name: str) -> None:
    """Create a schema inside the EKAIX database if it doesn't exist."""
    ensure_ekaix_database()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{EKAIX_DATABASE}"."{schema_name}"')
    cur.close()
