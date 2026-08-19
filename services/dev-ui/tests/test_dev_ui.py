from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient


SERVICES_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SERVICES_ROOT.parent
DEV_UI_MAIN = SERVICES_ROOT / "dev-ui" / "main.py"


def load_dev_ui_module(tmp_path, monkeypatch):
    frontend_dist = tmp_path / "frontend-dist"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text('<!doctype html><div id="root"></div>', encoding="utf-8")

    monkeypatch.setenv("CONFIG_AUTH_DB_PATH", str(tmp_path / "config_auth.db"))
    monkeypatch.setenv("CONFIG_AUTH_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("CONFIG_AUTH_DEV_BOOTSTRAP_ADMIN", "true")
    monkeypatch.setenv("CONFIG_AUTH_INTERNAL_TOKEN", "test-internal-token")
    monkeypatch.setenv("ORCHESTRATOR_RELOAD_URL", "")
    monkeypatch.setenv("DEV_UI_FRONTEND_DIST_DIR", str(frontend_dist))

    sys.path.insert(0, str(REPO_ROOT / "packages"))
    sys.path.insert(0, str(SERVICES_ROOT / "common"))
    try:
        for name in [
            "dev_ui_main",
            "config_auth",
            "config_auth.app",
            "config_auth.app.main",
            "config_auth.app.db",
            "config_auth.app.models",
            "config_auth.app.security",
        ]:
            sys.modules.pop(name, None)

        spec = importlib.util.spec_from_file_location("dev_ui_main", DEV_UI_MAIN)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["dev_ui_main"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if str(REPO_ROOT / "packages") in sys.path:
            sys.path.remove(str(REPO_ROOT / "packages"))
        if str(SERVICES_ROOT / "common") in sys.path:
            sys.path.remove(str(SERVICES_ROOT / "common"))


def load_dev_ui_app(tmp_path, monkeypatch):
    return load_dev_ui_module(tmp_path, monkeypatch).app


def test_dev_ui_mounts_internal_config_auth_routes(tmp_path, monkeypatch):
    app = load_dev_ui_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        missing_auth_response = client.get("/internal/ingestion-jobs")
        assert missing_auth_response.status_code == 401

        authed_response = client.get(
            "/internal/ingestion-jobs?status=pending",
            headers={"Authorization": "Bearer test-internal-token"},
        )
        assert authed_response.status_code == 200
        assert authed_response.json() == []


@pytest.mark.asyncio
async def test_chunking_dry_run_proxy_injects_path_corpus_id(tmp_path, monkeypatch):
    module = load_dev_ui_module(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "INGESTION_WORKER_URL", "http://ingestion-worker.test")
    calls = []

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            assert timeout == 300.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, json):
            calls.append((url, json))
            return httpx.Response(
                200,
                json={"chunks": [{"text": "preview"}]},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)

    result = await module.proxy_chunking_dry_run(
        "corpus-a",
        {"source_id": "source-1", "corpus_id": "wrong-client-value"},
        object(),
    )

    assert result == {"chunks": [{"text": "preview"}]}
    assert calls == [
        (
            "http://ingestion-worker.test/v1/dry-run/chunking",
            {"source_id": "source-1", "corpus_id": "corpus-a"},
        )
    ]
