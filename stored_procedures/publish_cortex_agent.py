"""
ekaiX Snowflake Stored Procedure: publish_cortex_agent

Deploys a semantic view and Cortex Agent to Snowflake Intelligence.
Requires elevated privileges (EXECUTE AS OWNER) to CREATE objects.

Usage:
    CALL ekaix.procedures.publish_cortex_agent(
        '<yaml_content>',
        'my_agent',
        'MY_DB.MY_SCHEMA',
        'ANALYST_ROLE'
    );

Returns: VARIANT with agent FQN, URL, access grants.
"""

import json
from typing import Any


def publish_cortex_agent(
    session: Any,
    yaml_content: str,
    agent_name: str,
    target_schema: str,
    caller_role: str,
) -> str:
    """
    Publish a semantic view and Cortex Agent to Snowflake Intelligence.

    This procedure executes with OWNER's rights to create database objects,
    then grants access back to the caller's role.

    Args:
        session: Snowpark Session (injected by Snowflake)
        yaml_content: Validated semantic view YAML
        agent_name: Name for the Cortex Agent
        target_schema: Target schema (DB.SCHEMA) for created objects
        caller_role: The caller's Snowflake role to grant access to

    Returns:
        JSON string with publication results
    """
    result: dict[str, Any] = {
        "success": False,
        "semantic_view_fqn": "",
        "agent_fqn": "",
        "grants": [],
        "errors": [],
    }

    sv_name = f"{agent_name}_semantic_view"
    sv_fqn = f"{target_schema}.{sv_name}"
    agent_fqn = f"{target_schema}.{agent_name}"

    # Append data quality disclaimer to agent system prompt
    data_quality_disclaimer = (
        "IMPORTANT: This Cortex Agent is powered by a semantic model created by ekaiX. "
        "The accuracy of responses depends on the quality of the underlying source data. "
        "Always verify critical business decisions against the original data sources."
    )

    try:
        # 1. Create Semantic View
        session.sql(f"""
            CREATE OR REPLACE SEMANTIC VIEW {sv_fqn}
            AS $${yaml_content}$$
        """).collect()
        result["semantic_view_fqn"] = sv_fqn

        # 2. Create Cortex Agent
        session.sql(f"""
            CREATE OR REPLACE CORTEX AGENT {agent_fqn}
            SEMANTIC_VIEWS = ('{sv_fqn}')
            SYSTEM_PROMPT = '{data_quality_disclaimer}'
        """).collect()
        result["agent_fqn"] = agent_fqn

        # 3. Grant access to caller's role
        grants = [
            f"GRANT USAGE ON SEMANTIC VIEW {sv_fqn} TO ROLE {caller_role}",
            f"GRANT USAGE ON CORTEX AGENT {agent_fqn} TO ROLE {caller_role}",
        ]

        for grant_sql in grants:
            try:
                session.sql(grant_sql).collect()
                result["grants"].append(grant_sql)
            except Exception as e:
                result["errors"].append(f"Grant failed: {e!s}")

        result["success"] = True
        result["agent_url"] = (
            f"https://app.snowflake.com/intelligence/agent/{agent_fqn}"
        )

    except Exception as e:
        result["errors"].append(f"Publication failed: {e!s}")

        # Attempt cleanup on failure
        try:
            session.sql(f"DROP CORTEX AGENT IF EXISTS {agent_fqn}").collect()
        except Exception:
            pass
        try:
            session.sql(f"DROP SEMANTIC VIEW IF EXISTS {sv_fqn}").collect()
        except Exception:
            pass

    return json.dumps(result)


# -- Snowflake CREATE PROCEDURE DDL --
# CREATE OR REPLACE PROCEDURE ekaix.procedures.publish_cortex_agent(
#     yaml_content VARCHAR,
#     agent_name VARCHAR,
#     target_schema VARCHAR,
#     caller_role VARCHAR
# )
# RETURNS VARIANT
# LANGUAGE PYTHON
# RUNTIME_VERSION = '3.11'
# PACKAGES = ('snowflake-snowpark-python')
# HANDLER = 'publish_cortex_agent'
# EXECUTE AS OWNER;
