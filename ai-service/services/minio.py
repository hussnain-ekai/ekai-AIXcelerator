"""MinIO client management for artifact and document storage.

Provides bucket initialization, object upload/download, and listing.
Manages buckets: artifacts, documents, workspace.
"""

from io import BytesIO

from minio import Minio

_client: Minio | None = None


def get_client(
    endpoint: str,
    access_key: str,
    secret_key: str,
    secure: bool = False,
) -> Minio:
    """Create or return the singleton MinIO client.

    Args:
        endpoint: MinIO host:port (e.g. localhost:9000).
        access_key: MinIO access key.
        secret_key: MinIO secret key.
        secure: Whether to use HTTPS.

    Returns:
        The shared Minio client instance.
    """
    global _client
    if _client is None:
        _client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
    return _client


def ensure_bucket(client: Minio, bucket: str) -> None:
    """Create the bucket if it does not already exist."""
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def upload_file(
    client: Minio,
    bucket: str,
    path: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> None:
    """Upload bytes to MinIO at the given bucket/path."""
    client.put_object(bucket, path, BytesIO(data), len(data), content_type=content_type)


def download_file(client: Minio, bucket: str, path: str) -> bytes:
    """Download an object from MinIO and return its contents as bytes."""
    response = client.get_object(bucket, path)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def list_objects(client: Minio, bucket: str, prefix: str) -> list[dict[str, str]]:
    """List all objects under a prefix in a bucket.

    Returns a list of dicts with name, size, and last_modified.
    """
    objects = client.list_objects(bucket, prefix=prefix, recursive=True)
    return [
        {
            "name": obj.object_name,
            "size": str(obj.size),
            "last_modified": str(obj.last_modified),
        }
        for obj in objects
    ]


def health_check(client: Minio) -> bool:
    """Return True if MinIO is reachable by checking for the artifacts bucket."""
    try:
        client.bucket_exists("artifacts")
        return True
    except Exception:
        return False
