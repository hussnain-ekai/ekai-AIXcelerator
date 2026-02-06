"""Health check endpoints for service monitoring and readiness probes."""

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("")
async def health_check() -> dict[str, str]:
    """Return basic service liveness status."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness_check() -> JSONResponse:
    """Return service readiness including dependency connectivity."""
    from services import minio as minio_service
    from services import neo4j as neo4j_service
    from services import postgres as postgres_service
    from services import redis as redis_service
    from services import snowflake as snowflake_service

    checks: dict[str, str] = {}

    try:
        pool = postgres_service._pool
        if pool:
            pg_ok = await postgres_service.health_check(pool)
            checks["postgresql"] = "ok" if pg_ok else "error"
        else:
            checks["postgresql"] = "not initialized"
    except Exception as e:
        checks["postgresql"] = f"error: {e}"

    try:
        driver = neo4j_service._driver
        if driver:
            neo4j_ok = await neo4j_service.health_check(driver)
            checks["neo4j"] = "ok" if neo4j_ok else "error"
        else:
            checks["neo4j"] = "not initialized"
    except Exception as e:
        checks["neo4j"] = f"error: {e}"

    try:
        client = redis_service._client
        if client:
            redis_ok = await redis_service.health_check(client)
            checks["redis"] = "ok" if redis_ok else "error"
        else:
            checks["redis"] = "not initialized"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    try:
        minio_client = minio_service._client
        if minio_client:
            minio_ok = minio_service.health_check(minio_client)
            checks["minio"] = "ok" if minio_ok else "error"
        else:
            checks["minio"] = "not initialized"
    except Exception as e:
        checks["minio"] = f"error: {e}"

    try:
        sf_ok = await snowflake_service.health_check()
        checks["snowflake"] = "ok" if sf_ok else "error"
    except Exception as e:
        checks["snowflake"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if all_ok else "degraded",
            "checks": checks,
        },
    )
