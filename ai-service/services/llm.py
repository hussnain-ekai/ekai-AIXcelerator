"""LLM provider factory supporting multiple backends.

Supports:
- Snowflake Cortex (via Chat Completions REST API — OpenAI-compatible)
- Google Vertex AI (user-provided service account credentials)
- Azure OpenAI (Enterprise)
- Anthropic (Public API)
- OpenAI (Public API)

LiteLLM Router Integration:
- When litellm_enable=True, returns a production-grade LiteLLM Router with automatic failover
- The router handles retries, cooldowns, and provider switching transparently
- When litellm_enable=False, falls back to legacy single-provider mode

Langfuse Integration:
- Callbacks are added at the MODEL level so all LLM calls are automatically traced
- This works even with LangGraph's astream_events() which doesn't propagate callbacks

Note on retry/fallback:
- LiteLLM Router provides production-grade retry and fallback at the router level
- LangChain provider SDKs (Vertex AI, OpenAI, Anthropic) have built-in retry for transient errors
"""

import json
import logging
import os
import tempfile
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from config import get_effective_settings

logger = logging.getLogger(__name__)


def _get_langfuse_callback() -> Any | None:
    """Create Langfuse callback handler if credentials are configured.

    Returns None if Langfuse is not configured or fails to initialize.
    """
    settings = get_effective_settings()
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None

    try:
        from langfuse.langchain import CallbackHandler

        handler = CallbackHandler()
        logger.info(
            "Langfuse callback handler created (base_url=%s)",
            settings.langfuse_base_url or "default",
        )
        return handler
    except Exception as e:
        logger.warning("Failed to create Langfuse callback handler: %s", e)
        return None


def _is_reasoning_model(model_name: str) -> bool:
    """Check if a model is a reasoning model that does not support temperature.

    The entire GPT-5 family (gpt-5, gpt-5-mini, gpt-5-nano) and o-series
    (o1, o3, o4-mini) are reasoning models that reject the temperature param.
    They use reasoning_effort instead.
    """
    name = model_name.lower()
    return any(
        name.startswith(prefix)
        for prefix in ("o1", "o3", "o4", "gpt-5")
    )


def _build_model_for_provider(
    provider: str,
    settings: Any,
    callbacks: list[Any] | None = None,
) -> BaseChatModel:
    """Build a BaseChatModel for the given provider string.

    Raises ValueError if the provider is unknown or misconfigured.
    """
    if provider == "snowflake-cortex":
        from langchain_openai import ChatOpenAI

        account = settings.snowflake_account
        base_url = f"https://{account}.snowflakecomputing.com/api/v2/cortex/v1"
        api_key = settings.snowflake_password.get_secret_value()
        logger.info(
            "Using Snowflake Cortex Chat Completions API: model=%s account=%s",
            settings.cortex_model,
            account,
        )
        return ChatOpenAI(
            model=settings.cortex_model,
            base_url=base_url,
            api_key=api_key,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            callbacks=callbacks,
        )

    if provider == "vertex-ai":
        if not settings.vertex_project:
            raise ValueError(
                "VERTEX_PROJECT is required when llm_provider=vertex-ai"
            )

        if settings.vertex_credentials_json:
            creds_json = settings.vertex_credentials_json
            creds_data = json.loads(creds_json)
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                json.dump(creds_data, f)
                creds_path = f.name
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
            logger.info("Using Vertex AI credentials from runtime overrides")
        elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            creds_path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
            if not os.path.exists(creds_path):
                raise ValueError(
                    f"GOOGLE_APPLICATION_CREDENTIALS file not found: {creds_path}"
                )
            logger.info("Using Vertex AI credentials from GOOGLE_APPLICATION_CREDENTIALS: %s", creds_path)
        else:
            raise ValueError(
                "Vertex AI credentials not found. Set GOOGLE_APPLICATION_CREDENTIALS env var "
                "or restore config from PostgreSQL."
            )

        model_name = settings.vertex_model
        is_claude = model_name.startswith("claude-")

        if is_claude:
            from langchain_google_vertexai.model_garden import ChatAnthropicVertex

            logger.info(
                "Using Vertex AI (Claude): model=%s project=%s location=%s",
                model_name,
                settings.vertex_project,
                settings.vertex_location,
            )
            return ChatAnthropicVertex(
                model=model_name,
                project=settings.vertex_project,
                location=settings.vertex_location,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                callbacks=callbacks,
            )
        else:
            from langchain_google_genai import ChatGoogleGenerativeAI

            logger.info(
                "Using Vertex AI (Gemini): model=%s project=%s location=%s",
                model_name,
                settings.vertex_project,
                settings.vertex_location,
            )
            return ChatGoogleGenerativeAI(
                model=model_name,
                project=settings.vertex_project,
                location=settings.vertex_location,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                callbacks=callbacks,
            )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        api_key = settings.anthropic_api_key.get_secret_value()
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when llm_provider=anthropic")
        logger.info("Using Anthropic API: model=%s", settings.anthropic_model)
        return ChatAnthropic(
            model=settings.anthropic_model,
            api_key=api_key,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            callbacks=callbacks,
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        api_key = settings.openai_api_key.get_secret_value()
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when llm_provider=openai")
        logger.info("Using OpenAI API: model=%s", settings.openai_model)
        kwargs: dict[str, Any] = dict(
            model=settings.openai_model,
            api_key=api_key,
            max_tokens=settings.llm_max_tokens,
            callbacks=callbacks,
        )
        if not _is_reasoning_model(settings.openai_model):
            kwargs["temperature"] = settings.llm_temperature
        return ChatOpenAI(**kwargs)

    if provider == "azure-openai":
        api_key = settings.azure_openai_api_key.get_secret_value()
        if not api_key or not settings.azure_openai_endpoint:
            raise ValueError(
                "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT are required "
                "when llm_provider=azure-openai"
            )
        deployment = settings.azure_openai_deployment
        endpoint = settings.azure_openai_endpoint.rstrip("/")

        if _is_reasoning_model(deployment):
            # GPT-5 family + o-series require the v1 API endpoint.
            # Use ChatOpenAI with base_url pointing to Azure's v1 path.
            # Docs: https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/reasoning
            from langchain_openai import ChatOpenAI as AzureV1ChatOpenAI

            base_url = f"{endpoint}/openai/v1/"
            logger.info(
                "Using Azure OpenAI (v1 API): model=%s base_url=%s",
                deployment,
                base_url,
            )
            return AzureV1ChatOpenAI(
                model=deployment,
                base_url=base_url,
                api_key=api_key,
                max_completion_tokens=settings.llm_max_tokens,
                callbacks=callbacks,
            )
        else:
            # Legacy models (gpt-4o, etc.) use AzureChatOpenAI with
            # the /openai/deployments/{name}/chat/completions path.
            from langchain_openai import AzureChatOpenAI

            logger.info(
                "Using Azure OpenAI (legacy): deployment=%s endpoint=%s",
                deployment,
                endpoint,
            )
            return AzureChatOpenAI(
                azure_deployment=deployment,
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=settings.azure_openai_api_version,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                callbacks=callbacks,
            )

    if not provider:
        raise ValueError(
            "LLM provider not configured. Please configure your LLM provider "
            "via the UI at /llm-configuration before using the agent."
        )

    raise ValueError(
        f"Unknown LLM provider: '{provider}'. "
        "Supported: snowflake-cortex, vertex-ai, azure-openai, anthropic, openai"
    )


def get_chat_model() -> BaseChatModel | Any:
    """Create a LangChain ChatModel or LiteLLM Router based on configuration.

    When litellm_enable=True (recommended for production):
        Returns a LiteLLM Router with automatic failover between primary and
        fallback providers. The router has the same interface as a LangChain
        ChatModel and handles retries, cooldowns, and provider switching.

    When litellm_enable=False (legacy mode):
        Returns a single-provider LangChain ChatModel with no automatic failover.

    Langfuse callbacks are automatically added to models if configured,
    ensuring all LLM calls are traced regardless of how the model is invoked.

    Returns:
        A LiteLLM Router (if enabled) or BaseChatModel instance.

    Raises:
        ValueError: If the provider is unknown or misconfigured.
    """
    settings = get_effective_settings()

    # Use LiteLLM Router if enabled (recommended for production)
    if settings.litellm_enable:
        try:
            from services.litellm_router import (
                FALLBACK_MODEL_GROUP,
                build_litellm_router,
            )
            from services.litellm_wrapper import create_langchain_compatible_router

            langfuse_callback = _get_langfuse_callback()
            callbacks = [langfuse_callback] if langfuse_callback else None

            router = build_litellm_router()

            # Determine if fallback is configured
            fallback = settings.llm_fallback_config
            has_fallback = bool(
                fallback and isinstance(fallback, dict) and fallback.get("provider")
            )

            model = create_langchain_compatible_router(
                router=router,
                primary_provider=settings.llm_provider,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                callbacks=callbacks,
                fallback_model_group=FALLBACK_MODEL_GROUP if has_fallback else None,
            )
            logger.info("Using LiteLLM Router for LLM calls (automatic failover enabled)")
            return model
        except Exception as e:
            logger.error("Failed to build LiteLLM Router: %s. Falling back to single provider.", e)
            # Fall through to legacy mode if router build fails

    # Fallback to legacy single-provider mode
    provider = settings.llm_provider.lower()

    langfuse_callback = _get_langfuse_callback()
    callbacks = [langfuse_callback] if langfuse_callback else None

    logger.info("Using legacy single-provider mode (no automatic failover)")
    return _build_model_for_provider(provider, settings, callbacks)
