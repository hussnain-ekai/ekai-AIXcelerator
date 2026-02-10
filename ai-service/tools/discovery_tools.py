"""LangChain tools for discovery Phase 2 — ERD construction from data description.

The build_erd_from_description tool loads the user-validated data description,
runs enhanced FK inference, builds the Neo4j graph, and saves the ERD artifact.
"""

import json
import logging
from contextvars import ContextVar
from uuid import uuid4

from langchain.tools import tool

logger = logging.getLogger(__name__)

# Context variable set by _run_agent so the tool can emit SSE artifact events
_sse_queue: ContextVar = ContextVar("_sse_queue", default=None)


@tool
async def build_erd_from_description(data_product_id: str) -> str:
    """Build the ERD (data map) using the saved data description for context.

    Runs FK inference enhanced with user-validated relationships, writes to
    the graph database, and saves the ERD artifact.
    Must be called AFTER save_data_description.

    Args:
        data_product_id: UUID of the data product.
    """
    from tools.postgres_tools import get_latest_data_description
    from services.discovery_pipeline import run_erd_pipeline

    # Load data description
    dd_raw = await get_latest_data_description.ainvoke(data_product_id)
    dd_result = json.loads(dd_raw)
    if dd_result.get("status") == "not_found":
        return json.dumps({"status": "error", "message": "No data description found. Call save_data_description first."})

    data_description = dd_result["description_json"]

    try:
        result = await run_erd_pipeline(data_product_id, data_description)
    except ValueError as e:
        return json.dumps({"status": "error", "message": str(e)})

    # Emit artifact SSE event if a queue is available
    queue = _sse_queue.get(None)
    erd_artifact_id = result.get("erd_artifact_id")
    if queue is not None and erd_artifact_id:
        await queue.put({
            "type": "artifact",
            "data": {
                "artifact_id": erd_artifact_id,
                "artifact_type": "erd",
            },
        })
        logger.info("build_erd_from_description: emitted ERD artifact event")

    return json.dumps({
        "status": "ok",
        "relationships_found": len(result.get("relationships", [])),
        "erd_artifact_id": erd_artifact_id,
    })
