"""LLM provider factory supporting multiple backends.

Supports:
- Snowflake Cortex (via Chat Completions REST API — OpenAI-compatible)
- Google Vertex AI (user-provided service account credentials)
- Azure OpenAI (Enterprise)
- Anthropic (Public API)
- OpenAI (Public API)

Langfuse Integration:
- Callbacks are added at the MODEL level so all LLM calls are automatically traced
- This works even with LangGraph's astream_events() which doesn't propagate callbacks
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


def get_chat_model() -> BaseChatModel:
    """Create a LangChain ChatModel based on the configured LLM provider.

    Langfuse callbacks are automatically added to the model if configured,
    ensuring all LLM calls are traced regardless of how the model is invoked.

    Returns:
        A BaseChatModel instance configured for the selected provider.

    Raises:
        ValueError: If the provider is unknown or misconfigured.
    """
    settings = get_effective_settings()
    provider = settings.llm_provider.lower()

    # Get Langfuse callback if configured (added to all models)
    langfuse_callback = _get_langfuse_callback()
    callbacks = [langfuse_callback] if langfuse_callback else None

    if provider == "snowflake-cortex":
        from langchain_openai import ChatOpenAI

        # Cortex Chat Completions API is OpenAI-compatible with full tool calling
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

        # Vertex AI credentials can come from:
        # 1. vertex_credentials_json (runtime override from PostgreSQL)
        # 2. GOOGLE_APPLICATION_CREDENTIALS env var (file path)
        if settings.vertex_credentials_json:
            # Write credentials JSON string to temp file
            creds_json = settings.vertex_credentials_json
            creds_data = json.loads(creds_json)
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                json.dump(creds_data, f)
                creds_path = f.name
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
            logger.info("Using Vertex AI credentials from runtime overrides")
        elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            # Use existing GOOGLE_APPLICATION_CREDENTIALS file
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

        # Determine if this is a Gemini or Claude model
        model_name = settings.vertex_model
        is_claude = model_name.startswith("claude-")

        if is_claude:
            # Use ChatAnthropicVertex for Claude models on Vertex AI
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
            # Use ChatGoogleGenerativeAI with vertexai=True for Gemini models
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
        return ChatOpenAI(
            model=settings.openai_model,
            api_key=api_key,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            callbacks=callbacks,
        )

    if provider == "azure-openai":
        from langchain_openai import AzureChatOpenAI

        api_key = settings.azure_openai_api_key.get_secret_value()
        if not api_key or not settings.azure_openai_endpoint:
            raise ValueError(
                "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT are required "
                "when llm_provider=azure-openai"
            )
        logger.info(
            "Using Azure OpenAI: deployment=%s endpoint=%s",
            settings.azure_openai_deployment,
            settings.azure_openai_endpoint,
        )
        return AzureChatOpenAI(
            azure_deployment=settings.azure_openai_deployment,
            azure_endpoint=settings.azure_openai_endpoint,
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
