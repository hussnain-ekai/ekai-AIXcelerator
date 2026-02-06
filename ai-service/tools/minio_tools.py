"""LangChain tools for MinIO artifact storage.

Tools manage generated artifacts in MinIO buckets:
    - Store and retrieve ERD diagrams, YAML files, and BRD documents
    - Manage uploaded documents and extracted content
    - Organize artifacts by data product ID
"""

import json
import logging
from uuid import uuid4

from langchain.tools import tool

from config import get_settings
from services import minio as minio_service
from services import postgres as pg_service

logger = logging.getLogger(__name__)


def _get_artifacts_bucket() -> str:
    """Get the artifacts bucket name from config."""
    return get_settings().minio_artifacts_bucket

CONTENT_TYPES: dict[str, str] = {
    "erd": "application/json",
    "yaml": "text/yaml",
    "brd": "application/json",
    "quality_report": "application/json",
}


def _get_client() -> minio_service.Minio:
    """Return the global MinIO client, raising if not initialized."""
    if minio_service._client is None:
        raise RuntimeError("MinIO client not initialized. Start the application first.")
    return minio_service._client


async def upload_artifact_programmatic(
    data_product_id: str,
    artifact_type: str,
    filename: str,
    content: str,
) -> dict:
    """Upload an artifact without going through LangChain tool decorator.

    Used by the safety net in agent.py when the LLM fails to call tools.
    """
    client = _get_client()
    content_type = CONTENT_TYPES.get(artifact_type, "application/octet-stream")
    content_bytes = content.encode("utf-8")
    artifact_id = str(uuid4())
    version = 1

    try:
        pool = pg_service._pool
        if pool is not None:
            sql = """
            INSERT INTO artifacts (id, data_product_id, artifact_type, minio_path, filename, file_size_bytes, content_type, created_by)
            VALUES ($1::uuid, $2::uuid, $3::artifact_type, $4, $5, $6, $7, $8)
            RETURNING version
            """
            placeholder_path = f"{data_product_id}/{artifact_type}/{filename}"
            rows = await pg_service.query(
                pool, sql,
                artifact_id, data_product_id, artifact_type,
                placeholder_path, filename, len(content_bytes), content_type, "agent",
            )
            if rows and len(rows) > 0:
                version = rows[0]["version"] if "version" in rows[0].keys() else 1
            final_path = f"{data_product_id}/{artifact_type}/v{version}/{filename}"
            await pg_service.execute(pool, "UPDATE artifacts SET minio_path = $1 WHERE id = $2::uuid", final_path, artifact_id)
            logger.info("Artifact persisted to PostgreSQL: %s/%s v%d", data_product_id, artifact_type, version)
    except Exception as e:
        logger.warning("Failed to persist artifact to PostgreSQL: %s", e)
        version = 1

    final_path = f"{data_product_id}/{artifact_type}/v{version}/{filename}"
    minio_service.ensure_bucket(client, _get_artifacts_bucket())
    minio_service.upload_file(client, _get_artifacts_bucket(), final_path, content_bytes, content_type=content_type)
    return {"status": "ok", "artifact_id": artifact_id, "version": version}


@tool
async def upload_artifact(
    data_product_id: str,
    artifact_type: str,
    filename: str,
    content: str,
) -> str:
    """Upload an artifact to MinIO and persist metadata to PostgreSQL.

    Artifacts are stored at: artifacts/{data_product_id}/{artifact_type}/v{version}/{filename}
    A reference row is also written to the PostgreSQL artifacts table.
    Version is auto-incremented by the database trigger.

    Args:
        data_product_id: UUID of the data product.
        artifact_type: One of 'erd', 'yaml', 'brd', 'quality_report'.
        filename: Name for the stored file.
        content: The file content as a string.
    """
    client = _get_client()
    content_type = CONTENT_TYPES.get(artifact_type, "application/octet-stream")
    content_bytes = content.encode("utf-8")

    # Persist artifact metadata to PostgreSQL first to get the auto-incremented version
    artifact_id = str(uuid4())
    version = 1  # Default, will be updated by query result

    try:
        pool = pg_service._pool
        if pool is not None:
            # Insert with RETURNING to get the auto-generated version
            sql = """
            INSERT INTO artifacts (id, data_product_id, artifact_type, minio_path, filename, file_size_bytes, content_type, created_by)
            VALUES ($1::uuid, $2::uuid, $3::artifact_type, $4, $5, $6, $7, $8)
            RETURNING version
            """
            # Use placeholder path first, we'll update after getting version
            placeholder_path = f"{data_product_id}/{artifact_type}/{filename}"
            rows = await pg_service.query(
                pool, sql,
                artifact_id,
                data_product_id,
                artifact_type,
                placeholder_path,
                filename,
                len(content_bytes),
                content_type,
                "agent",
            )
            if rows and len(rows) > 0:
                version = rows[0]["version"] if "version" in rows[0].keys() else 1

            # Now update the path with the actual version
            final_path = f"{data_product_id}/{artifact_type}/v{version}/{filename}"
            update_sql = "UPDATE artifacts SET minio_path = $1 WHERE id = $2::uuid"
            await pg_service.execute(pool, update_sql, final_path, artifact_id)

            logger.info("Artifact persisted to PostgreSQL: %s/%s v%d", data_product_id, artifact_type, version)
    except Exception as e:
        logger.warning("Failed to persist artifact to PostgreSQL: %s", e)
        # Fall back to version 1 path if DB fails
        version = 1

    # Upload to MinIO with version in path
    final_path = f"{data_product_id}/{artifact_type}/v{version}/{filename}"
    minio_service.ensure_bucket(client, _get_artifacts_bucket())
    minio_service.upload_file(
        client,
        _get_artifacts_bucket(),
        final_path,
        content_bytes,
        content_type=content_type,
    )

    return json.dumps({
        "status": "ok",
        "bucket": _get_artifacts_bucket(),
        "path": final_path,
        "artifact_id": artifact_id,
        "version": version,
    })


@tool
def retrieve_artifact(bucket: str, path: str) -> str:
    """Download an artifact from MinIO and return its content as a string.

    Args:
        bucket: The MinIO bucket name.
        path: Object path within the bucket.
    """
    client = _get_client()
    data = minio_service.download_file(client, bucket, path)

    return data.decode("utf-8")


@tool
def list_artifacts(data_product_id: str) -> str:
    """List all artifacts stored for a data product.

    Returns a JSON array of objects with name, size, and last_modified.

    Args:
        data_product_id: UUID of the data product.
    """
    client = _get_client()
    objects = minio_service.list_objects(client, _get_artifacts_bucket(), prefix=data_product_id)

    return json.dumps(objects)
