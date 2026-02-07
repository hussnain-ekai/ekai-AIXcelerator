"""LLM configuration router — runtime provider switching without restart."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from langchain_core.language_models.chat_models import BaseChatModel

from config import (
    apply_settings_overrides,
    clear_settings_overrides,
    get_effective_settings,
    get_settings,
)
from models.schemas import (
    LLMConfigRequest,
    LLMConfigResponse,
    LLMStatusResponse,
    LLMTestRequest,
    LLMTestResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"])


def _get_active_model_name(provider: str, settings: Any) -> str:
    """Return the model name for the given provider from settings."""
    if provider == "snowflake-cortex":
        return settings.cortex_model
    if provider == "vertex-ai":
        return settings.vertex_model
    if provider == "azure-openai":
        return settings.azure_openai_deployment
    if provider == "anthropic":
        return settings.anthropic_model
    if provider == "openai":
        return settings.openai_model
    return "unknown"


def _request_to_overrides(req: LLMConfigRequest | LLMTestRequest) -> dict[str, Any]:
    """Map request fields to Settings field names for apply_settings_overrides."""
    overrides: dict[str, Any] = {"llm_provider": req.provider}

    # Model shorthand: if `model` is set, map to the provider-specific field
    if req.model:
        if req.provider == "snowflake-cortex":
            overrides["cortex_model"] = req.model
        elif req.provider == "vertex-ai":
            overrides["vertex_model"] = req.model
        elif req.provider == "azure-openai":
            overrides["azure_openai_deployment"] = req.model
        elif req.provider == "anthropic":
            overrides["anthropic_model"] = req.model
        elif req.provider == "openai":
            overrides["openai_model"] = req.model

    # Provider-specific fields
    field_map: dict[str, str] = {
        "cortex_model": "cortex_model",
        # Vertex AI
        "vertex_credentials_json": "vertex_credentials_json",
        "vertex_project": "vertex_project",
        "vertex_location": "vertex_location",
        "vertex_model": "vertex_model",
        # Anthropic
        "anthropic_api_key": "anthropic_api_key",
        "anthropic_model": "anthropic_model",
        # OpenAI
        "openai_api_key": "openai_api_key",
        "openai_model": "openai_model",
        # Azure OpenAI
        "azure_openai_api_key": "azure_openai_api_key",
        "azure_openai_endpoint": "azure_openai_endpoint",
        "azure_openai_deployment": "azure_openai_deployment",
        "azure_openai_api_version": "azure_openai_api_version",
    }

    for req_field, settings_field in field_map.items():
        value = getattr(req, req_field, None)
        if value is not None:
            overrides[settings_field] = value

    return overrides


def _build_test_model(overrides: dict[str, Any]) -> BaseChatModel:
    """Build a throwaway chat model from overrides without touching global state."""
    provider = overrides.get("llm_provider", "")
    base = get_settings()

    if provider == "snowflake-cortex":
        from langchain_openai import ChatOpenAI

        account = base.snowflake_account
        return ChatOpenAI(
            model=overrides.get("cortex_model", base.cortex_model),
            base_url=f"https://{account}.snowflakecomputing.com/api/v2/cortex/v1",
            api_key=base.snowflake_password.get_secret_value(),
            temperature=get_settings().llm_temperature,
            max_tokens=get_settings().llm_test_max_tokens,
        )

    if provider == "vertex-ai":
        import json
        import os
        import tempfile

        creds_json = overrides.get("vertex_credentials_json", base.vertex_credentials_json)
        project = overrides.get("vertex_project", base.vertex_project)
        location = overrides.get("vertex_location", base.vertex_location)
        model_name = overrides.get("vertex_model", base.vertex_model)

        if not creds_json:
            raise ValueError("Vertex AI credentials JSON is required")
        if not project:
            raise ValueError("Vertex AI project ID is required")

        # Write credentials to temp file for GOOGLE_APPLICATION_CREDENTIALS
        creds_data = json.loads(creds_json)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(creds_data, f)
            creds_path = f.name

        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path

        # Determine if this is a Gemini or Claude model
        is_claude = model_name.startswith("claude-")

        if is_claude:
            from langchain_google_vertexai.model_garden import ChatAnthropicVertex

            return ChatAnthropicVertex(
                model=model_name,
                project=project,
                location=location,
                temperature=get_settings().llm_temperature,
                max_tokens=get_settings().llm_test_max_tokens,
            )
        else:
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model=model_name,
                project=project,
                location=location,
                temperature=get_settings().llm_temperature,
                max_tokens=get_settings().llm_test_max_tokens,
            )

    if provider == "azure-openai":
        from langchain_openai import AzureChatOpenAI

        api_key = overrides.get(
            "azure_openai_api_key",
            base.azure_openai_api_key.get_secret_value(),
        )
        endpoint = overrides.get("azure_openai_endpoint", base.azure_openai_endpoint)
        if not api_key or not endpoint:
            raise ValueError("Azure endpoint and API key are required")
        return AzureChatOpenAI(
            azure_deployment=overrides.get(
                "azure_openai_deployment", base.azure_openai_deployment
            ),
            azure_endpoint=endpoint,
            api_key=api_key if isinstance(api_key, str) else api_key.get_secret_value(),
            api_version=overrides.get(
                "azure_openai_api_version", base.azure_openai_api_version
            ),
            temperature=get_settings().llm_temperature,
            max_tokens=get_settings().llm_test_max_tokens,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        api_key = overrides.get(
            "anthropic_api_key",
            base.anthropic_api_key.get_secret_value(),
        )
        if not api_key:
            raise ValueError("Anthropic API key is required")
        return ChatAnthropic(
            model=overrides.get("anthropic_model", base.anthropic_model),
            api_key=api_key,
            temperature=get_settings().llm_temperature,
            max_tokens=get_settings().llm_test_max_tokens,
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        api_key = overrides.get(
            "openai_api_key",
            base.openai_api_key.get_secret_value(),
        )
        if not api_key:
            raise ValueError("OpenAI API key is required")
        return ChatOpenAI(
            model=overrides.get("openai_model", base.openai_model),
            api_key=api_key,
            temperature=get_settings().llm_temperature,
            max_tokens=get_settings().llm_test_max_tokens,
        )

    raise ValueError(f"Unknown provider: {provider}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/llm", response_model=LLMStatusResponse)
async def get_llm_status() -> LLMStatusResponse:
    """Return the currently active LLM provider and model."""
    effective = get_effective_settings()
    base = get_settings()
    provider = effective.llm_provider
    return LLMStatusResponse(
        provider=provider,
        model=_get_active_model_name(provider, effective),
        is_override=effective.llm_provider != base.llm_provider,
    )


@router.post("/llm", response_model=LLMConfigResponse)
async def set_llm_config(req: LLMConfigRequest) -> LLMConfigResponse:
    """Apply a new LLM configuration at runtime.

    Rebuilds the orchestrator with the new model. If the rebuild fails,
    reverts the overrides and returns an error.
    """
    overrides = _request_to_overrides(req)
    logger.info("Applying LLM config overrides: provider=%s", req.provider)

    # Save current overrides for rollback
    from config import _settings_overrides

    previous = dict(_settings_overrides)

    try:
        apply_settings_overrides(overrides)

        # Rebuild orchestrator with new model
        from agents.orchestrator import reset_orchestrator

        await reset_orchestrator()

        # Persist to PostgreSQL so config survives restarts
        try:
            from config import persist_llm_overrides
            from services.postgres import _pool as pg_pool

            if pg_pool:
                await persist_llm_overrides(pg_pool)
        except Exception as persist_err:
            logger.warning("Failed to persist LLM overrides: %s", persist_err)

        effective = get_effective_settings()
        model_name = _get_active_model_name(req.provider, effective)
        logger.info(
            "LLM config applied successfully: provider=%s model=%s",
            req.provider,
            model_name,
        )
        return LLMConfigResponse(
            status="ok",
            provider=req.provider,
            model=model_name,
        )
    except Exception as exc:
        logger.error("LLM config apply failed, reverting: %s", exc)
        # Revert
        _settings_overrides.clear()
        _settings_overrides.update(previous)
        # Rebuild orchestrator with reverted config
        try:
            from agents.orchestrator import reset_orchestrator

            await reset_orchestrator()
        except Exception:
            pass

        return LLMConfigResponse(
            status="error",
            provider=req.provider,
            model="",
            error=str(exc),
        )


@router.post("/llm/test", response_model=LLMTestResponse)
async def test_llm_connection(req: LLMTestRequest) -> LLMTestResponse:
    """Test an LLM provider connection without applying it.

    Creates a throwaway model, sends a test prompt, and returns the response
    time and model output.
    """
    overrides = _request_to_overrides(req)
    logger.info("Testing LLM connection: provider=%s", req.provider)

    try:
        model = _build_test_model(overrides)
        start = time.monotonic()
        response = await asyncio.wait_for(
            model.ainvoke("Say hello in one sentence."),
            timeout=get_settings().llm_test_timeout,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        content = response.content if hasattr(response, "content") else str(response)
        logger.info(
            "LLM test succeeded: provider=%s latency=%dms",
            req.provider,
            elapsed_ms,
        )
        return LLMTestResponse(
            status="ok",
            response_time_ms=elapsed_ms,
            model_response=str(content)[:500],
        )
    except asyncio.TimeoutError:
        return LLMTestResponse(
            status="error",
            error="Connection timed out after 15 seconds",
        )
    except Exception as exc:
        logger.error("LLM test failed: %s", exc)
        return LLMTestResponse(
            status="error",
            error=str(exc)[:500],
        )
