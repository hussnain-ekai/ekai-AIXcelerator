"""LiteLLM Router for automatic LLM fallback.

This module provides production-grade LLM failover using the LiteLLM Router.
The router automatically handles:
- Retries on transient failures (429, 500, 503, timeouts)
- Failover to backup providers on ANY error (including 403, 401, etc.)
- Cooldown management for failing providers
- Order-based routing (primary -> fallback)

Architecture:
- Primary provider → model group "ekaiX-primary"
- Fallback provider → model group "ekaiX-fallback"
- Router fallbacks config → ekaiX-primary fails over to ekaiX-fallback
- ChatLiteLLMRouter uses model="ekaiX-primary" so primary is tried first

Reference: https://docs.litellm.ai/docs/routing-load-balancing
"""

from __future__ import annotations

import logging
from typing import Any

from litellm import Router

from config import get_effective_settings

logger = logging.getLogger(__name__)

# Model group names — ChatLiteLLMRouter must use PRIMARY_MODEL_GROUP
PRIMARY_MODEL_GROUP = "ekaiX-primary"
FALLBACK_MODEL_GROUP = "ekaiX-fallback"


def build_litellm_router() -> Router:
    """Build LiteLLM Router with primary + fallback providers from Settings.

    Uses two separate model groups with explicit fallback configuration so that
    ANY error from the primary (including 403/401 auth errors) triggers failover
    to the fallback provider.

    Returns:
        Router configured with primary and fallback providers.

    Raises:
        ValueError: If no providers are configured.
    """
    settings = get_effective_settings()
    model_list: list[dict[str, Any]] = []
    has_fallback = False

    # Primary provider (from UI config stored in settings overrides)
    primary_model = _get_model_for_provider(settings, settings.llm_provider)
    primary_config = _build_provider_config(
        provider=settings.llm_provider,
        model=primary_model,
        model_group=PRIMARY_MODEL_GROUP,
    )
    if primary_config:
        model_list.append(primary_config)
        logger.info("LiteLLM primary: %s/%s", settings.llm_provider, primary_model)

    # Fallback provider (credentials stored inside llm_fallback_config dict)
    fallback = settings.llm_fallback_config
    if fallback and isinstance(fallback, dict) and fallback.get("provider"):
        fallback_provider = fallback["provider"]
        fallback_model = _get_model_from_fallback_config(fallback, fallback_provider)

        fallback_config = _build_provider_config(
            provider=fallback_provider,
            model=fallback_model,
            model_group=FALLBACK_MODEL_GROUP,
            credentials=fallback,
        )
        if fallback_config:
            model_list.append(fallback_config)
            has_fallback = True
            logger.info("LiteLLM fallback: %s/%s", fallback_provider, fallback_model)

    if not model_list:
        raise ValueError("No LiteLLM providers configured (check llm_provider in Settings)")

    # Configure explicit fallback: primary group → fallback group
    # This ensures ANY error (not just retryable ones) triggers failover
    router_kwargs: dict[str, Any] = dict(
        model_list=model_list,
        allowed_fails=settings.litellm_allowed_fails,
        cooldown_time=settings.litellm_cooldown_time,
        num_retries=settings.litellm_num_retries,
    )
    if has_fallback:
        router_kwargs["fallbacks"] = [{PRIMARY_MODEL_GROUP: [FALLBACK_MODEL_GROUP]}]

    # Drop unsupported params (e.g. temperature for reasoning models like gpt-5)
    # rather than failing with UnsupportedParamsError
    import litellm
    litellm.drop_params = True

    router = Router(**router_kwargs)

    logger.info(
        "LiteLLM Router initialized with %d provider(s)%s",
        len(model_list),
        f" (fallback: {PRIMARY_MODEL_GROUP} → {FALLBACK_MODEL_GROUP})" if has_fallback else "",
    )
    return router


def _get_model_for_provider(settings: Any, provider: str) -> str:
    """Get the model name for a specific provider from top-level settings."""
    if provider == "anthropic":
        return settings.anthropic_model
    elif provider == "openai":
        return settings.openai_model
    elif provider == "vertex-ai":
        return settings.vertex_model
    elif provider == "azure-openai":
        return settings.azure_openai_deployment
    elif provider == "snowflake-cortex":
        return settings.cortex_model
    return ""


def _get_model_from_fallback_config(fallback: dict[str, Any], provider: str) -> str:
    """Extract model name from a fallback config dict.

    The fallback config uses provider-specific keys (azure_openai_deployment,
    vertex_model, etc.), not a generic "model" key.
    """
    if provider == "azure-openai":
        return fallback.get("azure_openai_deployment", fallback.get("model", ""))
    elif provider == "vertex-ai":
        return fallback.get("vertex_model", fallback.get("model", ""))
    elif provider == "anthropic":
        return fallback.get("anthropic_model", fallback.get("model", ""))
    elif provider == "openai":
        return fallback.get("openai_model", fallback.get("model", ""))
    elif provider == "snowflake-cortex":
        return fallback.get("cortex_model", fallback.get("model", ""))
    return fallback.get("model", "")


def _get_secret(value: Any) -> str:
    """Extract string from a value that may be a SecretStr or plain string."""
    if value is None:
        return ""
    if hasattr(value, "get_secret_value"):
        return value.get_secret_value()
    return str(value)


def _build_provider_config(
    provider: str,
    model: str,
    model_group: str,
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build a LiteLLM model_list entry for a specific provider.

    Args:
        provider: Provider name (anthropic, openai, vertex-ai, azure-openai, snowflake-cortex)
        model: Model identifier for the provider
        model_group: Router model group name (e.g. "ekaiX-primary" or "ekaiX-fallback")
        credentials: Optional dict with provider-specific credentials.
            For the primary provider, reads from top-level settings.
            For the fallback provider, uses this dict instead.

    Returns:
        LiteLLM model configuration dict, or None if invalid provider.
    """
    if not provider or not model:
        return None

    settings = get_effective_settings()

    config: dict[str, Any] = {
        "model_name": model_group,
        "litellm_params": {},
    }

    if provider == "anthropic":
        config["litellm_params"]["model"] = f"anthropic/{model}"
        api_key = (
            credentials.get("anthropic_api_key", "") if credentials
            else _get_secret(settings.anthropic_api_key)
        )
        if api_key:
            config["litellm_params"]["api_key"] = api_key

    elif provider == "openai":
        config["litellm_params"]["model"] = f"openai/{model}"
        api_key = (
            credentials.get("openai_api_key", "") if credentials
            else _get_secret(settings.openai_api_key)
        )
        if api_key:
            config["litellm_params"]["api_key"] = api_key

    elif provider == "vertex-ai":
        config["litellm_params"]["model"] = f"vertex_ai/{model}"
        config["litellm_params"]["vertex_project"] = (
            credentials.get("vertex_project", "") if credentials
            else settings.vertex_project
        )
        config["litellm_params"]["vertex_location"] = (
            credentials.get("vertex_location", "") if credentials
            else settings.vertex_location
        )
        # Vertex AI uses GOOGLE_APPLICATION_CREDENTIALS env var for auth

    elif provider == "azure-openai":
        deployment = model  # For Azure, model IS the deployment name
        config["litellm_params"]["model"] = f"azure/{deployment}"
        api_key = (
            credentials.get("azure_openai_api_key", "") if credentials
            else _get_secret(settings.azure_openai_api_key)
        )
        if api_key:
            config["litellm_params"]["api_key"] = api_key
        config["litellm_params"]["api_base"] = (
            credentials.get("azure_openai_endpoint", "") if credentials
            else settings.azure_openai_endpoint
        )
        config["litellm_params"]["api_version"] = (
            credentials.get("azure_openai_api_version", "") if credentials
            else settings.azure_openai_api_version
        )

    elif provider == "snowflake-cortex":
        config["litellm_params"]["model"] = f"snowflake/{model}"
        config["litellm_params"]["account_identifier"] = settings.snowflake_account
        config["litellm_params"]["user"] = settings.snowflake_user
        password = _get_secret(settings.snowflake_password)
        if password:
            config["litellm_params"]["password"] = password

    else:
        logger.warning("Unknown LLM provider: %s", provider)
        return None

    return config
