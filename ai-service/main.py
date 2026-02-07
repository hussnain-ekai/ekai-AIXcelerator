"""FastAPI application entry point for the ekaix AI service.

Manages the application lifespan (startup/shutdown), CORS middleware,
and router registration. Initializes all database connections on startup
and closes them cleanly on shutdown.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from routers import agent, config, health

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

SERVICE_NAME = "ekaix-ai-service"
SERVICE_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup and shutdown of shared resources."""
    settings = get_settings()
    logger.info(
        "Starting %s v%s on port %d (llm_provider=%s)",
        SERVICE_NAME,
        SERVICE_VERSION,
        settings.port,
        settings.llm_provider,
    )

    # --- Initialize Langfuse tracing (optional) ---
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        import os
        os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
        os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
        os.environ["LANGFUSE_HOST"] = settings.langfuse_base_url
        logger.info(
            "Langfuse tracing enabled: %s",
            settings.langfuse_base_url
        )
    # Langfuse is optional - silently disabled if credentials not configured

    # --- Startup: Initialize database connections ---
    from services import minio as minio_service
    from services import neo4j as neo4j_service
    from services import postgres as postgres_service
    from services import redis as redis_service
    from services import snowflake as snowflake_service

    try:
        await postgres_service.get_pool(settings.database_url)
        logger.info("PostgreSQL pool initialized")
    except Exception as e:
        logger.error("PostgreSQL init failed: %s", e)

    try:
        await neo4j_service.get_driver(
            settings.neo4j_uri,
            settings.neo4j_user,
            settings.neo4j_password.get_secret_value(),
        )
        logger.info("Neo4j driver initialized")
    except Exception as e:
        logger.error("Neo4j init failed: %s", e)

    try:
        await redis_service.get_client(settings.redis_url)
        logger.info("Redis client initialized")
    except Exception as e:
        logger.error("Redis init failed: %s", e)

    try:
        minio_service.get_client(
            f"{settings.minio_endpoint}:{settings.minio_port}",
            settings.minio_access_key,
            settings.minio_secret_key.get_secret_value(),
            secure=settings.minio_use_ssl,
        )
        logger.info("MinIO client initialized")
    except Exception as e:
        logger.error("MinIO init failed: %s", e)

    try:
        sf_ok = await snowflake_service.health_check()
        if sf_ok:
            logger.info("Snowflake connection verified")
        else:
            logger.warning("Snowflake health check failed — check credentials")
    except Exception as e:
        logger.error("Snowflake init failed: %s", e)

    # Restore LLM overrides from PostgreSQL before building the orchestrator
    try:
        from config import restore_llm_overrides
        from services.postgres import _pool as pg_pool

        if pg_pool:
            await restore_llm_overrides(pg_pool)
    except Exception as e:
        logger.warning("Failed to restore LLM overrides: %s", e)

    # Initialize the checkpointer and orchestrator agent
    try:
        from agents.orchestrator import get_checkpointer, get_orchestrator
        await get_checkpointer()
        await get_orchestrator()
        logger.info("Deep Agents orchestrator initialized (with PostgreSQL checkpointer)")
    except Exception as e:
        logger.error("Orchestrator init failed: %s", e)

    yield

    # --- Shutdown: Close all connections ---
    logger.info("Shutting down %s", SERVICE_NAME)

    try:
        from agents.orchestrator import close_checkpointer
        await close_checkpointer()
    except Exception:
        pass
    try:
        await snowflake_service.close()
    except Exception:
        pass
    try:
        await postgres_service.close()
    except Exception:
        pass
    try:
        await neo4j_service.close()
    except Exception:
        pass
    try:
        await redis_service.close()
    except Exception:
        pass


app = FastAPI(
    title=SERVICE_NAME,
    version=SERVICE_VERSION,
    description="AI service for ekaiX AIXcelerator — orchestrates Deep Agents, "
    "LangChain tools, and Snowflake Cortex integration.",
    lifespan=lifespan,
)

# Parse CORS origins from config (comma-separated string)
_settings = get_settings()
_cors_origins = [origin.strip() for origin in _settings.allowed_cors_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(agent.router)
app.include_router(config.router)


@app.get("/")
async def root() -> dict[str, str]:
    """Return basic service identification."""
    return {"service": SERVICE_NAME, "version": SERVICE_VERSION}
