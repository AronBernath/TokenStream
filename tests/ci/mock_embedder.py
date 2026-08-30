from __future__ import annotations

import hashlib
import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


VECTOR_SIZE = 4


def _json(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _embed(text: str) -> list[float]:
    normalized = text.lower()
    if "tokenstream" in normalized or "retrieval" in normalized:
        return [1.0, 0.0, 0.0, 0.0]

    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    vector = [float(digest[i] + 1) for i in range(VECTOR_SIZE)]
    magnitude = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / magnitude for value in vector]


class Handler(BaseHTTPRequestHandler):
    server_version = "TokenStreamMockEmbedder/1.0"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path in {"/health", "/v1/health"}:
            _json(self, 200, {"ok": True, "vector_size": VECTOR_SIZE})
            return
        _json(self, 404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/embed":
            _json(self, 404, {"error": "not_found"})
            return

        length = int(self.headers.get("content-length", "0") or "0")
        raw_body = self.rfile.read(length) if length else b"{}"
        try:
            request = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            _json(self, 400, {"error": "invalid_json"})
            return

        inputs = request.get("inputs")
        if isinstance(inputs, str):
            inputs = [inputs]
        if not isinstance(inputs, list):
            _json(self, 400, {"error": "inputs_must_be_list"})
            return

        _json(self, 200, [_embed(str(item)) for item in inputs])


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8081), Handler).serve_forever()
