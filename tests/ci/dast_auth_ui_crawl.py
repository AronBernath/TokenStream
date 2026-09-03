"""Authenticated Layer 2C DAST crawl probes for the TokenStream dev UI."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dast_auth_management_probe import DEV_UI_URL, SECRET_VALUES, _bad_login, _redact, _seed_users


DEFAULT_TIMEOUT_MS = 15_000
UI_BASE_URL = os.environ.get("DEV_UI_URL", DEV_UI_URL).rstrip("/")
LEAK_PATTERNS = (
    "Traceback (most recent call last)",
    'File "',
    "/home/runner/",
    "/workspace/",
    "site-packages",
    "plaintext_key",
    "key_hash",
    "config_auth_session=",
)
SENSITIVE_RESPONSE_PATHS = ("/v1/auth/", "/v1/management/")
POST_LOGIN_REQUIRED_HEADERS = (
    "cache-control",
    "pragma",
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
)


@dataclass(frozen=True)
class UiRoute:
    route_id: str
    hash_path: str
    expected_heading: str
    category: str = "authenticated_page_crawl"
    reflected_payload: str = ""


UI_ROUTES = (
    UiRoute("dashboard", "#dashboard", "Dashboard"),
    UiRoute("corpora", "#corpora", "Corpora & Sources"),
    UiRoute("providers", "#providers", "Providers"),
    UiRoute("policies", "#policies", "Policies"),
    UiRoute("keys", "#keys", "Machine API Keys"),
    UiRoute("users", "#users", "Users"),
    UiRoute("rag", "#rag", "RAG Settings"),
    UiRoute("mcp", "#mcp", "MCP Settings"),
    UiRoute("corpus-details", "#corpora/details?corpus=ci_docs", "Corpus Details"),
)

REFLECTED_ROUTES = (
    UiRoute(
        "reflected-corpus-query",
        "#corpora/details?corpus=%3Cscript%3Ealert%281%29%3C%2Fscript%3E",
        "Corpus Details",
        category="reflected_input_behavior",
        reflected_payload="<script>alert(1)</script>",
    ),
    UiRoute(
        "reflected-unknown-hash",
        "#%3Cimg%20src%3Dx%20onerror%3Dalert%281%29%3E",
        "Dashboard",
        category="reflected_input_behavior",
        reflected_payload="<img src=x onerror=alert(1)>",
    ),
)

IDENTITIES = (
    ("viewer-session", "viewer", "viewer-password"),
    ("operator-session", "operator", "operator-password"),
    ("admin-session", "admin", "admin"),
)


def _import_playwright():
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Playwright is required for Layer 2C UI crawl probes. "
            "Install it with: python -m pip install playwright && python -m playwright install chromium"
        ) from exc
    return sync_playwright, PlaywrightError, PlaywrightTimeoutError


def _seed_layer_2c_users() -> list[dict[str, Any]]:
    setup = [_bad_login()]
    from dast_auth_management_probe import _login_identity

    admin, admin_login = _login_identity("layer2c-admin-seed-session", "admin", "admin")
    setup.append(admin_login)
    if admin_login["passed"]:
        setup.append(_seed_users(admin))
    return setup


def _selected_headers(headers: dict[str, str]) -> dict[str, str]:
    selected = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in POST_LOGIN_REQUIRED_HEADERS or lowered in ("content-type", "set-cookie"):
            selected[lowered] = _redact(str(value))
    return selected


def _same_origin_path(url: str) -> str:
    target = urlparse(url)
    base = urlparse(UI_BASE_URL)
    if target.scheme != base.scheme or target.netloc != base.netloc:
        return ""
    return target.path or "/"


def _is_sensitive_response(url: str) -> bool:
    path = _same_origin_path(url)
    return any(path.startswith(prefix) for prefix in SENSITIVE_RESPONSE_PATHS)


def _is_cache_hardened(headers: dict[str, str]) -> bool:
    cache_control = str(headers.get("cache-control") or "").lower()
    pragma = str(headers.get("pragma") or "").lower()
    return any(token in cache_control for token in ("no-store", "no-cache", "private")) or "no-cache" in pragma


def _missing_headers(headers: dict[str, str], names: tuple[str, ...]) -> list[str]:
    lowered = {key.lower() for key in headers}
    return [name for name in names if name not in lowered]


def _leak_findings(text: str) -> list[str]:
    redacted = _redact(text)
    findings = [pattern for pattern in LEAK_PATTERNS if pattern in redacted]
    findings.extend("<secret-value>" for value in SECRET_VALUES if value and value in text)
    findings.extend(re.findall(r"sk_[A-Za-z0-9_\-]{16,}", text))
    return sorted(set(findings))


def _response_record(response: Any) -> dict[str, Any] | None:
    path = _same_origin_path(response.url)
    if not path:
        return None
    return {
        "method": response.request.method,
        "url": response.url,
        "path": path,
        "status": response.status,
        "headers": _selected_headers(response.headers),
    }


def _route_url(route: UiRoute) -> str:
    return f"{UI_BASE_URL}/{route.hash_path}"


def _safe_inner_text(page: Any) -> str:
    try:
        return page.locator("body").inner_text(timeout=5_000)
    except Exception:
        return ""


def _safe_heading(page: Any) -> str:
    try:
        return str(page.locator("h1").first.text_content(timeout=5_000) or "").strip()
    except Exception:
        return ""


def _safe_title(page: Any) -> str:
    try:
        return str(page.title())
    except Exception:
        return ""


def _login(page: Any, username: str, password: str) -> dict[str, Any]:
    observed: dict[str, Any] = {"username": username, "status": "started"}
    page.goto(f"{UI_BASE_URL}/", wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    page.get_by_label("Username").fill(username, timeout=DEFAULT_TIMEOUT_MS)
    page.get_by_label("Password").fill(password, timeout=DEFAULT_TIMEOUT_MS)
    page.get_by_role("button", name=re.compile("Sign in", re.IGNORECASE)).click(timeout=DEFAULT_TIMEOUT_MS)
    page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
    heading = _safe_heading(page)
    text = _safe_inner_text(page)
    observed.update(
        {
            "status": "finished",
            "heading": heading,
            "title": _safe_title(page),
            "login_screen_visible": "Sign in" in text and "Username" in text,
        }
    )
    return observed


def _crawl_route(page: Any, identity: str, route: UiRoute) -> dict[str, Any]:
    responses: list[dict[str, Any]] = []
    console_messages: list[str] = []
    page_errors: list[str] = []
    dialogs: list[str] = []

    def on_response(response: Any) -> None:
        record = _response_record(response)
        if record is not None:
            responses.append(record)

    def on_console(message: Any) -> None:
        if message.type in {"error", "warning"}:
            console_messages.append(_redact(message.text)[:500])

    def on_page_error(error: Exception) -> None:
        page_errors.append(_redact(str(error))[:500])

    def on_dialog(dialog: Any) -> None:
        dialogs.append(_redact(dialog.message)[:500])
        dialog.dismiss()

    page.on("response", on_response)
    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("dialog", on_dialog)
    try:
        page.goto(_route_url(route), wait_until="networkidle", timeout=DEFAULT_TIMEOUT_MS)
        page.wait_for_timeout(300)
        heading = _safe_heading(page)
        body_text = _safe_inner_text(page)
        leak_findings = _leak_findings(body_text)
        reflected = bool(route.reflected_payload and route.reflected_payload in body_text)
        authenticated_api = [
            response
            for response in responses
            if _is_sensitive_response(response["url"]) and 200 <= int(response["status"]) < 300
        ]
        cacheable_authenticated = [
            response
            for response in authenticated_api
            if response["method"] != "OPTIONS" and not _is_cache_hardened(response["headers"])
        ]
        html_responses = [
            response
            for response in responses
            if response["path"] in {"/", "/index.html", "/admin"} and 200 <= int(response["status"]) < 300
        ]
        header_gaps = [
            {
                "path": response["path"],
                "missing_headers": _missing_headers(response["headers"], POST_LOGIN_REQUIRED_HEADERS),
            }
            for response in html_responses
        ]
        header_gaps = [gap for gap in header_gaps if gap["missing_headers"]]
        api_5xx = [
            response
            for response in responses
            if _is_sensitive_response(response["url"]) and int(response["status"]) >= 500
        ]
        login_screen_visible = "Sign in" in body_text and "Username" in body_text
        heading_matched = heading == route.expected_heading
        passed = (
            heading_matched
            and not login_screen_visible
            and not reflected
            and not dialogs
            and not page_errors
            and not leak_findings
            and not api_5xx
        )
        known_gap = bool(cacheable_authenticated or header_gaps)
        return {
            "check_id": f"L2C-{identity.upper().replace('-', '_')}-{route.route_id.upper().replace('-', '_')}",
            "category": route.category,
            "target": "dev-ui",
            "identity": identity,
            "description": f"Authenticated UI route crawl for {route.hash_path}",
            "url": _route_url(route),
            "expected_heading": route.expected_heading,
            "observed": {
                "heading": heading,
                "title": _safe_title(page),
                "login_screen_visible": login_screen_visible,
                "same_origin_response_count": len(responses),
                "authenticated_api_2xx_count": len(authenticated_api),
                "cacheable_authenticated_response_count": len(cacheable_authenticated),
                "post_login_header_gap_count": len(header_gaps),
                "console_messages": console_messages[:10],
                "page_errors": page_errors[:10],
                "dialogs": dialogs[:10],
                "api_5xx": api_5xx[:10],
                "cacheable_authenticated_responses": cacheable_authenticated[:10],
                "post_login_header_gaps": header_gaps[:10],
                "reflected_payload_observed": reflected,
                "leakage_findings": leak_findings,
            },
            "heading_matched": heading_matched,
            "known_gap": known_gap,
            "passed": passed
            or (
                known_gap
                and heading_matched
                and not reflected
                and not dialogs
                and not page_errors
                and not leak_findings
                and not api_5xx
            ),
            "outcome": (
                "accepted_known_gap"
                if known_gap
                and heading_matched
                and not reflected
                and not dialogs
                and not page_errors
                and not leak_findings
                and not api_5xx
                else "passed"
                if passed
                else "failed"
            ),
        }
    except Exception as exc:
        return {
            "check_id": f"L2C-{identity.upper().replace('-', '_')}-{route.route_id.upper().replace('-', '_')}",
            "category": route.category,
            "target": "dev-ui",
            "identity": identity,
            "description": f"Authenticated UI route crawl for {route.hash_path}",
            "url": _route_url(route),
            "expected_heading": route.expected_heading,
            "observed": {"error": _redact(str(exc))},
            "heading_matched": False,
            "known_gap": False,
            "passed": False,
            "outcome": "failed",
        }
    finally:
        page.remove_listener("response", on_response)
        page.remove_listener("console", on_console)
        page.remove_listener("pageerror", on_page_error)
        page.remove_listener("dialog", on_dialog)


def _logout_check(page: Any, identity: str) -> dict[str, Any]:
    try:
        page.get_by_role("button", name=re.compile("Logout", re.IGNORECASE)).click(timeout=DEFAULT_TIMEOUT_MS)
        page.get_by_role("button", name=re.compile("Sign in", re.IGNORECASE)).wait_for(timeout=DEFAULT_TIMEOUT_MS)
        after_me = page.evaluate(
            """async () => {
                const response = await fetch('/v1/auth/me', { credentials: 'same-origin', cache: 'no-store' });
                return { status: response.status };
            }"""
        )
        page.goto(f"{UI_BASE_URL}/#providers", wait_until="networkidle", timeout=DEFAULT_TIMEOUT_MS)
        body_text = _safe_inner_text(page)
        login_visible = "Sign in" in body_text and "Username" in body_text
        passed = bool(after_me.get("status") == 401 and login_visible)
        return {
            "check_id": f"L2C-{identity.upper().replace('-', '_')}-LOGOUT",
            "category": "session_logout_handling",
            "target": "dev-ui",
            "identity": identity,
            "description": "Logout invalidates the browser session and returns protected UI routes to the login screen.",
            "url": f"{UI_BASE_URL}/#providers",
            "observed": {
                "auth_me_after_logout_status": after_me.get("status"),
                "login_screen_visible_after_logout": login_visible,
            },
            "known_gap": False,
            "passed": passed,
            "outcome": "passed" if passed else "failed",
        }
    except Exception as exc:
        return {
            "check_id": f"L2C-{identity.upper().replace('-', '_')}-LOGOUT",
            "category": "session_logout_handling",
            "target": "dev-ui",
            "identity": identity,
            "description": "Logout invalidates the browser session and returns protected UI routes to the login screen.",
            "url": f"{UI_BASE_URL}/#providers",
            "observed": {"error": _redact(str(exc))},
            "known_gap": False,
            "passed": False,
            "outcome": "failed",
        }


def _run_identity(
    browser: Any, identity: str, username: str, password: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    context = browser.new_context(base_url=UI_BASE_URL, ignore_https_errors=False)
    page = context.new_page()
    login_observed = _login(page, username, password)
    setup = {
        "name": f"login {identity}",
        "identity": identity,
        "observed": login_observed,
        "passed": login_observed.get("status") == "finished" and not login_observed.get("login_screen_visible"),
    }
    checks: list[dict[str, Any]] = []
    if setup["passed"]:
        for route in (*UI_ROUTES, *REFLECTED_ROUTES):
            checks.append(_crawl_route(page, identity, route))
        checks.append(_logout_check(page, identity))
    context.close()
    return setup, checks


def build_report() -> dict[str, Any]:
    sync_playwright, _, _ = _import_playwright()
    setup = _seed_layer_2c_users()
    checks: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            for identity, username, password in IDENTITIES:
                login_setup, identity_checks = _run_identity(browser, identity, username, password)
                setup.append(login_setup)
                checks.extend(identity_checks)
        finally:
            browser.close()

    categories: dict[str, dict[str, int]] = {}
    for item in checks:
        category = item["category"]
        categories.setdefault(category, {"checks": 0, "failures": 0, "known_gaps": 0})
        categories[category]["checks"] += 1
        if not item["passed"] and not item["known_gap"]:
            categories[category]["failures"] += 1
        if item["known_gap"]:
            categories[category]["known_gaps"] += 1

    setup_failures = [item for item in setup if not item["passed"]]
    route_failures = [
        item
        for item in checks
        if item["category"] in {"authenticated_page_crawl", "reflected_input_behavior"} and not item["passed"]
    ]
    logout_failures = [item for item in checks if item["category"] == "session_logout_handling" and not item["passed"]]
    known_gaps = [item for item in checks if item["known_gap"]]
    api_5xx = [item for item in checks if item.get("observed", {}).get("api_5xx")]
    cacheable_authenticated = [
        item for item in checks if item.get("observed", {}).get("cacheable_authenticated_response_count", 0) > 0
    ]
    header_gaps = [item for item in checks if item.get("observed", {}).get("post_login_header_gap_count", 0) > 0]
    reflected = [item for item in checks if item.get("observed", {}).get("reflected_payload_observed")]
    leakage = [item for item in checks if item.get("observed", {}).get("leakage_findings")]

    return {
        "product": "TokenStream",
        "inventory_type": "dast-authenticated-layer-2c-ui-crawl-probe",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "authenticated-layer-2c-ui-crawl-mock-stack",
        "identities": {
            "session_roles": {
                "viewer": "read-only management role",
                "operator": "operational management writer without users:write",
                "admin": "full config-auth management role",
            },
        },
        "targets": {"dev_ui": UI_BASE_URL},
        "summary": {
            "setup_count": len(setup),
            "setup_failures": len(setup_failures),
            "check_count": len(checks),
            "passed_count": sum(1 for item in checks if item["passed"]),
            "route_failure_count": len(route_failures),
            "logout_failure_count": len(logout_failures),
            "api_5xx_count": len(api_5xx),
            "cacheable_authenticated_response_count": len(cacheable_authenticated),
            "post_login_header_gap_count": len(header_gaps),
            "reflected_input_failure_count": len(reflected),
            "unauthorized_data_exposure_count": len(leakage),
            "known_gap_count": len(known_gaps),
            "categories": categories,
        },
        "setup": setup,
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe authenticated Layer 2C dev-ui crawl behavior.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fail-on-setup-failure", action="store_true")
    parser.add_argument("--fail-on-route-failure", action="store_true")
    parser.add_argument("--fail-on-logout-failure", action="store_true")
    parser.add_argument("--fail-on-api-5xx", action="store_true")
    parser.add_argument("--fail-on-reflected-input", action="store_true")
    parser.add_argument("--fail-on-leakage", action="store_true")
    parser.add_argument("--fail-on-cacheable-authenticated-response", action="store_true")
    parser.add_argument("--fail-on-post-login-header-gap", action="store_true")
    return parser.parse_args()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    report = build_report()
    _write_json(args.output, report)
    summary = report["summary"]

    if args.fail_on_setup_failure and summary["setup_failures"]:
        raise SystemExit("One or more Layer 2C setup steps failed")
    if args.fail_on_route_failure and summary["route_failure_count"]:
        raise SystemExit("One or more Layer 2C UI routes failed to crawl")
    if args.fail_on_logout_failure and summary["logout_failure_count"]:
        raise SystemExit("One or more Layer 2C logout checks failed")
    if args.fail_on_api_5xx and summary["api_5xx_count"]:
        raise SystemExit("One or more Layer 2C UI routes observed authenticated API 5xx responses")
    if args.fail_on_reflected_input and summary["reflected_input_failure_count"]:
        raise SystemExit("One or more Layer 2C reflected input checks failed")
    if args.fail_on_leakage and summary["unauthorized_data_exposure_count"]:
        raise SystemExit("One or more Layer 2C UI routes exposed sensitive data")
    if args.fail_on_cacheable_authenticated_response and summary["cacheable_authenticated_response_count"]:
        raise SystemExit("One or more Layer 2C UI routes observed cacheable authenticated responses")
    if args.fail_on_post_login_header_gap and summary["post_login_header_gap_count"]:
        raise SystemExit("One or more Layer 2C UI routes observed post-login header gaps")


if __name__ == "__main__":
    main()
