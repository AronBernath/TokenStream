"""Live authorization matrix checks for the CI compose stack."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT_S = 10
ORCHESTRATOR_API_URL = os.environ.get("ORCHESTRATOR_API_URL", "http://127.0.0.1:8004").rstrip("/")
DEV_UI_URL = os.environ.get("DEV_UI_URL", "http://127.0.0.1:8010").rstrip("/")
RETRIEVAL_API_URL = os.environ.get("RETRIEVAL_API_URL", "http://127.0.0.1:8000").rstrip("/")
INGESTION_WORKER_URL = os.environ.get("INGESTION_WORKER_URL", "http://127.0.0.1:8002").rstrip("/")
INTERNAL_API_TOKEN = os.environ.get("CONFIG_AUTH_INTERNAL_TOKEN", "ci-internal-token")
ORCHESTRATOR_LEGACY_API_KEY = os.environ.get("ORCHESTRATOR_API_KEY", "ci-orchestrator-key")
ORCHESTRATOR_MODELS_TOKEN = os.environ.get("ORCHESTRATOR_MODELS_TOKEN", "")
ORCHESTRATOR_CHAT_TOKEN = os.environ.get("ORCHESTRATOR_CHAT_TOKEN", "")
ORCHESTRATOR_RAG_TOKEN = os.environ.get("ORCHESTRATOR_RAG_TOKEN", "")


@dataclass(frozen=True)
class Identity:
    name: str
    headers: dict[str, str]
    opener: urllib.request.OpenerDirector | None = None


def _json_body(payload: Any) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _request(
    method: str,
    url: str,
    *,
    body: Any | None = None,
    headers: dict[str, str] | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> dict[str, Any]:
    data = None if body is None else _json_body(body)
    request_headers = {"content-type": "application/json", **(headers or {})}
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    started = time.perf_counter()
    try:
        response_context = (
            opener.open(request, timeout=DEFAULT_TIMEOUT_S)
            if opener is not None
            else urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_S)
        )
        with response_context as response:
            response_body = response.read(2048)
            return {
                "status": response.status,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "content_type": response.headers.get("content-type", ""),
                "body_preview": response_body.decode("utf-8", errors="replace")[:500],
            }
    except urllib.error.HTTPError as exc:
        response_body = exc.read(2048)
        return {
            "status": exc.code,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "content_type": exc.headers.get("content-type", ""),
            "body_preview": response_body.decode("utf-8", errors="replace")[:500],
        }
    except Exception as exc:
        return {
            "status": None,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "error": str(exc),
        }


def _cookie_identity(name: str, username: str, password: str) -> tuple[Identity, dict[str, Any]]:
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    observed = _request(
        "POST",
        f"{DEV_UI_URL}/v1/auth/login",
        body={"username": username, "password": password},
        opener=opener,
    )
    return Identity(name=name, headers={}, opener=opener), {
        "name": f"login {name}",
        "identity": name,
        "observed": observed,
        "passed": observed.get("status") == 200,
    }


def _seed_management_users(admin: Identity) -> dict[str, Any]:
    users = [
        {
            "username": "admin",
            "password": "admin",
            "roles": ["admin"],
            "is_active": True,
            "must_rotate_password": True,
        },
        {
            "username": "viewer",
            "password": "viewer-password",
            "roles": ["viewer"],
            "is_active": True,
            "must_rotate_password": False,
        },
        {
            "username": "operator",
            "password": "operator-password",
            "roles": ["operator"],
            "is_active": True,
            "must_rotate_password": False,
        },
    ]
    observed = _request("PUT", f"{DEV_UI_URL}/v1/management/users", body=users, opener=admin.opener)
    return {
        "name": "seed management role users",
        "identity": admin.name,
        "observed": observed,
        "passed": observed.get("status") == 200,
    }


def _bad_login() -> dict[str, Any]:
    observed = _request(
        "POST",
        f"{DEV_UI_URL}/v1/auth/login",
        body={"username": "admin", "password": "definitely-not-the-password"},
    )
    return {
        "name": "bad login rejected",
        "identity": "bad-login",
        "observed": observed,
        "passed": observed.get("status") == 401,
    }


def _create_management_api_key(admin: Identity, name: str, scopes: list[str]) -> tuple[Identity, dict[str, Any]]:
    observed = _request(
        "POST",
        f"{DEV_UI_URL}/v1/management/api-keys",
        body={"subject": name, "scopes": scopes},
        opener=admin.opener,
    )
    token = ""
    if observed.get("status") == 200:
        try:
            token = json.loads(observed.get("body_preview") or "{}").get("plaintext_key", "")
        except json.JSONDecodeError:
            token = ""
    return Identity(name=name, headers={"authorization": f"Bearer {token}"}), {
        "name": f"create service API key {name}",
        "identity": admin.name,
        "observed": {**observed, "body_preview": "<redacted>"},
        "passed": observed.get("status") == 200 and bool(token),
    }


def _provider_payload(name: str = "ci-mock") -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "type": "openai_compat",
            "base_url": "http://mock-provider:8080/v1",
            "require_api_key": False,
            "default_model": "ci-mock-model",
            "models": ["ci-mock-model"],
            "capabilities": {"tools": False, "json_schema": True, "streaming": False},
        }
    ]


def _chat_payload() -> dict[str, Any]:
    return {
        "model": "ci-mock:ci-mock-model",
        "pipeline_id": "ci",
        "messages": [{"role": "user", "content": "authorization matrix check"}],
        "max_tokens": 16,
        "stream": False,
    }


def _rag_payload() -> dict[str, Any]:
    return {
        "query": "tokenstream retrieval smoke",
        "pipeline_id": "ci",
        "corpus_id": "ci_docs",
        "filters": {"source": "ci"},
        "top_k": 1,
    }


def _row(
    *,
    check_id: str,
    target: str,
    identity: Identity,
    method: str,
    url: str,
    expected_statuses: set[int],
    body: Any | None = None,
    known_gap: bool = False,
    notes: str = "",
) -> dict[str, Any]:
    observed = _request(method, url, body=body, headers=identity.headers, opener=identity.opener)
    passed = observed.get("status") in expected_statuses
    outcome = "accepted_known_gap" if passed and known_gap else "passed" if passed else "failed"
    return {
        "check_id": check_id,
        "target": target,
        "identity": identity.name,
        "method": method,
        "url": url,
        "expected_statuses": sorted(expected_statuses),
        "observed": observed,
        "known_gap": known_gap,
        "passed": passed,
        "outcome": outcome,
        "notes": notes,
    }


def build_report() -> dict[str, Any]:
    anonymous = Identity("anonymous", {})
    invalid = Identity("invalid-bearer", {"authorization": "Bearer invalid-token"})
    internal = Identity("internal-service", {"authorization": f"Bearer {INTERNAL_API_TOKEN}"})
    models_reader = Identity("orchestrator-models-reader", {"authorization": f"Bearer {ORCHESTRATOR_MODELS_TOKEN}"})
    chat_client = Identity("orchestrator-chat-client", {"authorization": f"Bearer {ORCHESTRATOR_CHAT_TOKEN}"})
    rag_client = Identity("orchestrator-rag-client", {"authorization": f"Bearer {ORCHESTRATOR_RAG_TOKEN}"})
    legacy_client = Identity("orchestrator-legacy-client", {"authorization": f"Bearer {ORCHESTRATOR_LEGACY_API_KEY}"})

    setup_results: list[dict[str, Any]] = []
    setup_results.extend(
        [
            {
                "name": "orchestrator scoped token configuration",
                "identity": "ci-secret-material",
                "observed": {
                    "models_token_configured": bool(ORCHESTRATOR_MODELS_TOKEN),
                    "chat_token_configured": bool(ORCHESTRATOR_CHAT_TOKEN),
                    "rag_token_configured": bool(ORCHESTRATOR_RAG_TOKEN),
                },
                "passed": all([ORCHESTRATOR_MODELS_TOKEN, ORCHESTRATOR_CHAT_TOKEN, ORCHESTRATOR_RAG_TOKEN]),
            }
        ]
    )
    admin, admin_login = _cookie_identity("admin-session", "admin", "admin")
    setup_results.extend([_bad_login(), admin_login])
    if admin_login["passed"]:
        setup_results.append(_seed_management_users(admin))
        status_reader, status_key_setup = _create_management_api_key(admin, "status-reader", ["status:read"])
        corpora_reader, corpora_key_setup = _create_management_api_key(admin, "corpora-reader", ["corpora:read"])
        setup_results.extend([status_key_setup, corpora_key_setup])
    else:
        status_reader = Identity("status-reader", {"authorization": "Bearer <unavailable>"})
        corpora_reader = Identity("corpora-reader", {"authorization": "Bearer <unavailable>"})

    viewer, viewer_login = _cookie_identity("viewer-session", "viewer", "viewer-password")
    operator, operator_login = _cookie_identity("operator-session", "operator", "operator-password")
    setup_results.extend([viewer_login, operator_login])

    checks = [
        _row(
            check_id="ORCH-ANON-MODELS",
            target="orchestrator-api",
            identity=anonymous,
            method="GET",
            url=f"{ORCHESTRATOR_API_URL}/v1/models",
            expected_statuses={401},
        ),
        _row(
            check_id="ORCH-INVALID-MODELS",
            target="orchestrator-api",
            identity=invalid,
            method="GET",
            url=f"{ORCHESTRATOR_API_URL}/v1/models",
            expected_statuses={401},
        ),
        _row(
            check_id="ORCH-MODELS-READ",
            target="orchestrator-api",
            identity=models_reader,
            method="GET",
            url=f"{ORCHESTRATOR_API_URL}/v1/models",
            expected_statuses={200},
        ),
        _row(
            check_id="ORCH-MODELS-NO-CHAT",
            target="orchestrator-api",
            identity=models_reader,
            method="POST",
            url=f"{ORCHESTRATOR_API_URL}/v1/chat/completions",
            body=_chat_payload(),
            expected_statuses={403},
        ),
        _row(
            check_id="ORCH-MODELS-NO-RAG",
            target="orchestrator-api",
            identity=models_reader,
            method="POST",
            url=f"{ORCHESTRATOR_API_URL}/v1/rag/query",
            body=_rag_payload(),
            expected_statuses={403},
        ),
        _row(
            check_id="ORCH-CHAT-INVOKE",
            target="orchestrator-api",
            identity=chat_client,
            method="POST",
            url=f"{ORCHESTRATOR_API_URL}/v1/chat/completions",
            body=_chat_payload(),
            expected_statuses={200},
        ),
        _row(
            check_id="ORCH-CHAT-NO-MODELS",
            target="orchestrator-api",
            identity=chat_client,
            method="GET",
            url=f"{ORCHESTRATOR_API_URL}/v1/models",
            expected_statuses={403},
        ),
        _row(
            check_id="ORCH-RAG-QUERY",
            target="orchestrator-api",
            identity=rag_client,
            method="POST",
            url=f"{ORCHESTRATOR_API_URL}/v1/rag/query",
            body=_rag_payload(),
            expected_statuses={200},
        ),
        _row(
            check_id="ORCH-RAG-NO-CHAT",
            target="orchestrator-api",
            identity=rag_client,
            method="POST",
            url=f"{ORCHESTRATOR_API_URL}/v1/chat/completions",
            body=_chat_payload(),
            expected_statuses={403},
        ),
        _row(
            check_id="ORCH-LEGACY-COMPAT",
            target="orchestrator-api",
            identity=legacy_client,
            method="GET",
            url=f"{ORCHESTRATOR_API_URL}/v1/models",
            expected_statuses={200},
            notes="Legacy shared-key support remains enabled in the CI stack for smoke compatibility.",
        ),
        _row(
            check_id="MGMT-ANON-STATUS",
            target="config-auth",
            identity=anonymous,
            method="GET",
            url=f"{DEV_UI_URL}/v1/management/status",
            expected_statuses={401},
        ),
        _row(
            check_id="MGMT-ANON-ME",
            target="config-auth",
            identity=anonymous,
            method="GET",
            url=f"{DEV_UI_URL}/v1/auth/me",
            expected_statuses={401},
        ),
        _row(
            check_id="MGMT-INVALID-PROVIDERS",
            target="config-auth",
            identity=invalid,
            method="GET",
            url=f"{DEV_UI_URL}/v1/management/providers",
            expected_statuses={401},
        ),
        _row(
            check_id="MGMT-ADMIN-USERS",
            target="config-auth",
            identity=admin,
            method="GET",
            url=f"{DEV_UI_URL}/v1/management/users",
            expected_statuses={200},
        ),
        _row(
            check_id="MGMT-VIEWER-READ",
            target="config-auth",
            identity=viewer,
            method="GET",
            url=f"{DEV_UI_URL}/v1/management/providers",
            expected_statuses={200},
        ),
        _row(
            check_id="MGMT-VIEWER-NO-PROVIDER-WRITE",
            target="config-auth",
            identity=viewer,
            method="PUT",
            url=f"{DEV_UI_URL}/v1/management/providers",
            body=_provider_payload("viewer-forbidden"),
            expected_statuses={403},
        ),
        _row(
            check_id="MGMT-VIEWER-NO-KEY-CREATE",
            target="config-auth",
            identity=viewer,
            method="POST",
            url=f"{DEV_UI_URL}/v1/management/api-keys",
            body={"subject": "viewer-forbidden", "scopes": ["status:read"]},
            expected_statuses={403},
        ),
        _row(
            check_id="MGMT-OPERATOR-PROVIDER-WRITE",
            target="config-auth",
            identity=operator,
            method="PUT",
            url=f"{DEV_UI_URL}/v1/management/providers",
            body=_provider_payload("operator-allowed"),
            expected_statuses={200},
        ),
        _row(
            check_id="MGMT-OPERATOR-NO-USER-WRITE",
            target="config-auth",
            identity=operator,
            method="PUT",
            url=f"{DEV_UI_URL}/v1/management/users",
            body=[],
            expected_statuses={403},
        ),
        _row(
            check_id="MGMT-SERVICE-STATUS-READ",
            target="config-auth",
            identity=status_reader,
            method="GET",
            url=f"{DEV_UI_URL}/v1/management/status",
            expected_statuses={200},
        ),
        _row(
            check_id="MGMT-SERVICE-STATUS-NO-PROVIDERS",
            target="config-auth",
            identity=status_reader,
            method="GET",
            url=f"{DEV_UI_URL}/v1/management/providers",
            expected_statuses={403},
        ),
        _row(
            check_id="MGMT-SERVICE-CORPORA-READ",
            target="config-auth",
            identity=corpora_reader,
            method="GET",
            url=f"{DEV_UI_URL}/v1/management/corpora",
            expected_statuses={200},
        ),
        _row(
            check_id="MGMT-SERVICE-CORPORA-NO-WRITE",
            target="config-auth",
            identity=corpora_reader,
            method="POST",
            url=f"{DEV_UI_URL}/v1/management/corpora",
            body={"corpus_id": "forbidden", "title": "Forbidden"},
            expected_statuses={403},
        ),
        _row(
            check_id="INTERNAL-ANON-CORPORA",
            target="config-auth",
            identity=anonymous,
            method="GET",
            url=f"{DEV_UI_URL}/internal/corpora",
            expected_statuses={401},
        ),
        _row(
            check_id="INTERNAL-BAD-CORPORA",
            target="config-auth",
            identity=invalid,
            method="GET",
            url=f"{DEV_UI_URL}/internal/corpora",
            expected_statuses={403},
        ),
        _row(
            check_id="INTERNAL-SERVICE-CORPORA",
            target="config-auth",
            identity=internal,
            method="GET",
            url=f"{DEV_UI_URL}/internal/corpora",
            expected_statuses={200},
        ),
        _row(
            check_id="INGEST-PURGE-ANON",
            target="ingestion-worker",
            identity=anonymous,
            method="POST",
            url=f"{INGESTION_WORKER_URL}/v1/purge/source",
            body={"corpus_id": "ci_docs", "source_id": "src"},
            expected_statuses={401},
        ),
        _row(
            check_id="INGEST-PURGE-BAD-TOKEN",
            target="ingestion-worker",
            identity=invalid,
            method="POST",
            url=f"{INGESTION_WORKER_URL}/v1/purge/source",
            body={"corpus_id": "ci_docs", "source_id": "src"},
            expected_statuses={403},
        ),
        _row(
            check_id="RETRIEVAL-DIRECT-ANON-GAP",
            target="retrieval-api",
            identity=anonymous,
            method="POST",
            url=f"{RETRIEVAL_API_URL}/v1/query",
            body={"query": "tokenstream retrieval smoke", "corpus_id": "ci_docs", "top_k": 1},
            expected_statuses={200},
            known_gap=True,
            notes="Current known gap: direct retrieval API does not enforce caller authentication.",
        ),
        _row(
            check_id="INGEST-DRY-RUN-ANON-GAP",
            target="ingestion-worker",
            identity=anonymous,
            method="POST",
            url=f"{INGESTION_WORKER_URL}/v1/dry-run/chunking",
            body={"corpus_id": "ci_docs", "max_preview_chunks": 1},
            expected_statuses={200, 400, 404, 422, 500},
            known_gap=True,
            notes="Current known gap: dry-run chunking endpoint reaches application logic without caller authentication.",
        ),
    ]

    setup_failures = [item for item in setup_results if not item["passed"]]
    mismatches = [item for item in checks if not item["passed"]]
    return {
        "product": "TokenStream",
        "inventory_type": "live-authorization-matrix",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "ci-compose-mock-stack",
        "targets": {
            "dev_ui": DEV_UI_URL,
            "orchestrator_api": ORCHESTRATOR_API_URL,
            "retrieval_api": RETRIEVAL_API_URL,
            "ingestion_worker": INGESTION_WORKER_URL,
        },
        "summary": {
            "setup_count": len(setup_results),
            "setup_failures": len(setup_failures),
            "check_count": len(checks),
            "passed_count": sum(1 for item in checks if item["passed"]),
            "mismatch_count": len(mismatches),
            "known_gap_count": sum(1 for item in checks if item["known_gap"]),
            "known_gap_5xx_count": sum(
                1
                for item in checks
                if item["known_gap"]
                and isinstance(item["observed"].get("status"), int)
                and item["observed"]["status"] >= 500
            ),
        },
        "setup": setup_results,
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live CI authorization matrix checks.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fail-on-mismatch", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    if args.fail_on_mismatch and report["summary"]["setup_failures"]:
        raise SystemExit("One or more authorization matrix setup steps failed")
    if args.fail_on_mismatch and report["summary"]["mismatch_count"]:
        raise SystemExit("One or more authorization matrix checks returned an unexpected status")


if __name__ == "__main__":
    main()
