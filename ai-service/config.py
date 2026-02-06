"""Application configuration loaded from environment variables via pydantic-settings.

IMPORTANT: All configuration is loaded from the ROOT .env file (../.env).
Do NOT create a separate .env file in this directory.
"""

from __future__ import annotations

import copy
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Path to root .env file (one level up from ai-service/)
ROOT_ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    """Central configuration for the ekaix AI service.

    All values are loaded from the ROOT .env file or environment variables.
    SecretStr fields mask their values in logs and repr output.
    """

    model_config = SettingsConfigDict(
        env_file=str(ROOT_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore extra env vars not defined in this model
    )

    # --- Service ---
    ai_service_port: int = 8001
    port: int = 8001  # Alias for compatibility
    log_level: str = "info"

    # --- CORS ---
    allowed_cors_origins: str = "http://localhost:3000"  # Comma-separated list

    # --- PostgreSQL ---
    database_url: str = "postgresql://ekaix:ekaix@localhost:5432/ekaix"

    # --- Neo4j ---
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("neo4j")

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- MinIO ---
    minio_endpoint: str = "localhost"
    minio_port: int = 9000
    minio_access_key: str = "minioadmin"
    minio_secret_key: SecretStr = SecretStr("minioadmin")
    minio_use_ssl: bool = False

    # --- Snowflake ---
    snowflake_account: str = ""
    snowflake_user: str = ""
    snowflake_password: SecretStr = SecretStr("")
    snowflake_warehouse: str = ""
    snowflake_database: str = ""
    snowflake_role: str = ""

    # --- LLM Provider Selection ---
    # Configured via UI only (/llm-configuration). No .env defaults.
    # Options: "vertex-ai", "azure-openai", "anthropic", "openai", "snowflake-cortex"
    llm_provider: str = ""  # Must be set via UI

    # --- Snowflake Cortex LLM ---
    cortex_model: str = ""

    # --- Anthropic (Public API) ---
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = ""

    # --- OpenAI (Public API) ---
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = ""

    # --- Google Vertex AI ---
    google_application_credentials: str = ""  # Path to service account JSON (set in shell, not .env)
    vertex_credentials_json: str = ""  # Runtime override from UI (JSON string)
    vertex_project: str = ""
    vertex_location: str = ""
    vertex_model: str = ""

    # --- Azure OpenAI (Enterprise) ---
    azure_openai_api_key: SecretStr = SecretStr("")
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = ""

    # --- Langfuse (tracing) ---
    langfuse_secret_key: str = ""
    langfuse_public_key: str = ""
    langfuse_base_url: str = ""

    # --- LangSmith (tracing fallback) ---
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "ekaix"

    # --- MinIO Buckets ---
    minio_artifacts_bucket: str = "artifacts"
    minio_documents_bucket: str = "documents"

    # --- Session & Cache TTLs (seconds) ---
    session_ttl_seconds: int = 3600  # 1 hour
    cache_ttl_seconds: int = 3600  # 1 hour

    # --- Snowflake Timeouts (seconds) ---
    snowflake_login_timeout: int = 30
    snowflake_network_timeout: int = 60

    # --- Agent Configuration ---
    agent_recursion_limit: int = 150
    agent_stream_timeout: float = 15.0
    discovery_max_columns_per_table: int = 15

    # --- LLM Configuration ---
    llm_temperature: float = 0.1
    llm_max_tokens: int = 8000
    llm_test_timeout: float = 15.0
    llm_test_max_tokens: int = 200

    # --- Data Quality Scoring ---
    pk_uniqueness_threshold: float = 0.98
    deduction_duplicate_pk: int = 15
    deduction_orphaned_fk: int = 10
    deduction_numeric_varchar: int = 5
    deduction_missing_description: int = 2

    # --- Requirements Phase ---
    max_requirements_turns: int = 15

    # --- Query Limits ---
    rcr_query_row_limit: int = 1000  # Max rows returned by RCR queries
    tool_output_truncate_length: int = 2000  # Max chars for tool output in SSE


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance (base .env values only).

    Uses lru_cache so the .env file is read only once per process.
    """
    return Settings()


# ---------------------------------------------------------------------------
# Mutable runtime overrides
# ---------------------------------------------------------------------------

ALLOWED_LLM_OVERRIDES: set[str] = {
    "llm_provider",
    "cortex_model",
    # Vertex AI — user-provided
    "vertex_credentials_json",
    "vertex_project",
    "vertex_location",
    "vertex_model",
    # Anthropic
    "anthropic_api_key",
    "anthropic_model",
    # OpenAI
    "openai_api_key",
    "openai_model",
    # Azure OpenAI
    "azure_openai_api_key",
    "azure_openai_endpoint",
    "azure_openai_deployment",
    "azure_openai_api_version",
}

_settings_overrides: dict[str, Any] = {}


def get_effective_settings() -> Settings:
    """Return a Settings instance with runtime overrides applied.

    Creates a shallow copy of the base settings, then patches whitelisted
    fields from ``_settings_overrides``.  Secret fields are wrapped in
    ``SecretStr`` automatically.
    """
    base = get_settings()
    if not _settings_overrides:
        return base

    effective = copy.copy(base)
    for key, value in _settings_overrides.items():
        if key not in ALLOWED_LLM_OVERRIDES:
            continue
        # Wrap secret fields
        field_info = Settings.model_fields.get(key)
        if field_info and field_info.annotation is SecretStr and isinstance(value, str):
            value = SecretStr(value)
        object.__setattr__(effective, key, value)

    return effective


def apply_settings_overrides(overrides: dict[str, Any]) -> Settings:
    """Merge *overrides* into the runtime override dict and return effective settings."""
    for key, value in overrides.items():
        if key in ALLOWED_LLM_OVERRIDES:
            _settings_overrides[key] = value
        else:
            logger.warning("Ignoring disallowed override key: %s", key)
    return get_effective_settings()


def clear_settings_overrides() -> None:
    """Reset all runtime overrides back to base .env values."""
    _settings_overrides.clear()


# ---------------------------------------------------------------------------
# PostgreSQL persistence for LLM overrides
# ---------------------------------------------------------------------------

_APP_CONFIG_DDL = """
CREATE TABLE IF NOT EXISTS app_config (
    key   TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_APP_CONFIG_KEY = "llm_overrides"


async def persist_llm_overrides(pool: object) -> None:
    """Save current LLM overrides to PostgreSQL ``app_config`` table.

    Must be called after ``apply_settings_overrides`` to persist state
    across service restarts. SecretStr values are stored as plain strings.
    """
    import json

    import asyncpg

    if not isinstance(pool, asyncpg.Pool):
        return

    # Serialize: unwrap SecretStr for storage
    data: dict[str, Any] = {}
    for key, value in _settings_overrides.items():
        if key not in ALLOWED_LLM_OVERRIDES:
            continue
        if isinstance(value, SecretStr):
            data[key] = value.get_secret_value()
        else:
            data[key] = value
    await pool.execute(
        """
        INSERT INTO app_config (key, value, updated_at)
        VALUES ($1, $2::jsonb, now())
        ON CONFLICT (key) DO UPDATE SET value = $2::jsonb, updated_at = now()
        """,
        _APP_CONFIG_KEY,
        json.dumps(data),
    )
    logger.info("Persisted LLM overrides to PostgreSQL")


async def restore_llm_overrides(pool: object) -> None:
    """Restore LLM overrides from PostgreSQL on startup.

    Checks two sources in order:
    1. ``app_config`` table (AI service's own persistence)
    2. ``workspaces.settings->'llm_config'`` (backend's persistence — fallback)

    This ensures the AI service can self-restore even when restarted
    independently of the backend.
    """
    import json

    import asyncpg

    if not isinstance(pool, asyncpg.Pool):
        return

    # Ensure app_config table exists
    await pool.execute(_APP_CONFIG_DDL)

    # Source 1: app_config (AI service's own persistence)
    row = await pool.fetchrow(
        "SELECT value FROM app_config WHERE key = $1",
        _APP_CONFIG_KEY,
    )
    if row:
        data = json.loads(row["value"]) if isinstance(row["value"], str) else row["value"]
        if data.get("llm_provider"):
            for key, value in data.items():
                if key in ALLOWED_LLM_OVERRIDES:
                    _settings_overrides[key] = value
            logger.info("Restored LLM overrides from app_config: provider=%s", data.get("llm_provider", ""))
            return

    # Source 2: workspaces table (backend saves here via PUT /settings/llm)
    ws_row = await pool.fetchrow(
        "SELECT settings->'llm_config' AS llm_config FROM workspaces "
        "WHERE settings->'llm_config' IS NOT NULL "
        "ORDER BY updated_at DESC LIMIT 1",
    )
    if ws_row and ws_row["llm_config"]:
        data = json.loads(ws_row["llm_config"]) if isinstance(ws_row["llm_config"], str) else ws_row["llm_config"]
        # Map frontend field names to settings override keys
        field_map = {
            "provider": "llm_provider",
            "cortex_model": "cortex_model",
            "vertex_credentials_json": "vertex_credentials_json",
            "vertex_project": "vertex_project",
            "vertex_location": "vertex_location",
            "vertex_model": "vertex_model",
            "anthropic_api_key": "anthropic_api_key",
            "anthropic_model": "anthropic_model",
            "openai_api_key": "openai_api_key",
            "openai_model": "openai_model",
            "azure_openai_api_key": "azure_openai_api_key",
            "azure_openai_endpoint": "azure_openai_endpoint",
            "azure_openai_deployment": "azure_openai_deployment",
            "azure_openai_api_version": "azure_openai_api_version",
        }
        for src_key, dst_key in field_map.items():
            value = data.get(src_key)
            if value and dst_key in ALLOWED_LLM_OVERRIDES:
                _settings_overrides[dst_key] = value
        logger.info(
            "Restored LLM overrides from workspaces table: provider=%s",
            data.get("provider", ""),
        )
        return

    logger.info("No persisted LLM overrides found")
