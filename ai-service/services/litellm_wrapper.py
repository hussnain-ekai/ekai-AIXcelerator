"""LangChain-compatible LiteLLM Router wrapper for Deep Agents.

Uses ChatLiteLLMRouter from the langchain-litellm package (v0.5.1+).
This is a proper LangChain BaseChatModel with native bind_tools() support,
which Deep Agents requires for tool-calling subagents.

The wrapper:
1. Takes a litellm.Router instance (built by litellm_router.py)
2. Wraps it in ChatLiteLLMRouter (proper LangChain model)
3. Sets streaming=True for SSE compatibility
4. Adds streaming fallback for initial connection errors (403, 401, etc.)

Streaming Fallback Issue:
    LiteLLM Router's built-in fallback only handles errors raised BEFORE the
    stream is created (inside async_function_with_fallbacks). However, some
    providers (Vertex AI) use lazy connections — the actual HTTP request happens
    during the first chunk iteration, AFTER the router returns the stream wrapper.
    The router's FallbackStreamWrapper only catches MidStreamFallbackError, so
    auth/permission errors (403) propagate up unhandled.

    Our subclass catches these errors during _astream iteration and retries with
    the fallback model group.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, List, Optional

from langchain_core.callbacks.manager import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_litellm import ChatLiteLLMRouter
from langchain_litellm.chat_models.litellm import (
    _convert_delta_to_message_chunk,
    _create_usage_metadata,
)
from litellm import Router

logger = logging.getLogger(__name__)

# Error patterns that should trigger fallback (initial connection failures)
_FALLBACK_ERROR_PATTERNS = (
    "permission denied",
    "permission_denied",
    "403",
    "401",
    "unauthorized",
    "authentication",
    "invalid_api_key",
    "invalid api key",
    "quota exceeded",
    "rate limit",
    "429",
    "503",
    "service unavailable",
    "timeout",
    "connection error",
    "connect timeout",
)


def _should_fallback(error: Exception) -> bool:
    """Check if an error should trigger fallback to the backup provider."""
    error_str = str(error).lower()
    error_type = type(error).__name__.lower()
    return any(
        pattern in error_str or pattern in error_type
        for pattern in _FALLBACK_ERROR_PATTERNS
    )


class ChatLiteLLMRouterWithFallback(ChatLiteLLMRouter):
    """ChatLiteLLMRouter with streaming fallback for initial connection errors.

    Overrides _astream to catch errors during the first chunk iteration
    and retry with the fallback model group.
    """

    fallback_model_group: Optional[str] = None

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Stream with automatic fallback on initial connection errors."""
        try:
            async for chunk in self._do_astream(
                messages, stop, run_manager, **kwargs
            ):
                yield chunk
        except Exception as e:
            if self.fallback_model_group and _should_fallback(e):
                logger.warning(
                    "Primary stream failed (%s: %s). Falling back to %s.",
                    type(e).__name__,
                    str(e)[:200],
                    self.fallback_model_group,
                )
                async for chunk in self._do_astream(
                    messages,
                    stop,
                    run_manager,
                    model_override=self.fallback_model_group,
                    **kwargs,
                ):
                    yield chunk
            else:
                raise

    async def _do_astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        model_override: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Execute the actual streaming call against the router."""
        default_chunk_class = AIMessageChunk
        message_dicts, params = self._create_message_dicts(messages, stop)
        params = {**params, **kwargs, "stream": True}
        params = {k: v for k, v in params.items() if v is not None}
        params["stream_options"] = self.stream_options
        self._prepare_params_for_router(params)

        # Override model group for fallback
        if model_override:
            params["model"] = model_override

        async for chunk in await self.router.acompletion(
            messages=message_dicts, **params
        ):
            usage_metadata = None
            if "usage" in chunk and chunk["usage"]:
                usage_metadata = _create_usage_metadata(chunk["usage"])

            if len(chunk["choices"]) == 0:
                if usage_metadata:
                    chunk_obj = default_chunk_class(
                        content="", usage_metadata=usage_metadata
                    )
                    cg_chunk = ChatGenerationChunk(message=chunk_obj)
                    if run_manager:
                        await run_manager.on_llm_new_token(
                            "", chunk=cg_chunk, **params
                        )
                    yield cg_chunk
                continue

            delta = chunk["choices"][0]["delta"]
            chunk = _convert_delta_to_message_chunk(delta, default_chunk_class)

            if usage_metadata and isinstance(chunk, AIMessageChunk):
                chunk.usage_metadata = usage_metadata
            default_chunk_class = chunk.__class__
            cg_chunk = ChatGenerationChunk(message=chunk)
            if run_manager:
                await run_manager.on_llm_new_token(
                    chunk.content, chunk=cg_chunk, **params
                )
            yield cg_chunk


def create_langchain_compatible_router(
    router: Router,
    primary_provider: str = "",
    temperature: float = 0.1,
    max_tokens: int = 64000,
    callbacks: list[Any] | None = None,
    fallback_model_group: str | None = None,
) -> BaseChatModel:
    """Create a LangChain-compatible ChatLiteLLMRouter from a litellm.Router.

    Uses ChatLiteLLMRouterWithFallback — a proper BaseChatModel with native
    bind_tools() support AND streaming fallback for initial connection errors.

    Args:
        router: The litellm.Router instance (from build_litellm_router())
        primary_provider: Primary provider name (for logging)
        temperature: LLM temperature
        max_tokens: Max output tokens
        callbacks: Optional LangChain callbacks (e.g. Langfuse)
        fallback_model_group: Fallback model group name for streaming errors

    Returns:
        ChatLiteLLMRouterWithFallback instance compatible with Deep Agents.
    """
    from services.litellm_router import PRIMARY_MODEL_GROUP

    model = ChatLiteLLMRouterWithFallback(
        router=router,
        model=PRIMARY_MODEL_GROUP,
        streaming=True,
        temperature=temperature,
        max_tokens=max_tokens,
        callbacks=callbacks,
        fallback_model_group=fallback_model_group,
    )

    logger.info(
        "ChatLiteLLMRouterWithFallback created (primary=%s, providers=%d, "
        "fallback_group=%s, streaming=True)",
        primary_provider,
        len(router.model_list) if hasattr(router, "model_list") else 0,
        fallback_model_group or "none",
    )

    return model
