"""Create an ephemeral config-auth API key for authenticated DAST scans."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT_S = 12
DEV_UI_URL = os.environ.get("DEV_UI_URL", "http://127.0.0.1:8010").rstrip("/")


def _json_body(payload: Any) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _request(
    method: str,
    url: str,
    *,
    body: Any | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=None if body is None else _json_body(body),
        headers={"content-type": "application/json"},
        method=method,
    )
    started = time.perf_counter()
    try:
        response_context = (
            opener.open(request, timeout=DEFAULT_TIMEOUT_S)
            if opener is not None
            else urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_S)
        )
        with response_context as response:
            text = response.read(65536).decode("utf-8", errors="replace")
            return {
                "status": response.status,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "body": text,
            }
    except urllib.error.HTTPError as exc:
        text = exc.read(65536).decode("utf-8", errors="replace")
        return {
            "status": exc.code,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "body": text,
        }


def _parse_json(text: str) -> dict[str, Any]:
    parsed = json.loads(text or "{}")
    return parsed if isinstance(parsed, dict) else {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a config-auth DAST bearer key in a disposable stack.")
    parser.add_argument("--subject", default="zap-layer2b-management-reader")
    parser.add_argument("--scope", action="append", default=[])
    parser.add_argument("--github-env", type=Path)
    parser.add_argument("--env-name", default="CONFIG_AUTH_DAST_TOKEN")
    parser.add_argument("--report-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    scopes = args.scope or [
        "status:read",
        "providers:read",
        "policies:read",
        "processors:read",
        "retrieval:read",
        "keys:read",
        "users:read",
        "rag:read",
        "corpora:read",
        "mcp:read",
    ]

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    login = _request(
        "POST",
        f"{DEV_UI_URL}/v1/auth/login",
        body={"username": "admin", "password": "admin"},
        opener=opener,
    )
    if login.get("status") != 200:
        raise SystemExit(f"Unable to log in as bootstrap admin: HTTP {login.get('status')}")

    created = _request(
        "POST",
        f"{DEV_UI_URL}/v1/management/api-keys",
        body={"subject": args.subject, "scopes": scopes},
        opener=opener,
    )
    if created.get("status") != 200:
        raise SystemExit(f"Unable to create config-auth DAST key: HTTP {created.get('status')}")

    payload = _parse_json(str(created.get("body") or ""))
    token = str(payload.get("plaintext_key") or "")
    entry = payload.get("entry") if isinstance(payload.get("entry"), dict) else {}
    if not token:
        raise SystemExit("Config-auth DAST key response did not include plaintext_key")

    print(f"::add-mask::{token}")
    if args.github_env:
        with args.github_env.open("a", encoding="utf-8") as fp:
            fp.write(f"{args.env_name}={token}\n")

    if args.report_output:
        report = {
            "product": "TokenStream",
            "inventory_type": "config-auth-dast-key-setup",
            "generated_at": datetime.now(UTC).isoformat(),
            "target": DEV_UI_URL,
            "subject": args.subject,
            "key_id": entry.get("key_id", ""),
            "scopes": scopes,
            "plaintext_key_exported_to_env": bool(args.github_env),
        }
        args.report_output.parent.mkdir(parents=True, exist_ok=True)
        args.report_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
