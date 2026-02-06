"""Agent conversation endpoints — message handling, SSE streaming, and control actions."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from config import get_settings
from models.schemas import (
    AgentStreamEvent,
    ApproveRequest,
    InterruptRequest,
    InvokeRequest,
    InvokeResponse,
)

logger = logging.getLogger(__name__)
_settings = get_settings()

# Discovery trigger constant
DISCOVERY_TRIGGER = "__START_DISCOVERY__"

router = APIRouter(prefix="/agent", tags=["Agent"])

# In-memory store for active streaming sessions.
# Maps session_id -> asyncio.Queue of SSE events.
_active_streams: dict[str, asyncio.Queue[dict | None]] = {}


@router.post("/message")
async def send_message(request: InvokeRequest) -> InvokeResponse:
    """Accept a user message and begin asynchronous agent processing.

    The message is dispatched to the Deep Agents orchestrator in a background
    task. The caller should subscribe to the SSE stream at
    /agent/stream/{session_id} to receive real-time responses.
    """
    message_id = uuid4()
    session_id = request.session_id
    logger.info(
        "Received message for session %s (message_id=%s): %s",
        session_id,
        message_id,
        request.message[:100],
    )

    # Create a queue for this session's SSE events
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    _active_streams[session_id] = queue

    # Launch the agent invocation in the background
    asyncio.create_task(_run_agent(session_id, request.message, str(request.data_product_id), queue))

    return InvokeResponse(
        session_id=session_id,
        message_id=message_id,
        status="processing",
    )


async def _get_data_product_info(data_product_id: str) -> dict | None:
    """Fetch data product details from PostgreSQL.

    Returns a dict with name, description, database, schemas, tables,
    or None if not found.
    """
    from services.postgres import get_pool, query
    from config import get_settings

    settings = get_settings()
    pool = await get_pool(settings.database_url)
    rows = await query(
        pool,
        """SELECT name, description, database_reference, schemas, tables
           FROM data_products WHERE id = $1""",
        data_product_id,
    )
    if not rows:
        return None

    r = rows[0]
    return {
        "name": r["name"],
        "description": r["description"] or "No description provided",
        "database": r["database_reference"],
        "schemas": r["schemas"] or [],
        "tables": r["tables"] or [],
    }


def _build_discovery_summary(
    pipeline_results: dict,
    dp_name: str,
    data_product_id: str,
) -> str:
    """Convert pipeline results into a structured summary for the LLM.

    The summary uses business language and avoids UUIDs, FQNs, and SQL.
    Technical details are in the pipeline results (used for artifacts only).
    """
    metadata = pipeline_results.get("metadata", [])
    classifications = pipeline_results.get("classifications", {})
    relationships = pipeline_results.get("relationships", [])
    quality = pipeline_results.get("quality", {})

    # Build table summaries with business names
    table_lines = []
    for table in metadata:
        fqn = table["fqn"]
        name = table["name"]
        classification = classifications.get(fqn, "UNKNOWN")
        biz_type = "transaction data" if classification == "FACT" else "reference data"
        col_count = len(table.get("columns", []))
        row_count = table.get("row_count")
        row_str = f", ~{row_count:,} records" if row_count else ""
        table_lines.append(f"  - {name} ({biz_type}, {col_count} fields{row_str})")

    # Build relationship summaries in plain language
    rel_lines = []
    # Build a lookup of fqn -> short name
    name_map = {t["fqn"]: t["name"] for t in metadata}
    for rel in relationships:
        src = name_map.get(rel["from_table"], rel["from_table"].split(".")[-1])
        tgt = name_map.get(rel["to_table"], rel["to_table"].split(".")[-1])
        via = rel.get("from_column", "")
        confidence = rel.get("confidence", 0)
        conf_str = "strong" if confidence >= 0.9 else "likely"
        rel_lines.append(f"  - {src} connects to {tgt} via {via} ({conf_str})")

    # Quality summary
    score = quality.get("overall_score", 0)
    completeness = quality.get("avg_completeness_pct", 0)
    issues = quality.get("issues", [])
    issue_summary = ""
    if issues:
        top_issues = issues[:3]
        issue_lines = [f"  - {i['message']}" for i in top_issues]
        issue_summary = "\nNotable issues:\n" + "\n".join(issue_lines)

    # Fact vs dimension counts
    fact_count = sum(1 for v in classifications.values() if v == "FACT")
    dim_count = sum(1 for v in classifications.values() if v == "DIMENSION")

    summary = f"""[INTERNAL CONTEXT — NOT FOR USER DISPLAY]

═══════════════════════════════════════════════════════
PRE-COMPUTED DISCOVERY RESULTS
═══════════════════════════════════════════════════════
Data Product: {dp_name}
Data Product ID (for tool calls only): {data_product_id}
Tables analyzed: {len(metadata)} ({fact_count} transaction, {dim_count} reference)

Tables:
{chr(10).join(table_lines)}

Connections found: {len(relationships)}
{chr(10).join(rel_lines) if rel_lines else '  (none detected)'}

Data Quality Score: {score}/100 (average completeness: {completeness:.0f}%)
{issue_summary}

═══════════════════════════════════════════════════════
YOUR TASK
═══════════════════════════════════════════════════════
All profiling, classification, relationship detection, data map construction,
and quality checks are ALREADY DONE. Artifacts are ALREADY saved.

Your ONLY job is to:
1. Interpret these results in 3-5 natural sentences (like a colleague giving a quick update)
2. Mention the quality score naturally
3. Highlight one interesting finding or issue
4. Ask ONE sharp business question about their domain

RULES:
- Do NOT call any tools — everything is already computed and saved
- Do NOT repeat the data above verbatim — interpret it in business language
- Refer to the data product as "{dp_name}"
- Use table short names (e.g. "your Customers table") not FQNs
- DATA ISOLATION: ONLY discuss the tables listed above. You know NOTHING about
  any other databases, schemas, or tables in this Snowflake account. They do not
  exist to you. NEVER mention or speculate about any other datasets.
══════════════════════════════════════════════════════════════════"""

    return summary


async def _run_agent(
    session_id: str,
    message: str,
    data_product_id: str,
    queue: asyncio.Queue[dict | None],
) -> None:
    """Run the orchestrator agent and push events to the SSE queue."""
    # Collect messages for persistence
    collected_messages: list[dict] = []
    current_assistant_content = ""
    # Track which LLM run_id is currently streaming to detect when a new
    # agent starts speaking (prevents concatenating subagent + orchestrator output)
    _current_run_id: str | None = None

    try:
        from agents.orchestrator import get_orchestrator
        from config import get_effective_settings

        # Check if this is a discovery trigger
        actual_message = message
        if message.strip() == DISCOVERY_TRIGGER:
            logger.info("Discovery trigger detected for session %s, running pipeline...", session_id)

            # 1. Get data product details
            dp_info = await _get_data_product_info(data_product_id)
            if dp_info is None:
                actual_message = "Please tell me about the data you want to analyze."
            elif not dp_info["tables"]:
                actual_message = f"I see you created '{dp_info['name']}' but no tables are selected yet. Please add tables in the Data Source Settings."
            else:
                # 2. Run deterministic pipeline (emits progress events to queue)
                from services.discovery_pipeline import run_discovery_pipeline

                pipeline_results = await run_discovery_pipeline(
                    data_product_id=data_product_id,
                    tables=dp_info["tables"],
                    database=dp_info["database"],
                    schemas=dp_info["schemas"],
                    queue=queue,
                )

                # 3. Build human-readable summary for the LLM
                actual_message = _build_discovery_summary(
                    pipeline_results, dp_info["name"], data_product_id,
                )
                logger.info("Pipeline complete, summary length: %d chars", len(actual_message))

        # Add user message to collection (use original message for non-internal triggers)
        if message.strip() != DISCOVERY_TRIGGER:
            collected_messages.append({
                "role": "user",
                "content": message,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            })

        # Langfuse tracing is now handled at the model level in services/llm.py
        # Each LLM call will be automatically traced with input/output
        settings = get_effective_settings()
        logger.info(
            "Starting agent for session %s (provider: %s, langfuse=%s)",
            session_id,
            settings.llm_provider,
            bool(settings.langfuse_public_key and settings.langfuse_secret_key),
        )

        # --- Data isolation: scope tools to this data product's database ---
        from tools.snowflake_tools import set_data_isolation_context

        dp_info_for_isolation = await _get_data_product_info(data_product_id)
        if dp_info_for_isolation:
            set_data_isolation_context(
                database=dp_info_for_isolation["database"],
                tables=dp_info_for_isolation["tables"],
            )
        else:
            set_data_isolation_context(database=None, tables=None)

        agent = get_orchestrator()
        config = {
            "configurable": {"thread_id": session_id},
            "recursion_limit": _settings.agent_recursion_limit,  # Shared between orchestrator + subagents
        }
        input_messages = {"messages": [HumanMessage(content=actual_message)]}

        # Stream events from the agent
        async for event in agent.astream_events(input_messages, config=config, version="v2"):
            kind = event.get("event", "")
            data = event.get("data", {})

            if kind == "on_chat_model_stream":
                # Token streaming — detect run boundaries to separate messages
                run_id = event.get("run_id", "")
                chunk = data.get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    # If a new LLM run starts, emit message_done to close previous bubble
                    if run_id and run_id != _current_run_id:
                        if _current_run_id is not None and current_assistant_content.strip():
                            await queue.put({
                                "type": "message_done",
                                "data": {"content": ""},
                            })
                        _current_run_id = run_id

                    # Extract text from structured content blocks if needed
                    content = chunk.content
                    if isinstance(content, list):
                        text_parts = [
                            block.get("text", "") if isinstance(block, dict) else str(block)
                            for block in content
                        ]
                        content = "".join(text_parts)
                    elif isinstance(content, dict):
                        content = content.get("text", str(content))

                    # Collect content for message persistence
                    current_assistant_content += content

                    await queue.put({
                        "type": "token",
                        "data": {"content": content},
                    })

            elif kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                tool_input = data.get("input", {})
                await queue.put({
                    "type": "tool_call",
                    "data": {
                        "tool": tool_name,
                        "input": tool_input if isinstance(tool_input, dict) else str(tool_input),
                    },
                })

            elif kind == "on_tool_end":
                tool_name = event.get("name", "unknown")
                output = data.get("output", "")
                # Truncate long outputs
                truncate_len = _settings.tool_output_truncate_length
                output_str = str(output)[:truncate_len] if output else ""

                # Detect artifact creation and emit artifact event
                if tool_name == "upload_artifact" and output_str:
                    try:
                        result = json.loads(output_str)
                        if result.get("status") == "ok":
                            path = result.get("path", "")
                            parts = path.split("/")
                            # path format: {data_product_id}/{artifact_type}/{filename}
                            if len(parts) >= 2:
                                artifact_type = parts[1]
                                await queue.put({
                                    "type": "artifact",
                                    "data": {
                                        "artifact_id": str(uuid4()),
                                        "artifact_type": artifact_type,
                                    },
                                })
                    except (json.JSONDecodeError, IndexError):
                        pass

                await queue.put({
                    "type": "tool_result",
                    "data": {
                        "tool": tool_name,
                        "output": output_str,
                    },
                })

            elif kind == "on_chain_end" and event.get("name") == "ekaix-orchestrator":
                # Final response — skip if we already streamed content via on_chat_model_stream.
                # The stream handler already sent all tokens to the client in real time.
                if current_assistant_content.strip():
                    # Content was already streamed, don't duplicate
                    pass
                else:
                    # Fallback: if nothing was streamed, emit the final messages
                    output = data.get("output", {})
                    if isinstance(output, dict):
                        messages = output.get("messages", [])
                        for msg in messages:
                            if isinstance(msg, AIMessage) and msg.content:
                                content = msg.content
                                if isinstance(content, list):
                                    text_parts = [
                                        block.get("text", "") if isinstance(block, dict) else str(block)
                                        for block in content
                                    ]
                                    content = "".join(text_parts)
                                elif isinstance(content, dict):
                                    content = content.get("text", str(content))
                                current_assistant_content += content
                                await queue.put({
                                    "type": "token",
                                    "data": {"content": content},
                                })

    except ValueError as e:
        # LangChain raises ValueError("No generations found in stream") when an LLM
        # produces an empty response (e.g. orchestrator after subagent did all the work).
        # This is benign — the subagent already delivered content.
        if "no generations" in str(e).lower():
            logger.info("Agent %s produced empty response (expected after subagent delegation)", session_id)
        else:
            logger.exception("Agent execution failed for session %s: %s", session_id, e)
            await queue.put({
                "type": "error",
                "data": {"message": f"{type(e).__name__}: {e}"},
            })
    except Exception as e:
        logger.exception("Agent execution failed for session %s: %s", session_id, e)
        await queue.put({
            "type": "error",
            "data": {"message": f"{type(e).__name__}: {e}"},
        })

    finally:
        # Add final assistant message to collection if we have content
        if current_assistant_content.strip():
            collected_messages.append({
                "role": "assistant",
                "content": current_assistant_content,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            })

        # Persist messages to Redis for session recovery
        if collected_messages:
            try:
                from services import redis as redis_service
                from services.postgres import get_pool, execute
                from config import get_settings

                settings = get_settings()
                client = await redis_service.get_client(settings.redis_url)
                history_key = f"agent:history:{session_id}"

                # Load existing history and append new messages
                existing = await redis_service.get_json(client, history_key)
                existing_messages = existing.get("messages", []) if existing else []
                all_messages = existing_messages + collected_messages

                await redis_service.set_json(
                    client,
                    history_key,
                    {"messages": all_messages, "data_product_id": data_product_id},
                    ttl=_settings.session_ttl_seconds,  # Use configured TTL
                )
                logger.info(
                    "Persisted %d messages to Redis for session %s (total: %d)",
                    len(collected_messages),
                    session_id,
                    len(all_messages),
                )

                # Also update data product state with session_id for recovery on page load
                pool = await get_pool(settings.database_url)
                await execute(
                    pool,
                    """UPDATE data_products
                       SET state = jsonb_set(state, '{session_id}', $1::jsonb)
                       WHERE id = $2::uuid""",
                    f'"{session_id}"',
                    data_product_id,
                )
                logger.info("Updated data product %s with session_id %s", data_product_id, session_id)
            except Exception as e:
                logger.warning("Failed to persist messages to Redis: %s", e)

        # Signal stream end
        await queue.put({
            "type": "done",
            "data": {"message": "Agent processing complete"},
        })
        await queue.put(None)  # Sentinel to close the generator


async def _event_generator(session_id: str) -> AsyncGenerator[str, None]:
    """Generate SSE-formatted events for a given agent session."""
    queue = _active_streams.get(session_id)

    if queue is None:
        # No active stream — send a waiting message then done
        event = AgentStreamEvent(
            type="done",
            data={"message": "No active processing for this session. Send a message first."},
            timestamp=datetime.now(tz=timezone.utc),
        )
        yield f"event: {event.type}\ndata: {json.dumps(event.model_dump(mode='json'))}\n\n"
        return

    # Send keepalive comment
    yield ": keepalive\n\n"

    try:
        while True:
            try:
                # Wait for events with timeout for keepalive
                item = await asyncio.wait_for(queue.get(), timeout=_settings.agent_stream_timeout)
            except asyncio.TimeoutError:
                # Send keepalive ping
                yield ": ping\n\n"
                continue

            if item is None:
                # Sentinel: stream is done
                break

            event = AgentStreamEvent(
                type=item["type"],
                data=item["data"],
                timestamp=datetime.now(tz=timezone.utc),
            )
            yield f"event: {event.type}\ndata: {json.dumps(event.model_dump(mode='json'))}\n\n"

            if item["type"] == "done":
                break

    finally:
        # Clean up the stream
        _active_streams.pop(session_id, None)


@router.get("/stream/{session_id}")
async def stream_response(session_id: str) -> StreamingResponse:
    """Subscribe to the agent response stream via Server-Sent Events."""
    return StreamingResponse(
        _event_generator(session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/interrupt")
async def interrupt_agent(request: InterruptRequest) -> dict[str, str]:
    """Interrupt a running agent session."""
    session_id = request.session_id
    logger.info("Interrupt requested for session %s: %s", session_id, request.reason)

    # Push an error event and close the stream
    queue = _active_streams.get(session_id)
    if queue:
        await queue.put({
            "type": "error",
            "data": {"message": f"Interrupted: {request.reason}"},
        })
        await queue.put({
            "type": "done",
            "data": {"message": "Session interrupted by user"},
        })
        await queue.put(None)

    return {"status": "interrupted", "session_id": session_id}


@router.post("/approve")
async def approve_action(request: ApproveRequest) -> dict[str, str]:
    """Approve or reject a pending agent action (e.g., publishing)."""
    logger.info(
        "Approval for session %s: approved=%s",
        request.session_id,
        request.approved,
    )

    # Resume the agent with the approval decision
    queue = _active_streams.get(request.session_id)
    if queue:
        status = "approved" if request.approved else "rejected"
        await queue.put({
            "type": "approval_response",
            "data": {"approved": request.approved, "status": status},
        })

    status = "approved" if request.approved else "rejected"
    return {"status": status, "session_id": request.session_id}


@router.get("/history/{session_id}")
async def get_history(session_id: str) -> dict:
    """Get conversation history for a session."""
    try:
        from services import redis as redis_service
        from config import get_settings

        settings = get_settings()
        client = await redis_service.get_client(settings.redis_url)
        key = f"agent:history:{session_id}"
        history = await redis_service.get_json(client, key)
        if history:
            return {
                "session_id": session_id,
                "messages": history.get("messages", []),
                "data_product_id": history.get("data_product_id"),
            }
    except Exception as e:
        logger.error("Failed to get history for session %s: %s", session_id, e)

    return {"session_id": session_id, "messages": []}
