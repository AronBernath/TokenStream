import os
from pathlib import Path
import sys

import httpx
import pytest


SERVICES_ROOT = Path(__file__).resolve().parents[2]
INGESTION_WORKER_ROOT = SERVICES_ROOT / "ingestion_worker"
COMMON_ROOT = SERVICES_ROOT / "common"

if str(INGESTION_WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(INGESTION_WORKER_ROOT))
if str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

os.environ.setdefault("EMBEDDER_URL", "http://embedder.test")
os.environ.setdefault("QDRANT_URL", "http://qdrant.test:6333")
os.environ.setdefault("DATA_DIR", str(Path.cwd()))

from worker.fetchers import fetch_source  # noqa: E402
from worker.main import validate_corpus_tree  # noqa: E402


def test_validate_corpus_tree_accepts_object_sources():
    corpus = {
        "corpus_id": "kb_pdf",
        "sources": [
            {
                "id": "source_pdf",
                "type": "object",
                "object_uri": "s3://bucket/env/tenant/corpus/source/hash/file.pdf",
                "format": "pdf",
            }
        ],
    }

    errors = validate_corpus_tree(corpus)

    assert errors == []


def test_validate_corpus_tree_rejects_missing_object_uri():
    corpus = {
        "corpus_id": "bad_corpus",
        "sources": [
            {
                "id": "broken_file",
                "type": "object",
                "format": "pdf",
            }
        ],
    }

    errors = validate_corpus_tree(corpus)

    assert errors
    assert "require object_uri" in errors[0]


def test_fetch_source_reads_s3_object_sources(tmp_path, monkeypatch):
    class FakeStorage:
        def get_bytes(self, object_uri: str) -> bytes:
            assert object_uri == "s3://rag-sources/dev/default/docs/source/hash/page.html"
            return b"<html><body>" + b"source text " * 8 + b"</body></html>"

    monkeypatch.setattr("worker.fetchers.S3ObjectStorage.from_env", lambda: FakeStorage())

    result = fetch_source(
        {
            "id": "source",
            "type": "object",
            "object_uri": "s3://rag-sources/dev/default/docs/source/hash/page.html",
            "format": "html",
        },
        data_dir=str(tmp_path),
    )

    assert result["format"] == "html"
    assert "source text" in result["content"]
    assert Path(result["local_path"]).is_file()


def test_fetch_source_reads_binary_object_sources_for_custom_processors(tmp_path, monkeypatch):
    payload = b"PK\x03\x04bundle bytes"

    class FakeStorage:
        def get_bytes(self, object_uri: str) -> bytes:
            assert object_uri == "s3://rag-sources/dev/default/docs/source/hash/bundle.zip"
            return payload

    monkeypatch.setattr("worker.fetchers.S3ObjectStorage.from_env", lambda: FakeStorage())

    result = fetch_source(
        {
            "id": "source",
            "type": "object",
            "object_uri": "s3://rag-sources/dev/default/docs/source/hash/bundle.zip",
            "format": "zip",
        },
        data_dir=str(tmp_path),
    )

    assert result["format"] == "zip"
    assert result["content"] == payload
    assert Path(result["local_path"]).suffix == ".zip"
    assert Path(result["local_path"]).read_bytes() == payload


def test_fetch_source_retries_empty_accepted_url_response(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        request = httpx.Request("GET", url)
        if len(calls) == 1:
            return httpx.Response(
                202,
                content=b"",
                headers={
                    "content-type": "text/html; charset=utf-8",
                    "x-amzn-waf-action": "challenge",
                },
                request=request,
            )
        return httpx.Response(
            200,
            text="<html><body>" + "source text " * 8 + "</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    monkeypatch.setattr("worker.fetchers.FETCH_MAX_ATTEMPTS", 2)
    monkeypatch.setattr("worker.fetchers.FETCH_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr("worker.fetchers.httpx.get", fake_get)

    result = fetch_source(
        {
            "id": "remote",
            "type": "url",
            "url": "https://example.test/document",
            "format": "html",
        },
        data_dir=str(tmp_path),
    )

    assert len(calls) == 2
    assert calls[0][1]["headers"]["User-Agent"]
    assert result["format"] == "html"
    assert "source text" in result["content"]


def test_fetch_source_reports_accepted_url_after_retries(tmp_path, monkeypatch):
    def fake_get(url, **kwargs):
        return httpx.Response(
            202,
            text="<html><body>challenge page</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("worker.fetchers.FETCH_MAX_ATTEMPTS", 2)
    monkeypatch.setattr("worker.fetchers.FETCH_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr("worker.fetchers.httpx.get", fake_get)

    with pytest.raises(ValueError, match="Remote source returned 202 Accepted"):
        fetch_source(
            {
                "id": "remote",
                "type": "url",
                "url": "https://example.test/document",
                "format": "html",
            },
            data_dir=str(tmp_path),
        )
