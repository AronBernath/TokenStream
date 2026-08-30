from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


INTERNAL_TOKEN = os.environ.get("CONFIG_AUTH_INTERNAL_TOKEN", "ci-internal-token")

CORPUS = {
    "corpus_id": "ci_docs",
    "title": "TokenStream CI Corpus",
    "description": "Mock corpus used by GitHub Actions smoke tests.",
    "environment": "ci",
    "tenant_id": "ci-tenant",
    "chunking": {"strategy": "mock"},
    "index": {},
    "retrieval_profile_id": "ci-hybrid",
    "retrieval_config": {
        "strict_filters": True,
        "filterable_fields": ["source", "language"],
        "citation_fields": ["doc_id", "source"],
    },
    "metadata": {"source": "ci"},
}

RETRIEVAL_PROFILES = [
    {
        "retrieval_profile_id": "ci-hybrid",
        "name": "CI hybrid retrieval",
        "type": "hybrid",
        "enabled": True,
        "config": {
            "strict_filters": True,
            "filterable_fields": ["source", "language"],
            "citation_fields": ["doc_id", "source"],
        },
    }
]


def _json(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _authorized(headers: Any) -> bool:
    return headers.get("authorization", "") == f"Bearer {INTERNAL_TOKEN}"


class Handler(BaseHTTPRequestHandler):
    server_version = "TokenStreamMockRegistry/1.0"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path in {"/health", "/v1/health"}:
            _json(self, 200, {"ok": True})
            return

        if self.path.startswith("/internal/") and not _authorized(self.headers):
            _json(self, 403, {"error": "forbidden"})
            return

        if self.path == "/internal/corpora":
            _json(self, 200, {"corpora": ["ci_docs"]})
            return
        if self.path == "/internal/corpora/ci_docs":
            _json(self, 200, CORPUS)
            return
        if self.path == "/internal/retrieval-profiles":
            _json(self, 200, RETRIEVAL_PROFILES)
            return
        if self.path.startswith("/internal/ingestion-jobs"):
            _json(self, 200, [])
            return

        _json(self, 404, {"error": "not_found"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8082), Handler).serve_forever()
