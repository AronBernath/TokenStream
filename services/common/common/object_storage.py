"""S3-compatible object storage utilities for registry-managed sources."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


def sanitize_object_name(value: str) -> str:
    name = Path(value or "").name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name[:160] or "upload.bin"


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class StoredObject:
    object_uri: str
    content_hash: str
    size_bytes: int
    content_type: str
    object_name: str


class S3ObjectStorage:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
        region: str | None = None,
    ):
        if not endpoint:
            raise ValueError("S3 endpoint is required")
        if not access_key:
            raise ValueError("S3 access key is required")
        if not secret_key:
            raise ValueError("S3 secret key is required")
        if not bucket:
            raise ValueError("S3 bucket is required")
        try:
            from minio import Minio
            from minio.error import S3Error
        except ImportError as exc:
            raise ValueError("minio package is required for S3-compatible object storage") from exc
        self.bucket = bucket
        self._s3_error = S3Error
        self.client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            region=region,
        )

    @classmethod
    def from_env(cls, prefix: str = "RAG_OBJECT_STORAGE_") -> "S3ObjectStorage":
        endpoint = os.environ.get(f"{prefix}ENDPOINT", os.environ.get("MINIO_ENDPOINT", "")).strip()
        access_key = os.environ.get(f"{prefix}ACCESS_KEY", os.environ.get("MINIO_ROOT_USER", "")).strip()
        secret_key = os.environ.get(f"{prefix}SECRET_KEY", os.environ.get("MINIO_ROOT_PASSWORD", "")).strip()
        bucket = os.environ.get(f"{prefix}BUCKET", os.environ.get("MINIO_BUCKET", "rag-sources")).strip()
        secure = os.environ.get(f"{prefix}SECURE", "false").strip().lower() in {"1", "true", "yes"}
        region = os.environ.get(f"{prefix}REGION", "").strip() or None
        return cls(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            secure=secure,
            region=region,
        )

    def ensure_bucket(self) -> None:
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
        except self._s3_error as exc:
            raise ValueError(f"object storage bucket is not available: {exc}") from exc

    def put_source_bytes(
        self,
        *,
        environment: str | None,
        tenant_id: str | None,
        corpus_id: str,
        source_id: str,
        original_name: str,
        content: bytes,
        content_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> StoredObject:
        self.ensure_bucket()
        content_hash = sha256_hex(content)
        safe_name = sanitize_object_name(original_name)
        object_name = "/".join(
            [
                environment or "default-env",
                tenant_id or "default-tenant",
                corpus_id,
                source_id,
                content_hash,
                safe_name,
            ]
        )
        stream = BytesIO(content)
        self.client.put_object(
            self.bucket,
            object_name,
            stream,
            length=len(content),
            content_type=content_type or "application/octet-stream",
            metadata={str(k): str(v) for k, v in (metadata or {}).items()},
        )
        return StoredObject(
            object_uri=f"s3://{self.bucket}/{object_name}",
            content_hash=content_hash,
            size_bytes=len(content),
            content_type=content_type or "application/octet-stream",
            object_name=object_name,
        )

    def import_source_bytes(self, **kwargs: Any) -> StoredObject:
        return self.put_source_bytes(**kwargs)

    def get_bytes(self, object_uri: str) -> bytes:
        bucket, object_name = parse_s3_uri(object_uri)
        response = self.client.get_object(bucket, object_name)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def export_bytes(self, object_uri: str) -> bytes:
        return self.get_bytes(object_uri)


def parse_s3_uri(object_uri: str) -> tuple[str, str]:
    parsed = urlparse(object_uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError(f"invalid S3 object URI: {object_uri}")
    return parsed.netloc, parsed.path.lstrip("/")
