"""OWASP ZAP hook for authenticated TokenStream API scans."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


TARGET_BASE_URL = os.environ.get("ZAP_AUTH_TARGET_BASE_URL", "http://127.0.0.1:8004").rstrip("/")
AUTH_TOKEN = os.environ.get("ZAP_AUTH_TOKEN", "")
COVERAGE_OUTPUT = os.environ.get("ZAP_AUTH_COVERAGE_OUTPUT", "/zap/wrk/zap-authenticated-coverage.json")
PROTECTED_PREFIXES = tuple(
    prefix.strip()
    for prefix in os.environ.get(
        "ZAP_AUTH_PROTECTED_PREFIXES",
        "/v1/models,/v1/chat/completions,/v1/rag/query,/v1/rag/lookup",
    ).split(",")
    if prefix.strip()
)


def _status_code(response_header: str) -> int | None:
    match = re.match(r"^HTTP/\S+\s+(\d{3})\b", response_header or "")
    if not match:
        return None
    return int(match.group(1))


def _header_present(request_header: str) -> bool:
    return bool(re.search(r"^authorization:\s*Bearer\s+\S+", request_header or "", flags=re.IGNORECASE | re.MULTILINE))


def _request_line_parts(request_header: str) -> tuple[str, str]:
    request_line = (request_header or "").splitlines()[0] if request_header else ""
    parts = request_line.split(" ", 2)
    if len(parts) < 2:
        return "", ""
    return parts[0], parts[1]


def _safe_message(message: dict[str, Any]) -> dict[str, Any]:
    request_header = str(message.get("requestHeader") or "")
    response_header = str(message.get("responseHeader") or "")
    parsed_method, url = _request_line_parts(request_header)
    path = ""
    for prefix in PROTECTED_PREFIXES:
        if prefix in url:
            path = prefix
            break
    return {
        "id": str(message.get("id") or ""),
        "method": str(message.get("method") or parsed_method),
        "url": url,
        "matched_protected_prefix": path,
        "status": _status_code(response_header),
        "authorization_bearer_present": _header_present(request_header),
    }


def zap_tuned(zap: Any) -> None:
    if not AUTH_TOKEN:
        raise RuntimeError("ZAP_AUTH_TOKEN is required for authenticated ZAP scans")
    zap.replacer.add_rule(
        description="tokenstream-layer2a-authorization",
        enabled=True,
        matchtype="REQ_HEADER",
        matchregex=False,
        matchstring="Authorization",
        replacement=f"Bearer {AUTH_TOKEN}",
    )


def zap_pre_shutdown(zap: Any) -> None:
    messages = zap.core.messages(baseurl=TARGET_BASE_URL)
    if isinstance(messages, dict):
        messages = messages.get("messages", [])
    elif isinstance(messages, str):
        messages = json.loads(messages)
        if isinstance(messages, dict):
            messages = messages.get("messages", [])
    if not isinstance(messages, list):
        messages = []

    protected_messages = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        safe = _safe_message(message)
        if safe["matched_protected_prefix"]:
            protected_messages.append(safe)

    authenticated_2xx = [
        item
        for item in protected_messages
        if item["authorization_bearer_present"] and isinstance(item["status"], int) and 200 <= item["status"] < 300
    ]

    output = {
        "product": "TokenStream",
        "inventory_type": "zap-authenticated-coverage",
        "generated_at": datetime.now(UTC).isoformat(),
        "target_base_url": TARGET_BASE_URL,
        "summary": {
            "message_count": len(messages),
            "protected_message_count": len(protected_messages),
            "protected_authenticated_message_count": sum(
                1 for item in protected_messages if item["authorization_bearer_present"]
            ),
            "protected_authenticated_2xx_count": len(authenticated_2xx),
        },
        "protected_messages": protected_messages,
    }
    Path(COVERAGE_OUTPUT).write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
