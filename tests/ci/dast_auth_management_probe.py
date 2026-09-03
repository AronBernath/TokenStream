"""Authenticated Layer 2B DAST probes for TokenStream management surfaces."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT_S = 12
DEV_UI_URL = os.environ.get("DEV_UI_URL", "http://127.0.0.1:8010").rstrip("/")

SECRET_VALUES: set[str] = set()
LEAK_PATTERNS = (
    "Traceback (most recent call last)",
    'File "',
    "/home/runner/",
    "/workspace/",
    "/app/",
    "site-packages",
    "uvicorn.error",
    "fastapi.exceptions",
    "sqlite3.",
    "sqlalchemy.",
)


@dataclass
class Identity:
    name: str
    headers: dict[str, str]
    opener: urllib.request.OpenerDirector | None = None


@dataclass(frozen=True)
class ProbeCase:
    check_id: str
    category: str
    target: str
    identity: str
    description: str
    method: str
    url: str
    expected_statuses: set[int]
    body: Any | None = None
    raw_body: bytes | None = None
    content_type: str = "application/json"
    auth_boundary_bypass_on_2xx: bool = False
    unsafe_mutation_on_2xx: bool = False
    allow_5xx: bool = False
    known_gap: bool = False
    validator: Callable[[dict[str, Any]], tuple[bool, str]] | None = None


def _json_body(payload: Any) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _redact(text: str) -> str:
    redacted = text
    for value in sorted(SECRET_VALUES, key=len, reverse=True):
        if value:
            redacted = redacted.replace(value, "<redacted-secret>")
    redacted = re.sub(r"sk_[A-Za-z0-9_\-]{16,}", "<redacted-api-key>", redacted)
    redacted = re.sub(r"config_auth_session=[^;,\\s]+", "config_auth_session=<redacted-session>", redacted)
    return redacted


def _selected_headers(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    selected = {}
    for name in ("content-type", "cache-control", "pragma", "set-cookie"):
        value = headers.get(name)
        if value:
            selected[name] = _redact(str(value))
    return selected


def _request(
    method: str,
    url: str,
    *,
    body: Any | None = None,
    raw_body: bytes | None = None,
    headers: dict[str, str] | None = None,
    opener: urllib.request.OpenerDirector | None = None,
    content_type: str = "application/json",
    include_raw_body: bool = False,
) -> dict[str, Any]:
    data = raw_body if raw_body is not None else None if body is None else _json_body(body)
    request_headers = {"content-type": content_type, **(headers or {})}
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    started = time.perf_counter()
    try:
        response_context = (
            opener.open(request, timeout=DEFAULT_TIMEOUT_S)
            if opener is not None
            else urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_S)
        )
        with response_context as response:
            response_body = response.read(65536).decode("utf-8", errors="replace")
            return {
                "status": response.status,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "headers": _selected_headers(response.headers),
                "body_preview": _redact(response_body[:20000]),
                **({"_raw_body": response_body} if include_raw_body else {}),
            }
    except urllib.error.HTTPError as exc:
        response_body = exc.read(65536).decode("utf-8", errors="replace")
        return {
            "status": exc.code,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "headers": _selected_headers(exc.headers),
            "body_preview": _redact(response_body[:20000]),
            **({"_raw_body": response_body} if include_raw_body else {}),
        }
    except Exception as exc:
        return {
            "status": None,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "error": _redact(str(exc)),
        }


def _parse_json(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _login_identity(name: str, username: str, password: str) -> tuple[Identity, dict[str, Any]]:
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    observed = _request(
        "POST",
        f"{DEV_UI_URL}/v1/auth/login",
        body={"username": username, "password": password},
        opener=opener,
    )
    for cookie in cookie_jar:
        SECRET_VALUES.add(cookie.value)
    return Identity(name=name, headers={}, opener=opener), {
        "name": f"login {name}",
        "identity": name,
        "observed": observed,
        "passed": observed.get("status") == 200,
    }


def _bad_login() -> dict[str, Any]:
    observed = _request(
        "POST",
        f"{DEV_UI_URL}/v1/auth/login",
        body={"username": "admin", "password": "not-the-password"},
    )
    return {
        "name": "bad login rejected",
        "identity": "bad-login",
        "observed": observed,
        "passed": observed.get("status") == 401,
    }


def _seed_users(admin: Identity) -> dict[str, Any]:
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
        "name": "seed management users",
        "identity": admin.name,
        "observed": observed,
        "passed": observed.get("status") == 200,
    }


def _create_service_key(admin: Identity, name: str, scopes: list[str]) -> tuple[Identity, dict[str, Any]]:
    observed = _request(
        "POST",
        f"{DEV_UI_URL}/v1/management/api-keys",
        body={"subject": name, "scopes": scopes},
        opener=admin.opener,
        include_raw_body=True,
    )
    token = ""
    key_id = ""
    if observed.get("status") == 200:
        raw_body = str(observed.pop("_raw_body", "") or "")
        parsed = _parse_json(raw_body)
        token = str(parsed.get("plaintext_key") or "")
        entry = parsed.get("entry") if isinstance(parsed.get("entry"), dict) else {}
        key_id = str(entry.get("key_id") or "")
        if token:
            SECRET_VALUES.add(token)
            observed["body_preview"] = "<redacted>"
    return Identity(name=name, headers={"authorization": f"Bearer {token}"}), {
        "name": f"create service key {name}",
        "identity": admin.name,
        "observed": {**observed, "key_id": key_id},
        "passed": observed.get("status") == 200 and bool(token),
    }


def _provider_payload(name: str, *, base_url: str = "http://mock-provider:8080/v1") -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "type": "openai_compat",
            "base_url": base_url,
            "require_api_key": False,
            "default_model": "ci-mock-model",
            "models": ["ci-mock-model"],
            "capabilities": {
                "tools": False,
                "json_schema": True,
                "streaming": False,
                "chunking": False,
                "max_context_window": 8192,
                "default_context_window": 4096,
            },
            "client_controls": {"temperature": True, "max_tokens": True},
        }
    ]


def _policy_payload() -> list[dict[str, Any]]:
    return [
        {
            "pipeline_id": "ci",
            "default_corpus_id": "ci_docs",
            "allowed_corpus_ids": ["ci_docs"],
            "allowed_tools": [],
            "allowed_providers": ["ci-mock"],
            "allowed_models": ["ci-mock-model"],
            "max_top_k": 3,
            "default_provider": "ci-mock",
            "default_model": "ci-mock-model",
        }
    ]


def _processor_payload(processor_id: str = "ci-generic") -> list[dict[str, Any]]:
    return [{"processor_id": processor_id, "type": "generic", "enabled": True, "config": {}}]


def _retrieval_profile_payload(profile_id: str = "ci-hybrid") -> list[dict[str, Any]]:
    return [{"retrieval_profile_id": profile_id, "type": "hybrid", "enabled": True, "config": {}}]


def _mcp_payload(url: str = "http://mock-mcp:8088/mcp") -> dict[str, Any]:
    return {
        "selected_servers": ["ci-mcp"],
        "servers": [{"name": "ci-mcp", "transport": "streamable_http", "url": url, "headers": {}}],
        "timeout_s": 5.0,
        "strict": False,
        "max_tool_rounds": 2,
    }


def _corpus_payload(corpus_id: str = "layer2b_docs") -> dict[str, Any]:
    return {
        "corpus_id": corpus_id,
        "title": "Layer 2B docs",
        "environment": "ci",
        "tenant_id": "ci-tenant",
        "metadata": {"purpose": "authenticated-dast"},
    }


def _source_payload(source_id: str = "layer2b-source") -> dict[str, Any]:
    return {
        "source_id": source_id,
        "type": "object",
        "format": "text",
        "object_uri": "s3://tokenstream-ci/layer2b/source.txt",
        "metadata": {"purpose": "authenticated-dast"},
    }


def _validate_no_plaintext_keys(observed: dict[str, Any]) -> tuple[bool, str]:
    preview = str(observed.get("body_preview") or "")
    if "plaintext_key" in preview or re.search(r"sk_[A-Za-z0-9_\-]{16,}", preview):
        return False, "API key listing exposed plaintext key material"
    return True, ""


def _validate_login_cookie_flags(observed: dict[str, Any]) -> tuple[bool, str]:
    set_cookie = str((observed.get("headers") or {}).get("set-cookie") or "").lower()
    missing = [flag for flag in ("httponly", "samesite=lax") if flag not in set_cookie]
    if missing:
        return False, f"login session cookie missing flags: {', '.join(missing)}"
    return True, ""


def _validate_session_user(observed: dict[str, Any], username: str) -> tuple[bool, str]:
    body = _parse_json(str(observed.get("body_preview") or ""))
    if body.get("username") != username:
        return False, f"expected session username {username!r}"
    return True, ""


def _case_url(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{DEV_UI_URL}{path}"


def _build_cases() -> list[ProbeCase]:
    metadata_url = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
    return [
        ProbeCase(
            "L2B-AUTH-ME-ADMIN",
            "cache_session_behavior",
            "config-auth",
            "admin-session",
            "Admin session resolves to the expected identity.",
            "GET",
            _case_url("/v1/auth/me"),
            {200},
            validator=lambda observed: _validate_session_user(observed, "admin"),
        ),
        ProbeCase(
            "L2B-AUTH-COOKIE-FLAGS",
            "cache_session_behavior",
            "config-auth",
            "fresh-admin-login",
            "Login response sets basic session cookie hardening flags.",
            "POST",
            _case_url("/v1/auth/login"),
            {200},
            body={"username": "admin", "password": "admin"},
            validator=_validate_login_cookie_flags,
        ),
        ProbeCase(
            "L2B-VIEWER-PROVIDERS-READ",
            "role_abuse",
            "management-api",
            "viewer-session",
            "Viewer can read providers.",
            "GET",
            _case_url("/v1/management/providers"),
            {200},
        ),
        ProbeCase(
            "L2B-VIEWER-PROVIDERS-WRITE",
            "role_abuse",
            "management-api",
            "viewer-session",
            "Viewer cannot replace provider configuration.",
            "PUT",
            _case_url("/v1/management/providers"),
            {403},
            body=_provider_payload("viewer-forbidden"),
            auth_boundary_bypass_on_2xx=True,
        ),
        ProbeCase(
            "L2B-VIEWER-USER-PRIVESC",
            "privilege_escalation",
            "management-api",
            "viewer-session",
            "Viewer cannot grant itself admin role.",
            "PUT",
            _case_url("/v1/management/users"),
            {403},
            body=[{"username": "viewer", "password": "viewer-password", "roles": ["admin"], "is_active": True}],
            auth_boundary_bypass_on_2xx=True,
        ),
        ProbeCase(
            "L2B-VIEWER-KEY-PRIVESC",
            "privilege_escalation",
            "management-api",
            "viewer-session",
            "Viewer cannot create an admin-scoped API key.",
            "POST",
            _case_url("/v1/management/api-keys"),
            {403},
            body={"subject": "viewer-admin-attempt", "scopes": ["admin:*"]},
            auth_boundary_bypass_on_2xx=True,
        ),
        ProbeCase(
            "L2B-OPERATOR-PROVIDERS-WRITE",
            "unsafe_mutation",
            "management-api",
            "operator-session",
            "Operator can update operational provider configuration in the disposable stack.",
            "PUT",
            _case_url("/v1/management/providers"),
            {200},
            body=_provider_payload("operator-allowed"),
        ),
        ProbeCase(
            "L2B-OPERATOR-USERS-WRITE",
            "role_abuse",
            "management-api",
            "operator-session",
            "Operator cannot mutate users.",
            "PUT",
            _case_url("/v1/management/users"),
            {403},
            body=[],
            auth_boundary_bypass_on_2xx=True,
        ),
        ProbeCase(
            "L2B-OPERATOR-KEY-ADMINSTAR",
            "privilege_escalation",
            "management-api",
            "operator-session",
            "Operator admin:* API key creation attempt is captured as current behavior.",
            "POST",
            _case_url("/v1/management/api-keys"),
            {200, 403},
            body={"subject": "operator-adminstar-attempt", "scopes": ["admin:*"]},
            unsafe_mutation_on_2xx=True,
            known_gap=True,
        ),
        ProbeCase(
            "L2B-SERVICE-STATUS-READ",
            "service_key_scope",
            "management-api",
            "status-service-key",
            "Status-only service key can read status.",
            "GET",
            _case_url("/v1/management/status"),
            {200},
        ),
        ProbeCase(
            "L2B-SERVICE-STATUS-NO-PROVIDERS",
            "service_key_scope",
            "management-api",
            "status-service-key",
            "Status-only service key cannot read providers.",
            "GET",
            _case_url("/v1/management/providers"),
            {403},
            auth_boundary_bypass_on_2xx=True,
        ),
        ProbeCase(
            "L2B-SERVICE-CORPORA-READ",
            "service_key_scope",
            "management-api",
            "corpora-service-key",
            "Corpora-read service key can read corpora.",
            "GET",
            _case_url("/v1/management/corpora"),
            {200},
        ),
        ProbeCase(
            "L2B-SERVICE-CORPORA-NO-WRITE",
            "service_key_scope",
            "management-api",
            "corpora-service-key",
            "Corpora-read service key cannot create corpora.",
            "POST",
            _case_url("/v1/management/corpora"),
            {403},
            body={"corpus_id": "service-forbidden"},
            auth_boundary_bypass_on_2xx=True,
        ),
        ProbeCase(
            "L2B-KEYS-LIST-NO-PLAINTEXT",
            "response_leakage",
            "management-api",
            "keys-reader-service-key",
            "API key listing does not expose plaintext key material.",
            "GET",
            _case_url("/v1/management/api-keys"),
            {200},
            validator=_validate_no_plaintext_keys,
        ),
        ProbeCase(
            "L2B-MALFORMED-PROVIDERS-SHAPE",
            "malformed_management_payload",
            "management-api",
            "admin-session",
            "Provider replacement rejects object bodies where a list is required.",
            "PUT",
            _case_url("/v1/management/providers"),
            {422},
            body={"not": "a-list"},
        ),
        ProbeCase(
            "L2B-MALFORMED-POLICIES-SHAPE",
            "malformed_management_payload",
            "management-api",
            "admin-session",
            "Policy replacement rejects object bodies where a list is required.",
            "PUT",
            _case_url("/v1/management/policies"),
            {422},
            body={"not": "a-list"},
        ),
        ProbeCase(
            "L2B-MALFORMED-APIKEY-SCOPES",
            "malformed_management_payload",
            "management-api",
            "admin-session",
            "API key creation rejects non-list scopes.",
            "POST",
            _case_url("/v1/management/api-keys"),
            {422},
            body={"subject": "bad-scopes", "scopes": "admin:*"},
        ),
        ProbeCase(
            "L2B-UNSAFE-PROVIDER-SSRF-SHAPE",
            "unsafe_mutation",
            "management-api",
            "operator-session",
            "Provider base URL accepts or rejects cloud-metadata-shaped input.",
            "PUT",
            _case_url("/v1/management/providers"),
            {200, 422},
            body=_provider_payload("metadata-shaped-provider", base_url=metadata_url),
            unsafe_mutation_on_2xx=True,
            known_gap=True,
        ),
        ProbeCase(
            "L2B-UNSAFE-RAG-UPSTREAM-SHAPE",
            "unsafe_mutation",
            "management-api",
            "operator-session",
            "RAG settings accept or reject cloud-metadata-shaped retrieval upstream URL.",
            "PUT",
            _case_url("/v1/management/rag-settings"),
            {200, 422},
            body={
                "default_corpus_id": "ci_docs",
                "selected_corpus_ids": ["ci_docs"],
                "default_top_k": 1,
                "retrieval_api_url": metadata_url,
            },
            unsafe_mutation_on_2xx=True,
            known_gap=True,
        ),
        ProbeCase(
            "L2B-UNSAFE-MCP-UPSTREAM-SHAPE",
            "unsafe_mutation",
            "management-api",
            "operator-session",
            "MCP settings accept or reject cloud-metadata-shaped server URLs.",
            "PUT",
            _case_url("/v1/management/mcp-settings"),
            {200, 422},
            body=_mcp_payload(metadata_url),
            unsafe_mutation_on_2xx=True,
            known_gap=True,
        ),
        ProbeCase(
            "L2B-CORPUS-CREATE",
            "corpora_sources",
            "management-api",
            "operator-session",
            "Operator can create a disposable corpus.",
            "POST",
            _case_url("/v1/management/corpora"),
            {200, 400},
            body=_corpus_payload(),
        ),
        ProbeCase(
            "L2B-SOURCE-OBJECT-CREATE",
            "corpora_sources",
            "management-api",
            "operator-session",
            "Operator can create object-backed source metadata.",
            "POST",
            _case_url("/v1/management/corpora/layer2b_docs/sources"),
            {200, 404},
            body=_source_payload(),
        ),
        ProbeCase(
            "L2B-SOURCE-ID-TRAVERSAL",
            "corpora_sources",
            "management-api",
            "operator-session",
            "Source path traversal-shaped identifiers are rejected.",
            "POST",
            _case_url("/v1/management/corpora/layer2b_docs/sources"),
            {400, 422},
            body=_source_payload("../secret"),
        ),
        ProbeCase(
            "L2B-CORPUS-IMPORT-BAD-SCHEMA",
            "corpora_sources",
            "management-api",
            "operator-session",
            "Corpus registry import rejects unsupported schema versions.",
            "POST",
            _case_url("/v1/management/corpora/registry-import"),
            {400, 422},
            body={"bundle": {"schema_version": "wrong", "corpus": _corpus_payload("imported")}},
        ),
        ProbeCase(
            "L2B-PROCESSORS-MALFORMED-ID",
            "processors",
            "management-api",
            "operator-session",
            "Processor registry rejects path traversal-shaped IDs.",
            "PUT",
            _case_url("/v1/management/processors"),
            {422},
            body=_processor_payload("../processor"),
        ),
        ProbeCase(
            "L2B-RETRIEVAL-PROFILE-MALFORMED-ID",
            "retrieval_profiles",
            "management-api",
            "operator-session",
            "Retrieval profile registry rejects path traversal-shaped IDs.",
            "PUT",
            _case_url("/v1/management/retrieval-profiles"),
            {422},
            body=_retrieval_profile_payload("../profile"),
        ),
        ProbeCase(
            "L2B-MCP-MALFORMED-TIMEOUT",
            "mcp_settings",
            "management-api",
            "operator-session",
            "MCP settings reject invalid timeout types.",
            "PUT",
            _case_url("/v1/management/mcp-settings"),
            {422},
            body={**_mcp_payload(), "timeout_s": "not-a-number"},
        ),
        ProbeCase(
            "L2B-KEYS-LIST-NO-PLAINTEXT-AFTER-MUTATION",
            "response_leakage",
            "management-api",
            "keys-reader-service-key",
            "API key listing still avoids plaintext after Layer 2B mutation probes.",
            "GET",
            _case_url("/v1/management/api-keys"),
            {200},
            validator=_validate_no_plaintext_keys,
        ),
    ]


def _leak_findings(observed: dict[str, Any]) -> list[str]:
    preview = json.dumps(observed, sort_keys=True)
    findings = [pattern for pattern in LEAK_PATTERNS if pattern in preview]
    findings.extend("<secret-value>" for value in SECRET_VALUES if value and value in preview)
    return sorted(set(findings))


def _run_case(case: ProbeCase, identities: dict[str, Identity]) -> dict[str, Any]:
    identity = identities[case.identity]
    observed = _request(
        case.method,
        case.url,
        body=case.body,
        raw_body=case.raw_body,
        headers=identity.headers,
        opener=identity.opener,
        content_type=case.content_type,
    )
    status = observed.get("status")
    status_matched = status in case.expected_statuses
    validator_passed = True
    validator_message = ""
    if case.validator and status_matched:
        validator_passed, validator_message = case.validator(observed)
    leak_findings = _leak_findings(observed)
    auth_boundary_bypass = bool(case.auth_boundary_bypass_on_2xx and isinstance(status, int) and 200 <= status < 300)
    unsafe_mutation = bool(case.unsafe_mutation_on_2xx and isinstance(status, int) and 200 <= status < 300)
    unhandled_error = bool(isinstance(status, int) and status >= 500 and not case.allow_5xx)
    counted_leak = bool(leak_findings and not case.known_gap)
    passed = (
        status_matched and validator_passed and not auth_boundary_bypass and not counted_leak and not unhandled_error
    )
    return {
        "check_id": case.check_id,
        "category": case.category,
        "target": case.target,
        "identity": case.identity,
        "description": case.description,
        "method": case.method,
        "url": case.url,
        "request_body": "<redacted>",
        "expected_statuses": sorted(case.expected_statuses),
        "observed": observed,
        "status_matched": status_matched,
        "validator_passed": validator_passed,
        "validator_message": validator_message,
        "auth_boundary_bypass": auth_boundary_bypass,
        "unsafe_mutation_observed": unsafe_mutation,
        "unhandled_error": unhandled_error,
        "leakage_findings": leak_findings,
        "known_gap": case.known_gap,
        "passed": passed,
        "outcome": "accepted_known_gap" if case.known_gap and status_matched else "passed" if passed else "failed",
    }


def _session_logout_check() -> tuple[dict[str, Any], dict[str, Any]]:
    identity, login = _login_identity("logout-session", "admin", "admin")
    observed = _request("POST", f"{DEV_UI_URL}/v1/auth/logout", opener=identity.opener)
    after = _request("GET", f"{DEV_UI_URL}/v1/auth/me", opener=identity.opener)
    result = {
        "check_id": "L2B-AUTH-LOGOUT-INVALIDATES-SESSION",
        "category": "cache_session_behavior",
        "target": "config-auth",
        "identity": "logout-session",
        "description": "Logout invalidates the current session cookie.",
        "method": "POST",
        "url": f"{DEV_UI_URL}/v1/auth/logout",
        "request_body": "<redacted>",
        "expected_statuses": [200],
        "observed": {"logout": observed, "me_after_logout": after},
        "status_matched": observed.get("status") == 200 and after.get("status") == 401,
        "validator_passed": True,
        "validator_message": "",
        "auth_boundary_bypass": after.get("status") != 401,
        "unsafe_mutation_observed": False,
        "unhandled_error": False,
        "leakage_findings": _leak_findings(observed) + _leak_findings(after),
        "known_gap": False,
    }
    result["passed"] = bool(
        result["status_matched"] and not result["auth_boundary_bypass"] and not result["leakage_findings"]
    )
    result["outcome"] = "passed" if result["passed"] else "failed"
    return login, result


def build_report() -> dict[str, Any]:
    setup: list[dict[str, Any]] = [_bad_login()]
    identities: dict[str, Identity] = {}

    admin, admin_login = _login_identity("admin-session", "admin", "admin")
    setup.append(admin_login)
    identities["admin-session"] = admin

    if admin_login["passed"]:
        setup.append(_seed_users(admin))
        for name, scopes in (
            ("status-service-key", ["status:read"]),
            ("corpora-service-key", ["corpora:read"]),
            ("keys-reader-service-key", ["keys:read"]),
        ):
            identity, created = _create_service_key(admin, name, scopes)
            identities[name] = identity
            setup.append(created)

    viewer, viewer_login = _login_identity("viewer-session", "viewer", "viewer-password")
    operator, operator_login = _login_identity("operator-session", "operator", "operator-password")
    fresh_admin, _ = _login_identity("fresh-admin-login", "admin", "admin")
    setup.extend([viewer_login, operator_login])
    identities.update(
        {
            "viewer-session": viewer,
            "operator-session": operator,
            "fresh-admin-login": fresh_admin,
        }
    )

    results = [_run_case(case, identities) for case in _build_cases()]
    logout_login, logout_result = _session_logout_check()
    setup.append(logout_login)
    results.append(logout_result)

    categories: dict[str, dict[str, int]] = {}
    for item in results:
        category = item["category"]
        categories.setdefault(category, {"checks": 0, "failures": 0, "known_gaps": 0})
        categories[category]["checks"] += 1
        if not item["passed"] and not item["known_gap"]:
            categories[category]["failures"] += 1
        if item["known_gap"]:
            categories[category]["known_gaps"] += 1

    setup_failures = [item for item in setup if not item["passed"]]
    status_mismatches = [item for item in results if not item["status_matched"] and not item["known_gap"]]
    validator_failures = [item for item in results if not item["validator_passed"] and not item["known_gap"]]
    auth_boundary_bypasses = [item for item in results if item["auth_boundary_bypass"] and not item["known_gap"]]
    unsafe_mutations = [item for item in results if item["unsafe_mutation_observed"]]
    leakage = [item for item in results if item["leakage_findings"] and not item["known_gap"]]
    unhandled_errors = [item for item in results if item["unhandled_error"] and not item["known_gap"]]
    known_gaps = [item for item in results if item["known_gap"]]

    return {
        "product": "TokenStream",
        "inventory_type": "dast-authenticated-layer-2b-management-probe",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "authenticated-layer-2b-management-mock-stack",
        "identities": {
            "session_roles": {
                "viewer": "read-only management role",
                "operator": "operational management writer without users:write",
                "admin": "full config-auth management role",
            },
            "service_keys": {
                "status-service-key": ["status:read"],
                "corpora-service-key": ["corpora:read"],
                "keys-reader-service-key": ["keys:read"],
            },
        },
        "targets": {
            "dev_ui_config_auth": DEV_UI_URL,
        },
        "summary": {
            "setup_count": len(setup),
            "setup_failures": len(setup_failures),
            "check_count": len(results),
            "passed_count": sum(1 for item in results if item["passed"]),
            "status_mismatch_count": len(status_mismatches),
            "validator_failure_count": len(validator_failures),
            "auth_boundary_bypass_count": len(auth_boundary_bypasses),
            "unsafe_mutation_observed_count": len(unsafe_mutations),
            "leakage_count": len(leakage),
            "unhandled_error_count": len(unhandled_errors),
            "known_gap_count": len(known_gaps),
            "categories": categories,
        },
        "setup": setup,
        "checks": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe authenticated Layer 2B management DAST behavior.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fail-on-setup-failure", action="store_true")
    parser.add_argument("--fail-on-status-mismatch", action="store_true")
    parser.add_argument("--fail-on-validator-failure", action="store_true")
    parser.add_argument("--fail-on-auth-boundary-bypass", action="store_true")
    parser.add_argument("--fail-on-leakage", action="store_true")
    parser.add_argument("--fail-on-unhandled-error", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary = report["summary"]
    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.fail_on_setup_failure and summary["setup_failures"]:
        raise SystemExit("One or more Layer 2B setup steps failed")
    if args.fail_on_status_mismatch and summary["status_mismatch_count"]:
        raise SystemExit("One or more Layer 2B checks returned an unexpected status")
    if args.fail_on_validator_failure and summary["validator_failure_count"]:
        raise SystemExit("One or more Layer 2B semantic validators failed")
    if args.fail_on_auth_boundary_bypass and summary["auth_boundary_bypass_count"]:
        raise SystemExit("One or more Layer 2B checks detected an authorization boundary bypass")
    if args.fail_on_leakage and summary["leakage_count"]:
        raise SystemExit("One or more Layer 2B checks detected response leakage")
    if args.fail_on_unhandled_error and summary["unhandled_error_count"]:
        raise SystemExit("One or more Layer 2B checks returned an unhandled server error")


if __name__ == "__main__":
    main()
