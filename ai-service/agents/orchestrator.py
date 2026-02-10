"""Main Deep Agents orchestrator with 6 specialized subagents.

Uses `create_deep_agent` from the deepagents library to build a LangGraph-based
agent that delegates to specialized subagents based on conversation phase:
    - Discovery: Profiles schemas, detects PKs/FKs, builds ERD
    - Requirements: Adaptive BRD capture (confidence-based, 1-4 question rounds)
    - Generation: Creates semantic view YAML from BRD
    - Validation: Tests generated YAML against real data via RCR
    - Publishing: Deploys semantic view + Cortex Agent
    - Explorer: Ad-hoc data queries during conversation
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from agents.prompts import (
    DISCOVERY_PROMPT,
    EXPLORER_PROMPT,
    GENERATION_PROMPT,
    ORCHESTRATOR_PROMPT,
    PUBLISHING_PROMPT,
    REQUIREMENTS_PROMPT,
    VALIDATION_PROMPT,
    sanitize_prompt_for_azure,
)

logger = logging.getLogger(__name__)

# Tools organized by subagent
_discovery_tools: list[Any] = []
_requirements_tools: list[Any] = []
_generation_tools: list[Any] = []
_validation_tools: list[Any] = []
_publishing_tools: list[Any] = []
_explorer_tools: list[Any] = []


def _load_tools() -> None:
    """Load all LangChain tools from the tools modules."""
    global _discovery_tools, _requirements_tools, _generation_tools
    global _validation_tools, _publishing_tools, _explorer_tools

    from tools.snowflake_tools import (
        compute_quality_score,
        create_cortex_agent,
        create_semantic_view,
        execute_rcr_query,
        grant_agent_access,
        profile_table,
        query_cortex_agent,
        query_information_schema,
        validate_semantic_view_yaml,
        validate_sql,
    )
    from tools.neo4j_tools import (
        classify_entity,
        get_relationship_path,
        query_erd_graph,
        update_erd,
    )
    from tools.postgres_tools import (
        get_latest_brd,
        get_latest_data_description,
        get_latest_semantic_view,
        load_workspace_state,
        log_agent_action,
        save_brd,
        save_data_description,
        save_quality_report,
        save_semantic_view,
        save_workspace_state,
        update_validation_status,
    )
    from tools.minio_tools import (
        list_artifacts,
        retrieve_artifact,
        upload_artifact,
    )
    from tools.discovery_tools import build_erd_from_description

    # Discovery Agent tools — conversational discovery + Data Description + ERD building
    _discovery_tools = [
        execute_rcr_query,
        query_erd_graph,
        save_data_description,
        get_latest_data_description,
        upload_artifact,
        build_erd_from_description,
    ]

    # Requirements Agent tools — NO execute_rcr_query (discovery context has all
    # field analysis; ad-hoc queries distract from BRD generation)
    _requirements_tools = [
        query_erd_graph,
        save_brd,
        upload_artifact,
        get_latest_brd,
        get_latest_data_description,
    ]

    from tools.web_tools import fetch_documentation

    # Generation Agent tools
    _generation_tools = [
        get_latest_brd,
        get_latest_data_description,
        get_latest_semantic_view,
        query_erd_graph,
        save_semantic_view,
        upload_artifact,
        execute_rcr_query,
        fetch_documentation,
    ]

    # Validation Agent tools (no upload_artifact — validation only validates, doesn't create artifacts)
    _validation_tools = [
        get_latest_semantic_view,
        get_latest_brd,
        validate_semantic_view_yaml,
        validate_sql,
        execute_rcr_query,
        update_validation_status,
        fetch_documentation,
    ]

    # Publishing Agent tools
    _publishing_tools = [
        get_latest_semantic_view,
        create_semantic_view,
        create_cortex_agent,
        grant_agent_access,
        log_agent_action,
        upload_artifact,
    ]

    # Explorer Agent tools
    _explorer_tools = [
        execute_rcr_query,
        query_erd_graph,
        profile_table,
        query_cortex_agent,
        get_latest_semantic_view,
        get_latest_brd,
    ]


def _build_subagents(model: Any, sanitize: bool = False) -> list[dict[str, Any]]:
    """Build the 6 subagent configurations for create_deep_agent.

    Args:
        model: The LangChain chat model to use for all subagents (same as orchestrator).
        sanitize: If True, apply Azure content-filter sanitization to all prompts.
    """
    _load_tools()

    _s = sanitize_prompt_for_azure if sanitize else lambda p: p

    return [
        {
            "name": "discovery-agent",
            "description": (
                "Interprets pre-computed discovery results and engages the user "
                "in multi-turn conversation about their data. Generates a Data Description "
                "document capturing business context, then builds the ERD. May take 1-3 rounds "
                "of questions before generating. Use when starting a new data product or "
                "when the user has questions about discovered data."
            ),
            "system_prompt": _s(DISCOVERY_PROMPT),
            "tools": _discovery_tools,
            "model": model,
        },
        {
            "name": "requirements-agent",
            "description": (
                "Captures business requirements through intelligent clarifying questions, "
                "then generates a comprehensive Business Requirements Document. "
                "Adaptively decides when enough information has been gathered — "
                "may ask 1-4 rounds of questions before generating the BRD. "
                "Use immediately after the user responds to the discovery analysis — "
                "do NOT wait for the user to explicitly ask for requirements."
            ),
            "system_prompt": _s(REQUIREMENTS_PROMPT),
            "tools": _requirements_tools,
            "model": model,
        },
        {
            "name": "generation-agent",
            "description": (
                "Generates Snowflake semantic view YAML from BRD and ERD. "
                "Uses fully qualified table names, verifies column existence. "
                "Use after requirements capture is complete."
            ),
            "system_prompt": _s(GENERATION_PROMPT),
            "tools": _generation_tools,
            "model": model,
        },
        {
            "name": "validation-agent",
            "description": (
                "Validates generated semantic view YAML against real Snowflake data. "
                "Runs EXPLAIN, checks cardinality, nulls, ranges. "
                "Use after YAML generation to verify correctness."
            ),
            "system_prompt": _s(VALIDATION_PROMPT),
            "tools": _validation_tools,
            "model": model,
        },
        {
            "name": "publishing-agent",
            "description": (
                "Deploys semantic views and Cortex Agents to Snowflake Intelligence. "
                "Requires explicit user approval before publishing. "
                "Use after validation passes to make the model available."
            ),
            "system_prompt": _s(PUBLISHING_PROMPT),
            "tools": _publishing_tools,
            "model": model,
        },
        {
            "name": "explorer-agent",
            "description": (
                "Answers ad-hoc data questions by running read-only queries against "
                "Snowflake and the ERD graph. Available in any phase. "
                "Use when the user asks data exploration questions."
            ),
            "system_prompt": _s(EXPLORER_PROMPT),
            "tools": _explorer_tools,
            "model": model,
        },
    ]


# Module-level singletons (async init — can't use @lru_cache)
_orchestrator: CompiledStateGraph | None = None
_checkpointer: Any = None  # AsyncPostgresSaver
_checkpointer_pool: Any = None  # psycopg_pool.AsyncConnectionPool


async def get_checkpointer() -> Any:
    """Return (and lazily create) the shared AsyncPostgresSaver checkpointer.

    Uses a psycopg AsyncConnectionPool for connection management.
    Creates checkpoint tables on first call.
    """
    global _checkpointer, _checkpointer_pool

    if _checkpointer is not None:
        return _checkpointer

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    from config import get_settings

    settings = get_settings()
    # Convert asyncpg-style URL to psycopg-style (postgresql:// → postgresql://)
    # Both use the same scheme, but psycopg needs the conninfo format
    conn_string = settings.database_url
    if conn_string.startswith("postgres://"):
        conn_string = "postgresql://" + conn_string[len("postgres://"):]

    _checkpointer_pool = AsyncConnectionPool(
        conninfo=conn_string,
        min_size=2,
        max_size=5,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    await _checkpointer_pool.open()

    _checkpointer = AsyncPostgresSaver(_checkpointer_pool)
    await _checkpointer.setup()
    logger.info("PostgreSQL checkpointer initialized (checkpoint tables ready)")
    return _checkpointer


async def close_checkpointer() -> None:
    """Close the checkpointer's connection pool. Called on shutdown."""
    global _checkpointer, _checkpointer_pool
    if _checkpointer_pool is not None:
        await _checkpointer_pool.close()
        logger.info("Checkpointer connection pool closed")
    _checkpointer = None
    _checkpointer_pool = None


async def get_orchestrator() -> CompiledStateGraph:
    """Create and cache the Deep Agents orchestrator.

    Returns a compiled LangGraph graph configured with:
    - The LLM from the configured provider (Cortex, Azure OpenAI, Anthropic, OpenAI, Vertex AI)
    - 6 specialized subagents (all using the same model as orchestrator)
    - All 19 LangChain tools
    - Langfuse tracing for monitoring LLM usage
    - PostgreSQL checkpointer for persistent conversation state
    """
    global _orchestrator

    if _orchestrator is not None:
        return _orchestrator

    from deepagents import create_deep_agent

    from config import get_effective_settings
    from services.llm import get_chat_model

    settings = get_effective_settings()
    model = get_chat_model()
    checkpointer = await get_checkpointer()

    # Sanitize prompts if the primary provider is Azure OpenAI
    needs_sanitize = _is_azure_model(model)
    if needs_sanitize:
        logger.info("Azure primary detected — sanitizing prompts for content filter")

    subagents = _build_subagents(model, sanitize=needs_sanitize)

    logger.info(
        "Building orchestrator: model=%s, subagents=%d, tools=%d, sanitized=%s",
        type(model).__name__,
        len(subagents),
        0,
        needs_sanitize,
    )

    orch_prompt = sanitize_prompt_for_azure(ORCHESTRATOR_PROMPT) if needs_sanitize else ORCHESTRATOR_PROMPT
    agent = create_deep_agent(
        model=model,
        system_prompt=orch_prompt,
        tools=[],
        subagents=subagents,
        name="ekaix-orchestrator",
        checkpointer=checkpointer,
    )

    _orchestrator = agent
    logger.info("Deep Agents orchestrator compiled successfully (with PostgreSQL checkpointer)")
    return agent


async def reset_orchestrator() -> CompiledStateGraph:
    """Clear the cached orchestrator and rebuild it with current settings.

    Call this after applying LLM config overrides so the new model is used.
    """
    global _orchestrator
    _orchestrator = None
    logger.info("Orchestrator cache cleared — rebuilding with current settings")
    return await get_orchestrator()


def _is_azure_model(model: Any) -> bool:
    """Check if a model is backed by Azure OpenAI (needs prompt sanitization)."""
    model_cls = type(model).__name__
    # AzureChatOpenAI is obvious; ChatOpenAI with Azure base_url is used for
    # reasoning models (gpt-5 family) routed through Azure's v1 endpoint.
    if model_cls == "AzureChatOpenAI":
        return True
    if model_cls == "ChatOpenAI":
        base_url = str(getattr(model, "openai_api_base", "") or getattr(model, "base_url", "") or "")
        return ".openai.azure.com" in base_url
    return False


async def build_fallback_orchestrator(fallback_model: Any) -> CompiledStateGraph:
    """Build a one-shot orchestrator using a specific fallback model.

    Does NOT modify the cached ``_orchestrator`` — this graph is used only
    for the current request. The next request will use the primary model again.

    If the fallback model is Azure OpenAI, prompts are automatically sanitized
    to avoid Azure's content filter (jailbreak detection).
    """
    from deepagents import create_deep_agent

    checkpointer = await get_checkpointer()
    needs_sanitize = _is_azure_model(fallback_model)
    if needs_sanitize:
        logger.info("Azure fallback detected — sanitizing prompts for content filter")
    subagents = _build_subagents(fallback_model, sanitize=needs_sanitize)

    orch_prompt = sanitize_prompt_for_azure(ORCHESTRATOR_PROMPT) if needs_sanitize else ORCHESTRATOR_PROMPT
    graph = create_deep_agent(
        model=fallback_model,
        system_prompt=orch_prompt,
        tools=[],
        subagents=subagents,
        name="ekaix-orchestrator",
        checkpointer=checkpointer,
    )
    logger.info("Built fallback orchestrator with model: %s (sanitized=%s)", type(fallback_model).__name__, needs_sanitize)
    return graph
