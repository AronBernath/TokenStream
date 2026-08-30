from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "TokenStreamMockProvider/1.0"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path in {"/health", "/v1/health"}:
            _json(self, 200, {"ok": True})
            return
        if self.path == "/v1/models":
            _json(
                self,
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "ci-mock-model",
                            "object": "model",
                            "owned_by": "tokenstream-ci",
                        }
                    ],
                },
            )
            return
        _json(self, 404, {"error": "not_found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0") or "0")
        raw_body = self.rfile.read(length) if length else b"{}"
        try:
            request = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            _json(self, 400, {"error": "invalid_json"})
            return

        if self.path != "/v1/chat/completions":
            _json(self, 404, {"error": "not_found"})
            return

        model = str(request.get("model") or "ci-mock-model")
        _json(
            self,
            200,
            {
                "id": "chatcmpl-tokenstream-ci",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "mock provider response from tokenstream ci",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 6, "total_tokens": 7},
            },
        )


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
