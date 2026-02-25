"""Main Deep Agents orchestrator with 6 specialized subagents.

Uses `create_deep_agent` from the deepagents library to build a LangGraph-based
agent that delegates to specialized subagents based on conversation phase:
    - Discovery: Profiles schemas, detects PKs/FKs, builds ERD
    - Transformation: Creates Dynamic Tables for bronze/silver data cleanup
    - Modeling: Designs and creates Gold layer star schema (fact + dimension tables)
    - Model Builder: BRD capture + semantic view YAML generation + validation (merged)
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
    MODEL_BUILDER_PROMPT,
    MODELING_PROMPT,
    ORCHESTRATOR_PROMPT,
    PUBLISHING_PROMPT,
    TRANSFORMATION_PROMPT,
    sanitize_prompt_for_azure,
)

logger = logging.getLogger(__name__)

# Tools organized by subagent
_discovery_tools: list[Any] = []
_transformation_tools: list[Any] = []
_modeling_tools: list[Any] = []
_model_builder_tools: list[Any] = []
_publishing_tools: list[Any] = []
_explorer_tools: list[Any] = []


def _load_tools() -> None:
    """Load all LangChain tools from the tools modules."""
    global _discovery_tools, _transformation_tools, _modeling_tools
    global _model_builder_tools, _publishing_tools, _explorer_tools

    from tools.snowflake_tools import (
        compute_quality_score,
        create_cortex_agent,
        create_document_search_service,
        create_semantic_view,
        execute_rcr_query,
        extract_structured_from_documents,
        grant_agent_access,
        profile_table,
        query_cortex_agent,
        query_information_schema,
        validate_semantic_view_yaml,
        validate_sql,
        verify_yaml_against_brd,
    )
    from tools.neo4j_tools import (
        classify_entity,
        get_relationship_path,
        query_erd_graph,
        update_erd,
    )
    from tools.neo4j_document_tools import (
        find_facts_for_entity,
        link_fact_to_entity,
        query_document_graph,
        upsert_document_chunks,
        upsert_document_facts,
        upsert_document_node,
    )
    from tools.postgres_tools import (
        get_latest_brd,
        get_latest_data_description,
        get_latest_semantic_view,
        load_workspace_state,
        log_agent_action,
        query_document_facts,
        search_document_chunks,
        save_brd,
        save_data_description,
        save_quality_report,
        save_semantic_view,
        save_workspace_state,
        update_validation_status,
        verify_brd_completeness,
    )
    from tools.minio_tools import (
        list_artifacts,
        retrieve_artifact,
        upload_artifact,
    )
    from tools.discovery_tools import build_erd_from_description
    from tools.transformation_tools import (
        profile_source_table,
        register_transformed_layer,
        transform_tables_batch,
    )
    from tools.modeling_tools import (
        create_gold_table,
        create_gold_tables_batch,
        generate_gold_table_ddl as generate_gold_ddl,
        validate_gold_grain,
        save_data_catalog,
        save_business_glossary,
        save_metrics_definitions,
        save_validation_rules,
        get_latest_data_catalog,
        get_latest_business_glossary,
        get_latest_metrics_definitions,
        get_latest_validation_rules,
        register_gold_layer,
        save_openlineage_artifact,
    )

    # Discovery Agent tools — conversational discovery + Data Description + ERD building
    _discovery_tools = [
        execute_rcr_query,
        query_erd_graph,
        save_data_description,
        get_latest_data_description,
        upload_artifact,
        build_erd_from_description,
    ]

    # Transformation Agent tools — batch processing with Cortex AI fallback
    _transformation_tools = [
        profile_source_table,
        transform_tables_batch,
        register_transformed_layer,
        execute_rcr_query,
    ]

    from tools.web_tools import fetch_documentation

    # Model Builder tools — combined requirements + generation + validation
    _model_builder_tools = [
        execute_rcr_query,
        save_brd,
        get_latest_brd,
        save_semantic_view,
        get_latest_semantic_view,
        upload_artifact,
        get_latest_data_description,
        query_erd_graph,
        query_document_graph,
        find_facts_for_entity,
        search_document_chunks,
        fetch_documentation,
        validate_semantic_view_yaml,
        update_validation_status,
        verify_brd_completeness,
        verify_yaml_against_brd,
        extract_structured_from_documents,
    ]

    # Modeling Agent tools — Gold layer star schema design and DDL
    # Prefer create_gold_tables_batch for batch processing (fewer tool calls)
    _modeling_tools = [
        get_latest_brd,
        get_latest_data_description,
        execute_rcr_query,
        create_gold_tables_batch,
        generate_gold_ddl,
        create_gold_table,
        validate_gold_grain,
        save_data_catalog,
        save_business_glossary,
        save_metrics_definitions,
        save_validation_rules,
        get_latest_data_catalog,
        get_latest_business_glossary,
        get_latest_metrics_definitions,
        get_latest_validation_rules,
        register_gold_layer,
        save_openlineage_artifact,
        upload_artifact,
    ]

    # Publishing Agent tools
    _publishing_tools = [
        get_latest_semantic_view,
        get_latest_brd,
        create_semantic_view,
        create_cortex_agent,
        create_document_search_service,
        grant_agent_access,
        log_agent_action,
        upload_artifact,
    ]

    # Explorer Agent tools
    _explorer_tools = [
        execute_rcr_query,
        query_erd_graph,
        query_document_graph,
        find_facts_for_entity,
        profile_table,
        query_cortex_agent,
        query_document_facts,
        search_document_chunks,
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
            "name": "transformation-agent",
            "description": (
                "Prepares non-gold data for semantic modeling by creating "
                "Snowflake Dynamic Tables. Handles type casting, deduplication, "
                "null handling, and column renaming. Use after discovery when "
                "tables are classified as bronze or silver quality."
            ),
            "system_prompt": _s(TRANSFORMATION_PROMPT),
            "tools": _transformation_tools,
            "model": model,
        },
        {
            "name": "modeling-agent",
            "description": (
                "Designs and creates Gold layer star schema (fact and dimension "
                "tables) as Snowflake Dynamic Tables based on business requirements. "
                "Generates data catalog, business glossary, metrics definitions, "
                "and validation rules. Use after BRD is approved."
            ),
            "system_prompt": _s(MODELING_PROMPT),
            "tools": _modeling_tools,
            "model": model,
        },
        {
            "name": "model-builder",
            "description": (
                "Handles the full BRD-to-validation lifecycle: captures business "
                "requirements through intelligent questions, generates the BRD, "
                "creates Snowflake semantic view YAML, verifies completeness, "
                "and validates against real data. Use for requirements capture, "
                "YAML generation, validation, and revision of either document."
            ),
            "system_prompt": _s(MODEL_BUILDER_PROMPT),
            "tools": _model_builder_tools,
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
        conn_string = "postgresql://" + conn_string[len("postgres://") :]

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
    - All LangChain tools (discovery, transformation, modeling, model-builder, publishing, explorer)
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

    orch_prompt = (
        sanitize_prompt_for_azure(ORCHESTRATOR_PROMPT) if needs_sanitize else ORCHESTRATOR_PROMPT
    )
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
        base_url = str(
            getattr(model, "openai_api_base", "") or getattr(model, "base_url", "") or ""
        )
        return ".openai.azure.com" in base_url
    return False
