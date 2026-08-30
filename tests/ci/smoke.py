from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


ORCHESTRATOR_API_KEY = os.environ.get("ORCHESTRATOR_API_KEY", "ci-orchestrator-key")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333").rstrip("/")
RETRIEVAL_API_URL = os.environ.get("RETRIEVAL_API_URL", "http://localhost:8000").rstrip("/")
ORCHESTRATOR_API_URL = os.environ.get("ORCHESTRATOR_API_URL", "http://localhost:8004").rstrip("/")
INGESTION_WORKER_URL = os.environ.get("INGESTION_WORKER_URL", "http://localhost:8002").rstrip("/")
DEV_UI_URL = os.environ.get("DEV_UI_URL", "http://localhost:8010").rstrip("/")
CORPUS_ID = "ci_docs"
COLLECTION = "corp_ci_ci-tenant_ci_docs"
VECTOR_SIZE = 4


def _request(method: str, url: str, payload: Any | None = None, headers: dict[str, str] | None = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"content-type": "application/json", **(headers or {})}
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        content = response.read()
        if not content:
            return None
        return json.loads(content.decode("utf-8"))


def _wait_for(name: str, url: str, expected_statuses: set[int] | None = None, timeout_s: int = 120) -> None:
    expected = expected_statuses or {200}
    deadline = time.monotonic() + timeout_s
    last_error = ""
    print(f"waiting for {name} at {url}", flush=True)
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status in expected:
                    print(f"{name} is ready", flush=True)
                    return
                last_error = f"HTTP {response.status}"
        except urllib.error.HTTPError as exc:
            if exc.code in expected:
                return
            last_error = f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)
    raise RuntimeError(f"{name} did not become ready at {url}: {last_error}")


def _seed_qdrant() -> None:
    print(f"seeding qdrant collection {COLLECTION}", flush=True)
    _request(
        "PUT",
        f"{QDRANT_URL}/collections/{COLLECTION}",
        {
            "vectors": {
                "size": VECTOR_SIZE,
                "distance": "Cosine",
            }
        },
    )
    print("qdrant seed data is ready", flush=True)
    _request(
        "PUT",
        f"{QDRANT_URL}/collections/{COLLECTION}/points?wait=true",
        {
            "points": [
                {
                    "id": 1,
                    "vector": [1.0, 0.0, 0.0, 0.0],
                    "payload": {
                        "chunk_id": "ci-docs-001",
                        "doc_id": "ci-guide",
                        "doc_type": "guide",
                        "title": "TokenStream Retrieval Smoke Guide",
                        "section_id": "retrieval",
                        "source_url": "https://example.invalid/tokenstream/ci-guide",
                        "tags": ["ci", "retrieval"],
                        "text": "TokenStream retrieval smoke tests use mock embeddings and seeded Qdrant data.",
                        "metadata": {
                            "source": "ci",
                            "language": "en",
                        },
                    },
                },
                {
                    "id": 2,
                    "vector": [0.0, 1.0, 0.0, 0.0],
                    "payload": {
                        "chunk_id": "ci-docs-002",
                        "doc_id": "ci-policy",
                        "doc_type": "policy",
                        "title": "TokenStream CI Policy",
                        "section_id": "policy",
                        "source_url": "https://example.invalid/tokenstream/ci-policy",
                        "tags": ["ci", "policy"],
                        "text": "The CI policy allows only the mock provider and mock model.",
                        "metadata": {
                            "source": "ci",
                            "language": "en",
                        },
                    },
                },
            ]
        },
    )


def _assert_chat() -> None:
    print("checking orchestrator chat completion through mock provider", flush=True)
    response = _request(
        "POST",
        f"{ORCHESTRATOR_API_URL}/v1/chat/completions",
        {
            "model": "ci-mock:ci-mock-model",
            "pipeline_id": "ci",
            "messages": [{"role": "user", "content": "Say hello from CI"}],
            "max_tokens": 32,
            "stream": False,
        },
        headers={"authorization": f"Bearer {ORCHESTRATOR_API_KEY}"},
    )
    content = response["choices"][0]["message"]["content"]
    if "mock provider response" not in content:
        raise AssertionError(f"unexpected chat completion content: {content!r}")
    print("mock provider chat completion passed", flush=True)


def _assert_models() -> None:
    print("checking orchestrator model catalog", flush=True)
    response = _request(
        "GET",
        f"{ORCHESTRATOR_API_URL}/v1/models",
        headers={"authorization": f"Bearer {ORCHESTRATOR_API_KEY}"},
    )
    model_ids = {item.get("id") for item in response.get("data", []) if isinstance(item, dict)}
    if "ci-mock-model" not in model_ids:
        raise AssertionError(f"mock model was not advertised: {response!r}")
    print("model catalog passed", flush=True)


def _assert_retrieval() -> None:
    print("checking orchestrator rag query through retrieval-api and mock embeddings", flush=True)
    response = _request(
        "POST",
        f"{ORCHESTRATOR_API_URL}/v1/rag/query",
        {
            "query": "tokenstream retrieval smoke",
            "pipeline_id": "ci",
            "corpus_id": CORPUS_ID,
            "filters": {"source": "ci"},
            "top_k": 1,
        },
        headers={"authorization": f"Bearer {ORCHESTRATOR_API_KEY}"},
    )
    chunks = response.get("chunks") or []
    if not chunks:
        raise AssertionError(f"retrieval returned no chunks: {response!r}")
    first = chunks[0]
    if first.get("chunk_id") != "ci-docs-001":
        raise AssertionError(f"unexpected first retrieval chunk: {first!r}")
    print("mock retrieval query passed", flush=True)


def main() -> None:
    _wait_for("qdrant", f"{QDRANT_URL}/collections", timeout_s=120)
    _seed_qdrant()
    _wait_for("retrieval-api", f"{RETRIEVAL_API_URL}/health", timeout_s=180)
    _wait_for("ingestion-worker", f"{INGESTION_WORKER_URL}/health", timeout_s=180)
    _wait_for("orchestrator-api", f"{ORCHESTRATOR_API_URL}/health", timeout_s=180)
    _wait_for("dev-ui", f"{DEV_UI_URL}/", timeout_s=180)
    _assert_models()
    _assert_chat()
    _assert_retrieval()
    print("TokenStream CI smoke checks passed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=TokenStream smoke failure::{message}", flush=True)
        raise
