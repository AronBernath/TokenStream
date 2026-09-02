from __future__ import annotations

import json
import os
import subprocess
import importlib
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


REPORT_PATH_ENV = "SECURITY_REGRESSION_REPORT_PATH"
DEFAULT_REPORT_PATH = "evidence/tests/security-regression-report.json"
REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"
PACKAGES_ROOT = REPO_ROOT / "packages"


@dataclass
class EvidenceResult:
    nodeid: str
    status: str
    actual_result: str


def pytest_configure(config: pytest.Config) -> None:
    config._security_evidence_results = {}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return
    if item.get_closest_marker("security_evidence") is None:
        return

    status = report.outcome
    actual_result = "passed"
    wasxfail = getattr(report, "wasxfail", None)
    if wasxfail and report.outcome == "skipped":
        status = "xfail"
        actual_result = f"expected failure: {wasxfail}"
    elif wasxfail and report.outcome == "passed":
        status = "xpass"
        actual_result = f"unexpected pass: {wasxfail}"
    elif report.failed:
        actual_result = str(report.longrepr)
    elif report.skipped:
        actual_result = "skipped"

    item.config._security_evidence_results[item.nodeid] = EvidenceResult(
        nodeid=item.nodeid,
        status=status,
        actual_result=actual_result,
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    results: list[dict[str, Any]] = []
    observed = session.config._security_evidence_results
    for item in session.items:
        marker = item.get_closest_marker("security_evidence")
        if marker is None:
            continue
        evidence = observed.get(item.nodeid, EvidenceResult(item.nodeid, "skipped", "not executed"))
        expected_result = marker.kwargs.get("expected_result", "")
        actual_result = evidence.actual_result
        if evidence.status == "passed" and expected_result:
            actual_result = expected_result
        payload = {
            "test_id": marker.kwargs.get("test_id", ""),
            "name": marker.kwargs.get("name", item.name),
            "target": marker.kwargs.get("target", ""),
            "control_ids": marker.kwargs.get("control_ids", []),
            "risk_ids": marker.kwargs.get("risk_ids", []),
            "cra_requirements": marker.kwargs.get("cra_requirements", []),
            "status": evidence.status,
            "expected_result": expected_result,
            "actual_result": actual_result,
            "notes": marker.kwargs.get("notes", ""),
            "nodeid": item.nodeid,
        }
        results.append(payload)

    if not results:
        return

    path = Path(os.environ.get(REPORT_PATH_ENV, DEFAULT_REPORT_PATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "product": "TokenStream",
        "assessment_scope": "CI/CD authz and security regression evidence",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git": {
            "commit": _git_value("rev-parse", "HEAD"),
            "branch": _git_value("branch", "--show-current"),
        },
        "test_environment": {
            "mode": os.environ.get("SECURITY_REGRESSION_MODE", "unit"),
            "embedder": "mock",
            "tei_required": False,
            "dev_ui_config_auth_mode": "combined",
        },
        "exitstatus": int(exitstatus),
        "results": results,
    }
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def evidence_marker(
    test_id: str,
    name: str,
    target: str,
    expected_result: str,
    *,
    control_ids: list[str] | None = None,
    risk_ids: list[str] | None = None,
    cra_requirements: list[str] | None = None,
    notes: str = "",
) -> pytest.MarkDecorator:
    return pytest.mark.security_evidence(
        test_id=test_id,
        name=name,
        target=target,
        control_ids=control_ids or [],
        risk_ids=risk_ids or [],
        cra_requirements=cra_requirements or [],
        expected_result=expected_result,
        notes=notes,
    )


def _git_value(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def _prepend_path(path: Path) -> None:
    text = str(path)
    if text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)


def _clear_modules(*prefixes: str) -> None:
    for name in list(sys.modules):
        if name in prefixes or any(name.startswith(f"{prefix}.") for prefix in prefixes):
            sys.modules.pop(name, None)


def _install_fake_qdrant(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_modules("qdrant_client")

    class FakeQdrantClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def collection_exists(self, *args: Any, **kwargs: Any) -> bool:
            return False

        def get_collections(self) -> Any:
            return types.SimpleNamespace(collections=[])

        def query_points(self, *args: Any, **kwargs: Any) -> Any:
            return types.SimpleNamespace(points=[])

        def search(self, *args: Any, **kwargs: Any) -> list[Any]:
            return []

        def search_points(self, *args: Any, **kwargs: Any) -> Any:
            return types.SimpleNamespace(result=[])

        def create_collection(self, *args: Any, **kwargs: Any) -> None:
            return None

        def upsert(self, *args: Any, **kwargs: Any) -> None:
            return None

        def delete(self, *args: Any, **kwargs: Any) -> None:
            return None

    def _model(**kwargs: Any) -> dict[str, Any]:
        return kwargs

    qdrant_mod = types.ModuleType("qdrant_client")
    qdrant_http_mod = types.ModuleType("qdrant_client.http")
    qdrant_models_mod = types.ModuleType("qdrant_client.http.models")
    qdrant_mod.QdrantClient = FakeQdrantClient
    qdrant_http_mod.models = qdrant_models_mod
    qdrant_models_mod.Distance = types.SimpleNamespace(COSINE="Cosine")
    qdrant_models_mod.VectorParams = _model
    qdrant_models_mod.PointStruct = _model
    qdrant_models_mod.PointIdsList = _model
    qdrant_models_mod.FilterSelector = _model
    qdrant_models_mod.Filter = _model
    qdrant_models_mod.FieldCondition = _model
    qdrant_models_mod.MatchValue = _model

    monkeypatch.setitem(sys.modules, "qdrant_client", qdrant_mod)
    monkeypatch.setitem(sys.modules, "qdrant_client.http", qdrant_http_mod)
    monkeypatch.setitem(sys.modules, "qdrant_client.http.models", qdrant_models_mod)


@pytest.fixture
def orchestrator_main(monkeypatch: pytest.MonkeyPatch):
    _clear_modules("app")
    _prepend_path(SERVICES_ROOT / "common")
    _prepend_path(SERVICES_ROOT / "orchestrator_api")
    monkeypatch.setenv("ORCHESTRATOR_API_KEY", "legacy-test-token")
    monkeypatch.setenv("MCP_SERVERS", "[]")
    monkeypatch.delenv("ORCHESTRATOR_API_KEYS_JSON", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_API_KEYS_PATH", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_PIPELINE_REGISTRY_JSON", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_PIPELINE_REGISTRY_PATH", raising=False)
    module = importlib.import_module("app.main")
    yield module
    _clear_modules("app")


@pytest.fixture
def config_auth_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _clear_modules("config_auth")
    _prepend_path(SERVICES_ROOT / "common")
    _prepend_path(PACKAGES_ROOT)
    monkeypatch.setenv("CONFIG_AUTH_DB_PATH", str(tmp_path / "config_auth.db"))
    monkeypatch.setenv("CONFIG_AUTH_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("CONFIG_AUTH_DEV_BOOTSTRAP_ADMIN", "true")
    monkeypatch.setenv("CONFIG_AUTH_INTERNAL_TOKEN", "test-internal-token")
    monkeypatch.setenv("ORCHESTRATOR_RELOAD_URL", "")
    monkeypatch.setenv("RETRIEVAL_API_URL", "http://retrieval-api.test:8000")
    monkeypatch.setenv("MCP_SERVERS", "[]")
    module = importlib.import_module("config_auth.app.main")
    module.repo.ensure_schema()
    module.repo.bootstrap_admin_if_needed(True)
    module.repo.import_or_seed_runtime_defaults(corpora=["kb_default"])
    module.repo.export_runtime_snapshots()
    yield module
    _clear_modules("config_auth")


@pytest.fixture
def retrieval_main(monkeypatch: pytest.MonkeyPatch):
    _clear_modules("app")
    _install_fake_qdrant(monkeypatch)
    _prepend_path(SERVICES_ROOT / "common")
    _prepend_path(SERVICES_ROOT / "retrieval_api")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.invalid:6333")
    monkeypatch.setenv("EMBEDDER_URL", "http://embedder.invalid")
    module = importlib.import_module("app.main")
    yield module
    _clear_modules("app")


@pytest.fixture
def ingestion_server(monkeypatch: pytest.MonkeyPatch):
    _clear_modules("worker")
    _install_fake_qdrant(monkeypatch)
    _prepend_path(SERVICES_ROOT / "common")
    _prepend_path(SERVICES_ROOT / "ingestion_worker")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.invalid:6333")
    monkeypatch.setenv("EMBEDDER_URL", "http://embedder.invalid")
    monkeypatch.setenv("REGISTRY_INTERNAL_URL", "http://registry.invalid/internal")
    monkeypatch.setenv("CONFIG_AUTH_INTERNAL_TOKEN", "test-internal-token")
    module = importlib.import_module("worker.server")
    yield module
    _clear_modules("worker")
