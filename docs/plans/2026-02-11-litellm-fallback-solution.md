# LiteLLM Fallback Solution for ekaiX

**Created:** 2026-02-11
**Implemented:** 2026-02-12
**Status:** ✅ COMPLETED
**Actual Effort:** ~2 hours
**Complexity:** Low (mostly configuration)

---

## Implementation Summary

**All steps completed successfully:**

1. ✅ **Installed LiteLLM** (v1.81.10) via pip
2. ✅ **Added LiteLLM Configuration** to `config.py`:
   - `litellm_enable: bool = True`
   - `litellm_allowed_fails: int = 3`
   - `litellm_cooldown_time: int = 60`
   - `litellm_num_retries: int = 2`
3. ✅ **Created LiteLLM Router** (`services/litellm_router.py`):
   - Builds router from primary + fallback provider config
   - Supports all 5 providers (Anthropic, OpenAI, Vertex AI, Azure OpenAI, Snowflake Cortex)
   - Order-based routing with automatic failover
4. ✅ **Created LangChain Wrapper** (`services/litellm_wrapper.py`):
   - `LiteLLMRouterWrapper` extends `BaseChatModel`
   - Exposes `.profile` attribute for Deep Agents compatibility
   - Implements LangChain-compatible interface (invoke, ainvoke, astream)
5. ✅ **Updated LLM Service** (`services/llm.py`):
   - `get_chat_model()` returns wrapped router when `litellm_enable=True`
   - Falls back to legacy single-provider mode when disabled
6. ✅ **Removed Custom Fallback Code**:
   - Deleted `_TRANSIENT_ERROR_PATTERNS` and `_is_transient_llm_error()` from `routers/agent.py`
   - Removed entire fallback try/except block (~200 lines) from `_run_agent()`
   - Deleted `build_fallback_orchestrator()` from `agents/orchestrator.py`
   - Deleted `get_fallback_chat_model()` and `_fallback_config_to_settings_patch()` from `services/llm.py`

**Services Status:** All services running successfully with no errors.

**Next Steps:** Test with real LLM providers to verify automatic failover behavior.

---

## Problem Statement

Current custom fallback implementation in `ai-service/routers/agent.py` is fragile and consumed significant development time (~1 day + 60% of weekly token quota) without reliable results. Need a production-grade, native solution.

## Solution: LiteLLM Router

LiteLLM is a production-grade LLM gateway that provides:
- ✅ Automatic failover between providers (Anthropic, OpenAI, Azure, Vertex AI, etc.)
- ✅ Zero custom error handling code
- ✅ Built-in retry logic with exponential backoff
- ✅ Automatic cooldown system for failing providers
- ✅ Load balancing across multiple deployments
- ✅ Cost tracking and usage monitoring
- ✅ Compatible with LangChain (works as drop-in replacement)

**GitHub:** https://github.com/BerriAI/litellm
**Docs:** https://docs.litellm.ai/

---

## Architecture

### Current Flow (Broken)
```
User Request → Agent → get_chat_model() → LangChain Model
                ↓
        Custom error handling in _run_agent()
                ↓
        Manual rebuild with fallback model
```

### New Flow (LiteLLM)
```
User Request → Agent → get_chat_model() → LiteLLM Router
                ↓
        Router automatically handles:
        - Retries on same provider
        - Failover to backup providers
        - Cooldown management
        - No code changes needed
```

---

## Installation

```bash
cd ai-service
source venv/bin/activate
pip install litellm
pip freeze > requirements.txt
```

---

## Implementation Plan

### Step 1: Add LiteLLM Configuration to Settings

**File:** `ai-service/config.py`

Add new settings for LiteLLM router:

```python
class Settings(BaseSettings):
    # ... existing settings ...

    # LiteLLM Router Settings
    litellm_enable: bool = True  # Enable LiteLLM router for fallback
    litellm_allowed_fails: int = 3  # Failures before cooldown
    litellm_cooldown_time: int = 60  # Cooldown duration in seconds
    litellm_num_retries: int = 2  # Retry attempts per provider
```

### Step 2: Create LiteLLM Router Configuration

**File:** `ai-service/services/litellm_router.py` (NEW)

```python
"""LiteLLM Router for automatic LLM fallback."""
import logging
import os
from litellm import Router
from config import get_settings

logger = logging.getLogger(__name__)

def build_litellm_router() -> Router:
    """
    Build LiteLLM Router with primary + fallback providers configured via Settings.

    Returns a Router instance that automatically handles failover between providers.
    Use router.acompletion() exactly like any LangChain model.
    """
    settings = get_settings()

    model_list = []

    # Primary provider (from UI config)
    primary_config = _build_provider_config(
        settings=settings,
        provider=settings.llm_provider,
        model=settings.llm_model,
        order=1,
    )
    if primary_config:
        model_list.append(primary_config)
        logger.info("LiteLLM primary: %s/%s", settings.llm_provider, settings.llm_model)

    # Fallback provider (if configured)
    if settings.llm_fallback_config:
        fallback = settings.llm_fallback_config
        fallback_config = _build_provider_config(
            settings=settings,
            provider=fallback.get("provider"),
            model=fallback.get("model"),
            order=2,
        )
        if fallback_config:
            model_list.append(fallback_config)
            logger.info("LiteLLM fallback: %s/%s", fallback.get("provider"), fallback.get("model"))

    if not model_list:
        raise ValueError("No LiteLLM providers configured")

    router = Router(
        model_list=model_list,
        enable_pre_call_checks=True,  # Required for order-based routing
        allowed_fails=settings.litellm_allowed_fails,
        cooldown_time=settings.litellm_cooldown_time,
        num_retries=settings.litellm_num_retries,
    )

    logger.info("LiteLLM Router initialized with %d provider(s)", len(model_list))
    return router


def _build_provider_config(
    settings,
    provider: str,
    model: str,
    order: int,
) -> dict | None:
    """Build a LiteLLM model_list entry for a specific provider."""
    if not provider or not model:
        return None

    config = {
        "model_name": "ekaiX-model",  # Logical name (always use this in code)
        "litellm_params": {
            "order": order,
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_tokens,
        },
    }

    if provider == "anthropic":
        config["litellm_params"]["model"] = f"anthropic/{model}"
        config["litellm_params"]["api_key"] = settings.anthropic_api_key

    elif provider == "openai":
        config["litellm_params"]["model"] = f"openai/{model}"
        config["litellm_params"]["api_key"] = settings.openai_api_key

    elif provider == "vertex-ai":
        config["litellm_params"]["model"] = f"vertex_ai/{model}"
        config["litellm_params"]["vertex_project"] = settings.vertex_ai_project_id
        config["litellm_params"]["vertex_location"] = settings.vertex_ai_location

    elif provider == "azure-openai":
        config["litellm_params"]["model"] = f"azure/{settings.azure_openai_deployment}"
        config["litellm_params"]["api_key"] = settings.azure_openai_api_key
        config["litellm_params"]["api_base"] = settings.azure_openai_endpoint
        config["litellm_params"]["api_version"] = settings.azure_openai_api_version

    elif provider == "snowflake-cortex":
        config["litellm_params"]["model"] = f"snowflake/{model}"
        config["litellm_params"]["account_identifier"] = settings.snowflake_account
        config["litellm_params"]["user"] = settings.snowflake_user
        config["litellm_params"]["password"] = settings.snowflake_password

    else:
        logger.warning("Unknown provider: %s", provider)
        return None

    return config
```

### Step 3: Update LLM Service to Use LiteLLM

**File:** `ai-service/services/llm.py`

Replace `get_chat_model()` function:

```python
from services.litellm_router import build_litellm_router

def get_chat_model():
    """
    Get the configured LLM (LiteLLM Router or LangChain model).

    If LiteLLM is enabled, returns a Router with automatic failover.
    Otherwise, returns a standard LangChain model.
    """
    settings = get_settings()

    # Use LiteLLM Router if enabled (recommended for production)
    if settings.litellm_enable:
        return build_litellm_router()

    # Fallback to legacy single-provider mode
    return _get_langchain_model()


def _get_langchain_model():
    """Legacy single-provider LangChain model (no fallback)."""
    # ... existing get_chat_model() code ...
```

### Step 4: Update Deep Agents to Use Router

**File:** `ai-service/agents/orchestrator.py`

The LiteLLM Router has the same interface as LangChain models, so Deep Agents should work without changes. Test with:

```python
from services.llm import get_chat_model

# This will now be a LiteLLM Router if litellm_enable=True
model = get_chat_model()

# Deep Agents uses model.profile - verify it works
agent = DeepAgents(
    model=model,
    # ... rest of config ...
)
```

**CRITICAL:** Test that `model.profile` still works with LiteLLM Router. If not, we may need to wrap the Router in a thin adapter class.

### Step 5: Remove Custom Fallback Code

**File:** `ai-service/routers/agent.py`

Remove ALL custom fallback logic:
1. Delete `_is_transient_llm_error()` function
2. Delete `build_fallback_orchestrator()` from orchestrator.py
3. Remove the try/except fallback block in `_run_agent()`
4. Remove `_TRANSIENT_ERROR_PATTERNS` constant
5. Clean up imports

### Step 6: Update Frontend Configuration

**File:** `frontend/src/app/llm-configuration/page.tsx`

No changes needed! The existing UI for Primary + Fallback provider selection works as-is. The backend will use these settings to configure LiteLLM Router.

---

## Configuration Example

### Via UI Settings (Stored in PostgreSQL)

```json
{
  "llm_provider": "anthropic",
  "llm_model": "claude-sonnet-4-20250514",
  "llm_fallback_config": {
    "provider": "openai",
    "model": "gpt-4-turbo"
  }
}
```

### LiteLLM Router Result

```python
# Automatically configured as:
model_list = [
    {
        "model_name": "ekaiX-model",
        "litellm_params": {
            "model": "anthropic/claude-sonnet-4-20250514",
            "api_key": "...",
            "order": 1,  # Primary
        },
    },
    {
        "model_name": "ekaiX-model",
        "litellm_params": {
            "model": "openai/gpt-4-turbo",
            "api_key": "...",
            "order": 2,  # Fallback
        },
    },
]
```

---

## Testing Plan

### 1. Unit Test Router Creation

```python
def test_litellm_router_build():
    """Verify router builds correctly from settings."""
    router = build_litellm_router()
    assert router is not None
    assert len(router.model_list) >= 1
```

### 2. Integration Test Failover

```python
async def test_litellm_failover():
    """Verify automatic failover on provider failure."""
    # Configure primary with invalid API key (will fail)
    # Configure fallback with valid API key
    router = build_litellm_router()

    response = await router.acompletion(
        model="ekaiX-model",
        messages=[{"role": "user", "content": "Hello"}],
    )

    # Should succeed via fallback
    assert response is not None
```

### 3. E2E Test with Discovery Pipeline

Use Playwright to:
1. Set primary provider to Anthropic (valid key)
2. Set fallback to OpenAI (valid key)
3. Create a data product
4. Verify discovery completes successfully
5. Check logs to confirm which provider was used

### 4. E2E Test Fallback Trigger

Use Playwright to:
1. Set primary provider to Anthropic (INVALID key)
2. Set fallback to OpenAI (valid key)
3. Create a data product
4. Verify discovery completes via fallback
5. Check logs to confirm fallback was triggered

---

## Migration Steps

1. **Install LiteLLM** (5 min)
   ```bash
   pip install litellm
   ```

2. **Add router config file** (15 min)
   - Create `services/litellm_router.py`
   - Copy code from Step 2 above

3. **Update llm.py** (10 min)
   - Modify `get_chat_model()` to use router
   - Keep old code as `_get_langchain_model()` fallback

4. **Test with Deep Agents** (30 min)
   - Verify `model.profile` still works
   - If not, create adapter wrapper

5. **Remove custom fallback code** (20 min)
   - Clean up agent.py
   - Remove orchestrator fallback function
   - Remove error pattern constants

6. **E2E testing** (60 min)
   - Test normal operation
   - Test failover scenario
   - Verify logs show correct behavior

**Total Estimated Time:** 2-3 hours

---

## Benefits

| Aspect | Custom Implementation | LiteLLM Router |
|--------|----------------------|----------------|
| **Code complexity** | ~200 lines custom error handling | ~80 lines config |
| **Reliability** | Fragile, breaks easily | Production-tested |
| **Maintenance** | High (fix for each LLM) | Low (vendor-maintained) |
| **Features** | Basic retry | Retry + cooldown + load balancing |
| **Monitoring** | Manual logging | Built-in metrics |
| **Cost tracking** | None | Automatic |
| **Testing** | Difficult to test | Easy to test |

---

## Rollback Plan

If LiteLLM doesn't work:

1. Set `litellm_enable=False` in config
2. System falls back to `_get_langchain_model()` (single provider, no fallback)
3. No data loss, no downtime

---

## Open Questions

1. **Does LiteLLM Router expose `.profile` attribute for Deep Agents?**
   - If not, need a thin wrapper class
   - Test this first before proceeding

2. **Does LangGraph work with LiteLLM Router?**
   - Should work (same interface as LangChain models)
   - Verify with simple test

3. **Redis requirement?**
   - LiteLLM can use Redis for distributed cooldown state
   - We already have Redis running
   - Optional: configure router to use it

---

## References

- **LiteLLM Docs:** https://docs.litellm.ai/
- **Routing & Fallbacks:** https://docs.litellm.ai/docs/routing-load-balancing
- **Router Architecture:** https://docs.litellm.ai/docs/router_architecture
- **LangChain Integration:** https://docs.litellm.ai/docs/langchain/
- **GitHub:** https://github.com/BerriAI/litellm

---

## Decision Log

- **2026-02-11:** Decided to use LiteLLM instead of custom fallback implementation
- **Rationale:** Production-grade solution, zero custom code, vendor-maintained
- **Risk:** Unknown compatibility with Deep Agents `.profile` attribute
- **Mitigation:** Test first, create adapter wrapper if needed
