"""Unauthenticated DAST boundary probes for the CI stack."""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT_S = 8
PUBLIC_OK = {200}
AUTH_REJECTED = {401, 403}


def _env_url(name: str, default: str) -> str:
    return os.environ.get(name, default).rstrip("/")


DEV_UI_URL = _env_url("DEV_UI_URL", "http://127.0.0.1:8010")
ORCHESTRATOR_API_URL = _env_url("ORCHESTRATOR_API_URL", "http://127.0.0.1:8004")
RETRIEVAL_API_URL = _env_url("RETRIEVAL_API_URL", "http://127.0.0.1:8000")
INGESTION_WORKER_URL = _env_url("INGESTION_WORKER_URL", "http://127.0.0.1:8002")


def _request(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            response_body = response.read(2048)
            return {
                "status": response.status,
                "reachable": True,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "content_type": response.headers.get("content-type", ""),
                "body_preview": response_body.decode("utf-8", errors="replace")[:500],
            }
    except urllib.error.HTTPError as exc:
        response_body = exc.read(2048)
        return {
            "status": exc.code,
            "reachable": True,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "content_type": exc.headers.get("content-type", ""),
            "body_preview": response_body.decode("utf-8", errors="replace")[:500],
        }
    except (TimeoutError, urllib.error.URLError, OSError, socket.timeout) as exc:
        reason = getattr(exc, "reason", exc)
        return {
            "status": None,
            "reachable": False,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "error": str(reason),
        }


def _json_body(payload: Any) -> bytes:
    return json.dumps(payload).encode("utf-8")


BODY_VARIANTS: list[tuple[str, bytes, str]] = [
    ("normal", _json_body({"query": "tokenstream retrieval smoke", "corpus_id": "ci_docs"}), "application/json"),
    ("sqli", _json_body({"query": "' OR '1'='1", "corpus_id": "ci_docs"}), "application/json"),
    ("path_traversal", _json_body({"query": "../../../../etc/passwd", "corpus_id": "ci_docs"}), "application/json"),
    ("malformed_json", b'{"query": ', "application/json"),
    ("large_input", _json_body({"query": "A" * 25000, "corpus_id": "ci_docs"}), "application/json"),
]


def _public_checks() -> list[dict[str, Any]]:
    return [
        {"name": "dev-ui root", "method": "GET", "url": f"{DEV_UI_URL}/", "expected_statuses": PUBLIC_OK},
        {"name": "dev-ui health", "method": "GET", "url": f"{DEV_UI_URL}/health", "expected_statuses": PUBLIC_OK},
        {
            "name": "dev-ui openapi",
            "method": "GET",
            "url": f"{DEV_UI_URL}/openapi.json",
            "expected_statuses": PUBLIC_OK,
        },
        {
            "name": "orchestrator health",
            "method": "GET",
            "url": f"{ORCHESTRATOR_API_URL}/health",
            "expected_statuses": PUBLIC_OK,
        },
        {
            "name": "orchestrator v1 openapi",
            "method": "GET",
            "url": f"{ORCHESTRATOR_API_URL}/v1/openapi.json",
            "expected_statuses": PUBLIC_OK,
        },
        {
            "name": "retrieval health",
            "method": "GET",
            "url": f"{RETRIEVAL_API_URL}/health",
            "expected_statuses": PUBLIC_OK,
        },
        {
            "name": "retrieval openapi",
            "method": "GET",
            "url": f"{RETRIEVAL_API_URL}/openapi.json",
            "expected_statuses": PUBLIC_OK,
        },
        {
            "name": "ingestion worker health",
            "method": "GET",
            "url": f"{INGESTION_WORKER_URL}/health",
            "expected_statuses": PUBLIC_OK,
        },
        {
            "name": "ingestion worker openapi",
            "method": "GET",
            "url": f"{INGESTION_WORKER_URL}/openapi.json",
            "expected_statuses": PUBLIC_OK,
        },
    ]


def _protected_get_checks() -> list[dict[str, Any]]:
    return [
        {"name": "orchestrator models", "method": "GET", "url": f"{ORCHESTRATOR_API_URL}/v1/models"},
        {"name": "dev-ui auth me", "method": "GET", "url": f"{DEV_UI_URL}/v1/auth/me"},
        {"name": "management status", "method": "GET", "url": f"{DEV_UI_URL}/v1/management/status"},
        {"name": "management providers", "method": "GET", "url": f"{DEV_UI_URL}/v1/management/providers"},
        {"name": "management api keys", "method": "GET", "url": f"{DEV_UI_URL}/v1/management/api-keys"},
        {"name": "management corpora", "method": "GET", "url": f"{DEV_UI_URL}/v1/management/corpora"},
    ]


def _protected_post_checks() -> list[dict[str, Any]]:
    return [
        {"name": "orchestrator rag query", "method": "POST", "url": f"{ORCHESTRATOR_API_URL}/v1/rag/query"},
        {"name": "orchestrator rag lookup", "method": "POST", "url": f"{ORCHESTRATOR_API_URL}/v1/rag/lookup"},
        {
            "name": "orchestrator chat completions",
            "method": "POST",
            "url": f"{ORCHESTRATOR_API_URL}/v1/chat/completions",
        },
        {"name": "management create api key", "method": "POST", "url": f"{DEV_UI_URL}/v1/management/api-keys"},
        {"name": "management create corpus", "method": "POST", "url": f"{DEV_UI_URL}/v1/management/corpora"},
    ]


def _public_behavior_checks() -> list[dict[str, Any]]:
    return [
        {
            "name": "retrieval direct query",
            "method": "POST",
            "url": f"{RETRIEVAL_API_URL}/v1/query",
            "body": _json_body({"query": "tokenstream retrieval smoke", "corpus_id": "ci_docs", "top_k": 1}),
            "content_type": "application/json",
        },
        {
            "name": "ingestion worker dry run",
            "method": "POST",
            "url": f"{INGESTION_WORKER_URL}/v1/dry-run/chunking",
            "body": _json_body({"text": "TokenStream DAST smoke text.", "corpus": {"corpus_id": "ci_docs"}}),
            "content_type": "application/json",
        },
    ]


def _mock_exposure_checks() -> list[dict[str, Any]]:
    return [
        {"name": "mock provider direct exposure", "method": "GET", "url": "http://127.0.0.1:8080/health"},
        {"name": "mock embedder direct exposure", "method": "GET", "url": "http://127.0.0.1:8081/health"},
        {"name": "mock registry direct exposure", "method": "GET", "url": "http://127.0.0.1:8082/health"},
    ]


def _probe_public() -> list[dict[str, Any]]:
    results = []
    for check in _public_checks():
        observed = _request(check["method"], check["url"])
        passed = observed.get("status") in check["expected_statuses"]
        results.append(
            {
                **check,
                "expected_statuses": sorted(check["expected_statuses"]),
                "observed": observed,
                "passed": passed,
            }
        )
    return results


def _probe_protected() -> list[dict[str, Any]]:
    results = []
    header_variants = [
        ("missing_authorization", {}),
        ("malformed_authorization", {"authorization": "NotBearer token"}),
        ("invalid_bearer", {"authorization": "Bearer invalid-token"}),
    ]
    for check in _protected_get_checks():
        for variant_name, headers in header_variants:
            observed = _request(check["method"], check["url"], headers=headers)
            status = observed.get("status")
            results.append(
                {
                    **check,
                    "variant": variant_name,
                    "observed": observed,
                    "passed": status in AUTH_REJECTED,
                    "auth_bypass": isinstance(status, int) and 200 <= status < 400,
                }
            )

    for check in _protected_post_checks():
        for variant_name, body, content_type in BODY_VARIANTS:
            observed = _request(check["method"], check["url"], body=body, headers={"content-type": content_type})
            status = observed.get("status")
            results.append(
                {
                    **check,
                    "variant": variant_name,
                    "observed": observed,
                    "passed": status in AUTH_REJECTED,
                    "auth_bypass": isinstance(status, int) and 200 <= status < 400,
                }
            )
    return results


def _probe_public_behavior() -> list[dict[str, Any]]:
    results = []
    for check in _public_behavior_checks():
        headers = {"content-type": check["content_type"]}
        observed = _request(check["method"], check["url"], body=check["body"], headers=headers)
        results.append({**check, "body": "<redacted>", "observed": observed})
    return results


def _probe_mock_exposure() -> list[dict[str, Any]]:
    results = []
    for check in _mock_exposure_checks():
        observed = _request(check["method"], check["url"], timeout_s=3)
        exposed = bool(observed.get("reachable"))
        results.append({**check, "observed": observed, "passed": not exposed, "exposed": exposed})
    return results


def build_report() -> dict[str, Any]:
    public_results = _probe_public()
    protected_results = _probe_protected()
    public_behavior_results = _probe_public_behavior()
    mock_exposure_results = _probe_mock_exposure()
    protected_mismatches = [item for item in protected_results if not item["passed"]]
    auth_bypasses = [item for item in protected_results if item["auth_bypass"]]
    exposed_mocks = [item for item in mock_exposure_results if item["exposed"]]

    return {
        "product": "TokenStream",
        "inventory_type": "dast-unauthenticated-boundary-probe",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "unauthenticated",
        "targets": {
            "dev_ui": DEV_UI_URL,
            "orchestrator_api": ORCHESTRATOR_API_URL,
            "retrieval_api": RETRIEVAL_API_URL,
            "ingestion_worker": INGESTION_WORKER_URL,
        },
        "summary": {
            "public_check_count": len(public_results),
            "public_check_failures": sum(1 for item in public_results if not item["passed"]),
            "protected_check_count": len(protected_results),
            "protected_rejection_mismatches": len(protected_mismatches),
            "auth_bypass_count": len(auth_bypasses),
            "public_behavior_check_count": len(public_behavior_results),
            "mock_exposure_check_count": len(mock_exposure_results),
            "mock_exposure_count": len(exposed_mocks),
        },
        "public_checks": public_results,
        "protected_checks": protected_results,
        "public_behavior_checks": public_behavior_results,
        "mock_exposure_checks": mock_exposure_results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe unauthenticated TokenStream CI attack surface.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fail-on-public-unavailable", action="store_true")
    parser.add_argument("--fail-on-auth-bypass", action="store_true")
    parser.add_argument("--fail-on-mock-exposure", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary = report["summary"]
    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.fail_on_public_unavailable and summary["public_check_failures"]:
        raise SystemExit("One or more public DAST readiness targets were unavailable")
    if args.fail_on_auth_bypass and summary["auth_bypass_count"]:
        raise SystemExit("One or more protected endpoints accepted unauthenticated access")
    if args.fail_on_mock_exposure and summary["mock_exposure_count"]:
        raise SystemExit("One or more mock services were exposed on the runner host")


if __name__ == "__main__":
    main()
