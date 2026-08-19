import sys
import types
from pathlib import Path

import pytest


COMMON_ROOT = Path(__file__).resolve().parent.parent / "services" / "common"
if str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

from common.object_storage import (  # noqa: E402
    S3ObjectStorage,
    parse_s3_uri,
    sanitize_object_name,
    sha256_hex,
)


class FakeS3Error(Exception):
    pass


class FakeObjectResponse:
    def __init__(self, content: bytes):
        self.content = content
        self.closed = False
        self.released = False

    def read(self):
        return self.content

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class FakeMinioClient:
    instances = []

    def __init__(self, endpoint, *, access_key, secret_key, secure, region):
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.secure = secure
        self.region = region
        self.buckets = set()
        self.puts = []
        self.objects = {}
        self.last_response = None
        FakeMinioClient.instances.append(self)

    def bucket_exists(self, bucket):
        return bucket in self.buckets

    def make_bucket(self, bucket):
        self.buckets.add(bucket)

    def put_object(self, bucket, object_name, stream, *, length, content_type, metadata):
        content = stream.read()
        self.puts.append(
            {
                "bucket": bucket,
                "object_name": object_name,
                "content": content,
                "length": length,
                "content_type": content_type,
                "metadata": metadata,
            }
        )
        self.objects[(bucket, object_name)] = content

    def get_object(self, bucket, object_name):
        self.last_response = FakeObjectResponse(self.objects[(bucket, object_name)])
        return self.last_response


@pytest.fixture
def fake_minio(monkeypatch):
    FakeMinioClient.instances = []
    minio_mod = types.ModuleType("minio")
    minio_error_mod = types.ModuleType("minio.error")
    minio_mod.Minio = FakeMinioClient
    minio_error_mod.S3Error = FakeS3Error
    monkeypatch.setitem(sys.modules, "minio", minio_mod)
    monkeypatch.setitem(sys.modules, "minio.error", minio_error_mod)
    return FakeMinioClient


def test_sanitize_object_name_uses_basename_and_safe_fallback():
    assert sanitize_object_name("../unsafe name?.pdf") == "unsafe_name_.pdf"
    assert sanitize_object_name("") == "upload.bin"
    assert len(sanitize_object_name("x" * 300)) == 160


def test_parse_s3_uri_accepts_bucket_and_object_name():
    assert parse_s3_uri("s3://rag-sources/env/tenant/file.pdf") == ("rag-sources", "env/tenant/file.pdf")


@pytest.mark.parametrize("uri", ["", "http://bucket/key", "s3://bucket", "s3:///missing-bucket/key"])
def test_parse_s3_uri_rejects_invalid_uris(uri):
    with pytest.raises(ValueError):
        parse_s3_uri(uri)


def test_put_source_bytes_creates_bucket_and_returns_content_addressed_uri(fake_minio):
    storage = S3ObjectStorage(
        endpoint="minio.test:9000",
        access_key="access",
        secret_key="secret",
        bucket="rag-sources",
        secure=True,
        region="eu-test-1",
    )

    stored = storage.put_source_bytes(
        environment="dev",
        tenant_id="tenant-a",
        corpus_id="corpus",
        source_id="source",
        original_name="unsafe name?.pdf",
        content=b"pdf bytes",
        content_type="application/pdf",
        metadata={"page": 1, "verified": True},
    )

    assert storage.client.bucket_exists("rag-sources")
    assert stored.content_hash == sha256_hex(b"pdf bytes")
    assert stored.size_bytes == 9
    assert stored.object_name == f"dev/tenant-a/corpus/source/{stored.content_hash}/unsafe_name_.pdf"
    assert stored.object_uri == f"s3://rag-sources/{stored.object_name}"
    assert storage.client.puts == [
        {
            "bucket": "rag-sources",
            "object_name": stored.object_name,
            "content": b"pdf bytes",
            "length": 9,
            "content_type": "application/pdf",
            "metadata": {"page": "1", "verified": "True"},
        }
    ]


def test_get_bytes_reads_and_releases_minio_response(fake_minio):
    storage = S3ObjectStorage(
        endpoint="minio.test:9000",
        access_key="access",
        secret_key="secret",
        bucket="rag-sources",
    )
    storage.client.objects[("rag-sources", "path/file.txt")] = b"hello"

    assert storage.get_bytes("s3://rag-sources/path/file.txt") == b"hello"
    assert storage.client.last_response.closed is True
    assert storage.client.last_response.released is True
