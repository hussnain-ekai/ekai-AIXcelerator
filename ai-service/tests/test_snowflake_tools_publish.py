"""Tests for Snowflake publishing helper normalization logic."""

import json

from tools import snowflake_tools


def test_resolve_role_for_grant_accepts_current_role_token(monkeypatch) -> None:
    def _fake_execute(sql: str):
        assert "SELECT CURRENT_ROLE()" in sql
        return [{"ROLE_NAME": "ANALYST_ROLE"}]

    monkeypatch.setattr(snowflake_tools, "execute_query_sync", _fake_execute)
    role, err = snowflake_tools._resolve_role_for_grant("CURRENT_ROLE()")
    assert err is None
    assert role == "ANALYST_ROLE"


def test_normalize_fqn_parts_strips_wrapping_quotes() -> None:
    parts, err = snowflake_tools._normalize_fqn_parts(
        '"EKAIX"."WORLD_BANK_MARTS"."WB_AGENT"',
        expected_parts=3,
        labels=["database", "schema", "agent"],
    )
    assert err is None
    assert parts == ["EKAIX", "WORLD_BANK_MARTS", "WB_AGENT"]


def test_query_cortex_agent_parses_agent_run_response(monkeypatch) -> None:
    def _fake_execute(sql: str):
        assert "SNOWFLAKE.CORTEX.AGENT_RUN" in sql
        return [{"RESPONSE": '{"role":"assistant","content":[{"type":"text","text":"Answer text"}]}'}]

    monkeypatch.setattr(snowflake_tools, "execute_query_sync", _fake_execute)
    result = json.loads(
        snowflake_tools.query_cortex_agent.func(
            "EKAIX.WORLD_BANK_MARTS.WB_AGENT",
            "question",
        )
    )
    assert result["status"] == "success"
    assert result["answer"] == "Answer text"


def test_query_cortex_agent_maps_agent_run_error_to_auth(monkeypatch) -> None:
    def _fake_execute(_sql: str):
        return [{
            "RESPONSE": (
                '{"code":"399513","message":"The agent does not exist or access is not authorized '
                'for the current role."}'
            )
        }]

    monkeypatch.setattr(snowflake_tools, "execute_query_sync", _fake_execute)
    result = json.loads(
        snowflake_tools.query_cortex_agent.func(
            "EKAIX.WORLD_BANK_MARTS.WB_AGENT",
            "question",
        )
    )
    assert result["tool"] == "query_cortex_agent"
    assert result["error_type"] == "auth"


def test_execute_rcr_query_auto_remaps_missing_ekaix_table(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_execute(sql: str):
        calls.append(sql)
        if sql.startswith('USE DATABASE "WORLD_BANK"'):
            return []
        if "EKAIX" in sql and "GDP_CURRENT_USD" in sql:
            raise Exception(
                "002003 (42S02): SQL compilation error: "
                "Object 'EKAIX.WORLD_BANK_MACRO_INFRASTRUCTURE_SIGNALS_MARTS.GDP_CURRENT_USD' "
                "does not exist or not authorized."
            )
        if '"WORLD_BANK"."PUBLIC"."GDP_CURRENT_USD"' in sql:
            return [{"COUNTRY_CODE": "ARG"}]
        raise AssertionError(f"Unexpected SQL: {sql}")

    monkeypatch.setattr(snowflake_tools, "execute_query_sync", _fake_execute)
    snowflake_tools.set_data_isolation_context(
        database="WORLD_BANK",
        tables=["WORLD_BANK.PUBLIC.GDP_CURRENT_USD", "WORLD_BANK.PUBLIC.POPULATION_TOTAL"],
    )

    result = json.loads(
        snowflake_tools.execute_rcr_query.func(
            "SELECT * FROM EKAIX.World_Bank_Macro_Infrastructure_Signals_MARTS.GDP_CURRENT_USD LIMIT 5"
        )
    )

    snowflake_tools.set_data_isolation_context(database=None, tables=None)

    assert result["row_count"] == 1
    assert result["autocorrected_from"] == "EKAIX.WORLD_BANK_MACRO_INFRASTRUCTURE_SIGNALS_MARTS.GDP_CURRENT_USD"
    assert result["autocorrected_to"] == "WORLD_BANK.PUBLIC.GDP_CURRENT_USD"
    assert any('"WORLD_BANK"."PUBLIC"."GDP_CURRENT_USD"' in sql for sql in calls)


def test_execute_rcr_query_returns_allowed_tables_hint_for_unknown_object(monkeypatch) -> None:
    def _fake_execute(sql: str):
        if sql.startswith('USE DATABASE "WORLD_BANK"'):
            return []
        raise Exception(
            "002003 (42S02): SQL compilation error: "
            "Object 'EKAIX.WORLD_BANK_MACRO_INFRASTRUCTURE_SIGNALS_MARTS.UNKNOWN_SIGNAL' "
            "does not exist or not authorized."
        )

    monkeypatch.setattr(snowflake_tools, "execute_query_sync", _fake_execute)
    snowflake_tools.set_data_isolation_context(
        database="WORLD_BANK",
        tables=["WORLD_BANK.PUBLIC.GDP_CURRENT_USD", "WORLD_BANK.PUBLIC.POPULATION_TOTAL"],
    )

    result = json.loads(
        snowflake_tools.execute_rcr_query.func(
            "SELECT * FROM EKAIX.WORLD_BANK_MACRO_INFRASTRUCTURE_SIGNALS_MARTS.UNKNOWN_SIGNAL LIMIT 5"
        )
    )

    snowflake_tools.set_data_isolation_context(database=None, tables=None)

    assert result["tool"] == "execute_rcr_query"
    assert result["missing_object"] == "EKAIX.WORLD_BANK_MACRO_INFRASTRUCTURE_SIGNALS_MARTS.UNKNOWN_SIGNAL"
    assert "WORLD_BANK.PUBLIC.GDP_CURRENT_USD" in result["allowed_tables"]
