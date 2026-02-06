"""MinIO-backed artifact storage backend.

Manages binary artifacts (ERD images, YAML files, BRD documents, exports)
in MinIO buckets organized by data product ID:
    - artifacts/{data_product_id}/erd/
    - artifacts/{data_product_id}/yaml/
    - artifacts/{data_product_id}/brd/
    - documents/uploads/
    - documents/extracted/
"""

from dataclasses import dataclass

from minio import Minio

from config import get_settings
from services import minio as minio_service


def _get_artifacts_bucket() -> str:
    return get_settings().minio_artifacts_bucket


def _get_documents_bucket() -> str:
    return get_settings().minio_documents_bucket


@dataclass
class MinIOFilesystemBackend:
    """Artifact and document storage backed by MinIO."""

    client: Minio

    def _ensure_buckets(self) -> None:
        """Create required buckets if they do not exist."""
        minio_service.ensure_bucket(self.client, _get_artifacts_bucket())
        minio_service.ensure_bucket(self.client, _get_documents_bucket())

    def upload_artifact(
        self,
        data_product_id: str,
        artifact_type: str,
        filename: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload an artifact and return the object path."""
        self._ensure_buckets()
        path = f"{data_product_id}/{artifact_type}/{filename}"
        minio_service.upload_file(self.client, _get_artifacts_bucket(), path, data, content_type)
        return path

    def download_artifact(self, data_product_id: str, artifact_type: str, filename: str) -> bytes:
        """Download an artifact by its components."""
        path = f"{data_product_id}/{artifact_type}/{filename}"
        return minio_service.download_file(self.client, _get_artifacts_bucket(), path)

    def list_artifacts(self, data_product_id: str) -> list[dict[str, str]]:
        """List all artifacts under a data product."""
        return minio_service.list_objects(self.client, _get_artifacts_bucket(), prefix=data_product_id)

    def upload_document(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Upload a user document and return the object path."""
        self._ensure_buckets()
        minio_service.upload_file(self.client, _get_documents_bucket(), path, data, content_type)
        return path

    def download_document(self, path: str) -> bytes:
        """Download a document from the documents bucket."""
        return minio_service.download_file(self.client, _get_documents_bucket(), path)
