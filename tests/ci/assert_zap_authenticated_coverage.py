"""Assert authenticated OWASP ZAP scans reached protected TokenStream endpoints."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_REQUIRED_ENDPOINTS = ("/v1/models", "/v1/chat/completions", "/v1/rag/query")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _authenticated_2xx_messages(report: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for message in report.get("protected_messages") or []:
        if not isinstance(message, dict):
            continue
        status = message.get("status")
        if message.get("authorization_bearer_present") and isinstance(status, int) and 200 <= status < 300:
            out.append(message)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate authenticated ZAP request coverage.")
    parser.add_argument("--coverage-report", required=True, type=Path)
    parser.add_argument("--require-any-2xx", action="store_true")
    parser.add_argument("--require-all-protected-authenticated", action="store_true")
    parser.add_argument("--required-endpoint", action="append", default=[])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = _load(args.coverage_report)
    messages = _authenticated_2xx_messages(report)
    protected_messages = [message for message in report.get("protected_messages") or [] if isinstance(message, dict)]
    missing_auth = [message for message in protected_messages if not message.get("authorization_bearer_present")]
    required = tuple(args.required_endpoint or DEFAULT_REQUIRED_ENDPOINTS)
    matched = {
        endpoint
        for endpoint in required
        for message in messages
        if str(message.get("matched_protected_prefix") or "") == endpoint
    }

    summary = {
        "required_endpoints": list(required),
        "matched_authenticated_2xx_endpoints": sorted(matched),
        "protected_messages_missing_auth_count": len(missing_auth),
        "authenticated_2xx_count": len(messages),
        "coverage_summary": report.get("summary", {}),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))

    if args.require_all_protected_authenticated and missing_auth:
        sample = ", ".join(str(message.get("url") or "") for message in missing_auth[:5])
        raise SystemExit(f"ZAP protected endpoint request(s) were missing bearer auth: {sample}")

    if args.require_any_2xx and not matched:
        raise SystemExit(
            "ZAP authenticated scan did not record any authenticated 2xx response "
            f"for required endpoint prefixes: {', '.join(required)}"
        )


if __name__ == "__main__":
    main()
