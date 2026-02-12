"""Pydantic v2 models aligned with the ekaiX OpenAPI specification.

These models define the request/response contracts for the AI service endpoints
and the SSE event payloads used during agent streaming.
"""

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Agent messages
# ---------------------------------------------------------------------------


class MessageAttachment(BaseModel):
    """Reference to a document or artifact attached to a user message."""

    type: Literal["document", "artifact"]
    id: UUID


class AgentMessage(BaseModel):
    """A single message in an agent conversation."""

    role: Literal["user", "assistant", "system", "tool"]
    content: str
    timestamp: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# SSE streaming
# ---------------------------------------------------------------------------

StreamEventType = Literal[
    "token",
    "message_done",
    "tool_call",
    "tool_result",
    "phase_change",
    "artifact",
    "approval_request",
    "approval_response",
    "status",
    "error",
    "done",
    "pipeline_progress",
    "data_maturity",
]


class AgentStreamEvent(BaseModel):
    """Payload for a single Server-Sent Event during agent streaming."""

    type: StreamEventType
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------


class FileContent(BaseModel):
    """Base64-encoded file content attached to a user message."""

    filename: str
    content_type: str
    base64_data: str


class InvokeRequest(BaseModel):
    """Request body for POST /agent/message."""

    session_id: str
    data_product_id: UUID
    message: str = Field(min_length=1, max_length=10_000)
    attachments: list[MessageAttachment] = Field(default_factory=list)
    file_contents: list[FileContent] = Field(default_factory=list)


class RetryRequest(BaseModel):
    """Request body for POST /agent/retry."""

    session_id: str
    data_product_id: UUID
    message_id: str | None = None
    edited_content: str | None = None
    original_content: str | None = None


class InvokeResponse(BaseModel):
    """Synchronous response after accepting an agent message."""

    session_id: str
    message_id: UUID
    status: Literal["processing", "completed", "error"]


class InterruptRequest(BaseModel):
    """Request body for POST /agent/interrupt."""

    session_id: str
    reason: str = ""


class ApproveRequest(BaseModel):
    """Request body for POST /agent/approve."""

    session_id: str
    approved: bool


# ---------------------------------------------------------------------------
# Data Quality
# ---------------------------------------------------------------------------


class QualityCheckResult(BaseModel):
    """Score and details for a single quality check category."""

    score: int = Field(ge=0, le=100)
    details: str = ""


class QualityIssue(BaseModel):
    """A single data-quality issue found during discovery."""

    severity: Literal["critical", "warning", "info"]
    message: str
    affected_tables: list[str] = Field(default_factory=list)


class DataQualityResult(BaseModel):
    """Aggregate data-quality report produced after discovery."""

    overall_score: int = Field(ge=0, le=100)
    check_results: dict[str, QualityCheckResult] = Field(default_factory=dict)
    issues: list[QualityIssue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LLM Configuration
# ---------------------------------------------------------------------------

LLMProvider = Literal[
    "snowflake-cortex",
    "vertex-ai",
    "azure-openai",
    "anthropic",
    "openai",
]


class LLMConfigRequest(BaseModel):
    """Request to change the active LLM provider at runtime."""

    provider: LLMProvider
    model: str | None = None

    # Provider-specific credentials (optional — uses env defaults if omitted)
    cortex_model: str | None = None
    # Vertex AI — user-provided credentials
    vertex_credentials_json: str | None = None
    vertex_project: str | None = None
    vertex_location: str | None = None
    vertex_model: str | None = None
    # Anthropic
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    # OpenAI
    openai_api_key: str | None = None
    openai_model: str | None = None
    # Azure OpenAI
    azure_openai_api_key: str | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str | None = None

    # Optional fallback provider config
    fallback: dict[str, Any] | None = None


class LLMConfigResponse(BaseModel):
    """Response after applying an LLM config change."""

    status: Literal["ok", "error"]
    provider: str
    model: str
    error: str | None = None


class LLMTestRequest(BaseModel):
    """Request to test an LLM provider without applying it."""

    provider: LLMProvider
    model: str | None = None

    # Same optional credential fields
    cortex_model: str | None = None
    # Vertex AI
    vertex_credentials_json: str | None = None
    vertex_project: str | None = None
    vertex_location: str | None = None
    vertex_model: str | None = None
    # Anthropic
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    # OpenAI
    openai_api_key: str | None = None
    openai_model: str | None = None
    # Azure OpenAI
    azure_openai_api_key: str | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str | None = None


class LLMTestResponse(BaseModel):
    """Response from a test connection attempt."""

    status: Literal["ok", "error"]
    response_time_ms: int | None = None
    model_response: str | None = None
    error: str | None = None


class LLMFallbackStatus(BaseModel):
    """Fallback provider info."""

    provider: str
    model: str


class LLMStatusResponse(BaseModel):
    """Current active LLM status."""

    provider: str
    model: str
    is_override: bool = False
    fallback: LLMFallbackStatus | None = None
