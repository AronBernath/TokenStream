from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


PACKAGES_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PACKAGES_ROOT.parent
COMMON_ROOT = REPO_ROOT / "services" / "common"


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    db_path = tmp_path / "config_auth.db"
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("CONFIG_AUTH_DB_PATH", str(db_path))
    monkeypatch.setenv("CONFIG_AUTH_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("CONFIG_AUTH_DEV_BOOTSTRAP_ADMIN", "true")
    monkeypatch.setenv("CONFIG_AUTH_INTERNAL_TOKEN", "test-internal-token")
    monkeypatch.setenv("ORCHESTRATOR_RELOAD_URL", "")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.5")
    monkeypatch.setenv("OPENAI_MODELS", "gpt-5.4-mini,gpt-5.5")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("DEEPSEEK_MODELS", "deepseek-v4-pro,deepseek-v4-flash")
    monkeypatch.setenv(
        "MCP_SERVERS",
        json.dumps(
            [
                {"name": "grafana", "transport": "streamable_http", "url": "http://grafana:9000/mcp"},
                {"name": "viz", "transport": "streamable_http", "url": "http://viz:8101/mcp"},
            ]
        ),
    )
    monkeypatch.setenv("MCP_TIMEOUT_S", "45")
    monkeypatch.setenv("MCP_MAX_TOOL_ROUNDS", "40")
    monkeypatch.setenv("DEFAULT_CORPUS_ID", "kb_default")
    monkeypatch.setenv("RETRIEVAL_API_URL", "http://retrieval-api:8000")

    sys.path.insert(0, str(PACKAGES_ROOT))
    sys.path.insert(0, str(COMMON_ROOT))
    try:
        for name in [
            "config_auth",
            "config_auth.app",
            "config_auth.app.main",
            "config_auth.app.db",
            "config_auth.app.models",
            "config_auth.app.security",
        ]:
            sys.modules.pop(name, None)
        module = importlib.import_module("config_auth.app.main")
        module.repo.ensure_schema()
        module.repo.bootstrap_admin_if_needed(True)
        module.repo.import_or_seed_runtime_defaults(corpora=["kb_default", "kb_archive", "kb_public"])
        module.repo.export_runtime_snapshots()
        yield module
    finally:
        for name in [
            "config_auth",
            "config_auth.app",
            "config_auth.app.main",
            "config_auth.app.db",
            "config_auth.app.models",
            "config_auth.app.security",
        ]:
            sys.modules.pop(name, None)
        if str(PACKAGES_ROOT) in sys.path:
            sys.path.remove(str(PACKAGES_ROOT))
        if str(COMMON_ROOT) in sys.path:
            sys.path.remove(str(COMMON_ROOT))


def login(client: TestClient) -> None:
    response = client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200


class FakeObjectStorage:
    def __init__(self):
        self.objects = {}

    def put_source_bytes(
        self,
        *,
        environment,
        tenant_id,
        corpus_id,
        source_id,
        original_name,
        content,
        content_type,
        metadata=None,
    ):
        import hashlib

        content_hash = hashlib.sha256(content).hexdigest()
        object_uri = (
            f"s3://test-bucket/{environment}/{tenant_id}/{corpus_id}/{source_id}/{content_hash}/{original_name}"
        )
        self.objects[object_uri] = content
        return SimpleNamespace(
            object_uri=object_uri,
            content_hash=content_hash,
            size_bytes=len(content),
            content_type=content_type,
            object_name="/".join(object_uri.split("/")[3:]),
        )


def install_fake_object_storage(app_module) -> FakeObjectStorage:
    storage = FakeObjectStorage()
    app_module.repo.object_storage = storage
    return storage


def test_registry_schema_includes_index_soft_delete_and_job_scope(app_module):
    with sqlite3.connect(app_module.DB_PATH) as conn:
        corpora_columns = {row[1] for row in conn.execute("PRAGMA table_info(corpora)")}
        source_columns = {row[1] for row in conn.execute("PRAGMA table_info(corpus_sources)")}
        job_columns = {row[1] for row in conn.execute("PRAGMA table_info(ingestion_jobs)")}
        processor_columns = {row[1] for row in conn.execute("PRAGMA table_info(processors)")}
        retrieval_profile_columns = {row[1] for row in conn.execute("PRAGMA table_info(retrieval_profiles)")}

    assert {
        "index_json",
        "deleted_at",
        "processor_id",
        "processor_config_json",
        "retrieval_profile_id",
        "retrieval_config_json",
    }.issubset(corpora_columns)
    assert {"deleted_at", "content_type", "processor_id", "processor_config_json"}.issubset(source_columns)
    assert {"environment", "tenant_id", "request_json", "plan_json", "stats_json"}.issubset(job_columns)
    assert {"processor_id", "payload_json", "created_at", "updated_at"}.issubset(processor_columns)
    assert {"retrieval_profile_id", "payload_json", "created_at", "updated_at"}.issubset(retrieval_profile_columns)


def test_registry_api_persists_schema_fields_and_enforces_internal_auth(app_module):
    client = TestClient(app_module.app)
    login(client)
    storage = install_fake_object_storage(app_module)

    create_corpus_response = client.post(
        "/v1/management/corpora",
        json={
            "corpus_id": "product_docs",
            "title": "Product Documentation",
            "environment": "prod",
            "tenant_id": "tenant-a",
            "chunking": {"target_chars": 2200},
            "index": {"vector_distance": "cosine"},
            "processor_id": "docs.generic",
            "processor_config": {"level": "corpus", "nested": {"a": 1}},
            "retrieval_config": {
                "filterable_fields": ["repo", "commit_sha"],
                "citation_fields": ["path", "start_line"],
                "lexical_fields": ["symbol", "path"],
                "default_filters": {"repo": "manuals"},
            },
            "metadata": {"owner": "docs"},
        },
    )
    assert create_corpus_response.status_code == 200
    created_corpus = create_corpus_response.json()
    assert created_corpus["index"] == {"vector_distance": "cosine"}
    assert created_corpus["processor_id"] == "docs.generic"
    assert created_corpus["processor_config"] == {"level": "corpus", "nested": {"a": 1}}
    assert created_corpus["retrieval_config"]["lexical_fields"] == ["symbol", "path"]
    assert created_corpus["retrieval_config"]["default_filters"] == {"repo": "manuals"}

    create_source_response = client.post(
        "/v1/management/corpora/product_docs/sources",
        json={
            "source_id": "docs_site",
            "type": "url",
            "url": "https://example.com/docs",
            "format": "html",
            "configuration": {"max_depth": 2},
            "processor_id": "docs.remote",
            "processor_config": {"level": "source"},
        },
    )
    assert create_source_response.status_code == 200
    created_source = create_source_response.json()
    assert created_source["configuration"] == {"max_depth": 2}
    assert created_source["processor_id"] == "docs.remote"
    assert created_source["processor_config"] == {"level": "source"}

    upload_response = client.post(
        "/v1/management/corpora/product_docs/sources/upload",
        data={
            "source_id": "product_manual",
            "format": "html",
            "configuration_json": '{"mode": "upload"}',
            "processor_id": "docs.upload",
            "processor_config_json": '{"level": "upload"}',
            "metadata_json": '{"repo": "manuals"}',
        },
        files={"upload": ("manual.html", b"<html><body>" + b"x" * 128 + b"</body></html>", "text/html")},
    )
    assert upload_response.status_code == 200
    uploaded_source = upload_response.json()
    assert uploaded_source["object_uri"].startswith("s3://test-bucket/prod/tenant-a/product_docs/product_manual/")
    assert uploaded_source["object_uri"] in storage.objects
    assert uploaded_source["content_type"] == "text/html"
    assert uploaded_source["configuration"] == {"mode": "upload"}
    assert uploaded_source["processor_id"] == "docs.upload"
    assert uploaded_source["processor_config"] == {"level": "upload"}
    assert uploaded_source["metadata"] == {"repo": "manuals"}

    create_job_response = client.post(
        "/v1/management/corpora/product_docs/ingestion-jobs",
        json={
            "source_ids": ["docs_site"],
            "processor_id": "docs.job",
            "processor_config": {"level": "job"},
            "configuration": {"priority": "normal"},
        },
    )
    assert create_job_response.status_code == 200
    job = create_job_response.json()
    assert job["environment"] == "prod"
    assert job["tenant_id"] == "tenant-a"
    assert job["request"]["configuration"] == {"priority": "normal"}
    assert job["request"]["processor_id"] == "docs.job"
    assert job["request"]["processor_config"] == {"level": "job"}

    no_token_response = client.get("/internal/corpora")
    assert no_token_response.status_code == 401
    internal_headers = {"Authorization": "Bearer test-internal-token"}
    assert client.get("/internal/corpora/product_docs", headers=internal_headers).status_code == 200

    claim_response = client.post(
        f"/internal/ingestion-jobs/{job['job_id']}/claim",
        headers=internal_headers,
        json={"worker_id": "worker-1"},
    )
    assert claim_response.status_code == 200
    assert (
        client.post(
            f"/internal/ingestion-jobs/{job['job_id']}/heartbeat",
            headers={**internal_headers, "x-worker-id": "worker-1"},
        ).status_code
        == 200
    )


def test_top_level_processor_and_retrieval_profile_registry(app_module):
    client = TestClient(app_module.app)
    login(client)

    processors_payload = [
        {
            "processor_id": "client.processor.v1",
            "type": "structured_archive",
            "name": "Client Processor",
            "config": {"include": ["**/*.py"], "metadata_defaults": {"source_kind": "code"}},
            "metadata": {"owner": "client-app"},
        }
    ]
    processor_put_response = client.put("/v1/management/processors", json=processors_payload)
    assert processor_put_response.status_code == 200
    assert processor_put_response.json()[0]["processor_id"] == "client.processor.v1"
    assert client.get("/v1/management/processors").json()[0]["type"] == "structured_archive"

    retrieval_profiles_payload = [
        {
            "retrieval_profile_id": "client.retrieval.v1",
            "type": "hybrid",
            "name": "Client Retrieval",
            "config": {
                "default_filters": {"repo": "orchestrator"},
                "filterable_fields": ["repo", "commit_sha", "path", "source_kind"],
                "lexical_fields": ["symbol", "path"],
                "citation_fields": ["path", "start_line", "end_line"],
                "strict_filters": True,
            },
        }
    ]
    retrieval_put_response = client.put("/v1/management/retrieval-profiles", json=retrieval_profiles_payload)
    assert retrieval_put_response.status_code == 200
    assert retrieval_put_response.json()[0]["retrieval_profile_id"] == "client.retrieval.v1"

    create_corpus_response = client.post(
        "/v1/management/corpora",
        json={
            "corpus_id": "sample_corpus",
            "processor_id": "client.processor.v1",
            "retrieval_profile_id": "client.retrieval.v1",
            "processor_config": {"include": ["services/**/*.py"]},
            "retrieval_config": {"default_filters": {"commit_sha": "abc123"}},
        },
    )
    assert create_corpus_response.status_code == 200
    corpus = create_corpus_response.json()
    assert corpus["processor_id"] == "client.processor.v1"
    assert corpus["retrieval_profile_id"] == "client.retrieval.v1"

    internal_headers = {"Authorization": "Bearer test-internal-token"}
    assert client.get("/internal/processors", headers=internal_headers).json()[0]["processor_id"] == (
        "client.processor.v1"
    )
    assert (
        client.get("/internal/retrieval-profiles", headers=internal_headers).json()[0]["retrieval_profile_id"]
        == "client.retrieval.v1"
    )

    processors_snapshot = json.loads((Path(app_module.RUNTIME_DIR) / "processors.json").read_text(encoding="utf-8"))
    retrieval_snapshot = json.loads(
        (Path(app_module.RUNTIME_DIR) / "retrieval_profiles.json").read_text(encoding="utf-8")
    )
    assert processors_snapshot["client.processor.v1"]["type"] == "structured_archive"
    assert retrieval_snapshot["client.retrieval.v1"]["config"]["lexical_fields"] == ["symbol", "path"]


def test_service_key_can_manage_corpus_lifecycle_and_poll_readiness(app_module):
    client = TestClient(app_module.app)
    login(client)
    storage = install_fake_object_storage(app_module)

    read_only_key_response = client.post(
        "/v1/management/api-keys",
        json={"subject": "doc-client-readonly", "scopes": ["corpora:read"]},
    )
    assert read_only_key_response.status_code == 200
    read_only_headers = {"Authorization": f"Bearer {read_only_key_response.json()['plaintext_key']}"}

    forbidden_response = client.put(
        "/v1/management/corpora/codebase_docs/ensure",
        json={"title": "Codebase Docs"},
        headers=read_only_headers,
    )
    assert forbidden_response.status_code == 403
    assert "corpora:write" in forbidden_response.json()["detail"]["error"]

    writer_key_response = client.post(
        "/v1/management/api-keys",
        json={"subject": "doc-client", "scopes": ["corpora:read", "corpora:write"]},
    )
    assert writer_key_response.status_code == 200
    writer_headers = {"Authorization": f"Bearer {writer_key_response.json()['plaintext_key']}"}

    ensure_response = client.put(
        "/v1/management/corpora/codebase_docs/ensure",
        json={
            "title": "Codebase Docs",
            "environment": "prod",
            "tenant_id": "tenant-a",
            "metadata": {"snapshot_kind": "committed_code"},
        },
        headers=writer_headers,
    )
    assert ensure_response.status_code == 200
    assert ensure_response.json()["corpus_id"] == "codebase_docs"
    assert ensure_response.json()["metadata"] == {"snapshot_kind": "committed_code"}

    ensure_again_response = client.put(
        "/v1/management/corpora/codebase_docs/ensure",
        json={"title": "A Different Title"},
        headers=writer_headers,
    )
    assert ensure_again_response.status_code == 200
    assert ensure_again_response.json()["title"] == "Codebase Docs"

    source_response = client.post(
        "/v1/management/corpora/codebase_docs/sources",
        json={
            "source_id": "snapshot_abc123",
            "type": "url",
            "title": "Committed Snapshot",
            "url": "https://example.com/snapshots/abc123.tar.gz",
            "format": "text",
            "metadata": {"commit_sha": "abc123"},
        },
        headers=writer_headers,
    )
    assert source_response.status_code == 200

    upload_response = client.post(
        "/v1/management/corpora/codebase_docs/sources/upload",
        data={
            "source_id": "snapshot_archive",
            "title": "Uploaded Snapshot",
            "format": "text",
            "tags_json": '["snapshot"]',
        },
        files={
            "upload": (
                "snapshot.txt",
                b"committed source snapshot\n" + b"a" * 128,
                "text/plain",
            )
        },
        headers=writer_headers,
    )
    assert upload_response.status_code == 200
    assert upload_response.json()["object_uri"] in storage.objects

    job_response = client.post(
        "/v1/management/corpora/codebase_docs/ingestion-jobs",
        json={"source_ids": ["snapshot_abc123", "snapshot_archive"], "configuration": {"reason": "documentation"}},
        headers=writer_headers,
    )
    assert job_response.status_code == 200
    job = job_response.json()

    pending_readiness = client.get(
        "/v1/management/corpora/codebase_docs/readiness",
        headers=writer_headers,
    )
    assert pending_readiness.status_code == 200
    assert pending_readiness.json()["ready"] is False
    assert pending_readiness.json()["status"] == "pending"
    assert "no_completed_ingestion_job" in pending_readiness.json()["reasons"]

    internal_headers = {"Authorization": "Bearer test-internal-token"}
    claim_response = client.post(
        f"/internal/ingestion-jobs/{job['job_id']}/claim",
        headers=internal_headers,
        json={"worker_id": "worker-1"},
    )
    assert claim_response.status_code == 200
    complete_response = client.patch(
        f"/internal/ingestion-jobs/{job['job_id']}",
        headers={**internal_headers, "x-worker-id": "worker-1"},
        json={"status": "completed", "stats": {"chunks": 12}},
    )
    assert complete_response.status_code == 200

    ready_response = client.get(
        "/v1/management/corpora/codebase_docs/readiness",
        headers=writer_headers,
    )
    assert ready_response.status_code == 200
    ready_payload = ready_response.json()
    assert ready_payload["ready"] is True
    assert ready_payload["status"] == "ready"
    assert ready_payload["latest_completed_job_id"] == job["job_id"]


def test_bootstrap_admin_login_and_me(app_module):
    client = TestClient(app_module.app)
    login(client)

    response = client.get("/v1/auth/me")
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "admin"
    assert "admin" in data["roles"]
    assert "users:write" in data["permissions"]


def test_deprecated_admin_aliases_are_removed(app_module):
    client = TestClient(app_module.app)
    login(client)

    # Verify that /v1/admin/users returns 404
    res = client.get("/v1/admin/users")
    assert res.status_code == 404

    # Verify that /v1/management/users returns 200
    res = client.get("/v1/management/users")
    assert res.status_code == 200


def test_users_response_does_not_expose_password_hash(app_module):
    client = TestClient(app_module.app)
    login(client)

    response = client.get("/v1/management/users")
    assert response.status_code == 200
    data = response.json()
    assert data
    assert "password_hash" not in data[0]


def test_machine_key_creation_uses_scrypt_snapshot(app_module):
    client = TestClient(app_module.app)
    login(client)

    response = client.post(
        "/v1/management/api-keys",
        json={"subject": "svc", "scopes": ["chat:invoke", "rag:query"]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["plaintext_key"].startswith("sk_")

    snapshot = json.loads((Path(app_module.RUNTIME_DIR) / "api_keys.json").read_text(encoding="utf-8"))
    assert snapshot
    assert snapshot[0]["key_algorithm"] == "scrypt"
    assert "plaintext_key" not in snapshot[0]


def test_provider_secret_ref_round_trip_without_plaintext(app_module):
    client = TestClient(app_module.app)
    login(client)

    providers = [
        {
            "name": "openai",
            "type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "require_api_key": True,
            "default_model": "gpt-test",
            "models": ["gpt-test"],
            "capabilities": {
                "tools": True,
                "json_schema": True,
                "streaming": True,
                "max_context_window": 8192,
                "default_context_window": 8192,
            },
            "client_controls": {"temperature": True, "max_tokens": True, "context_length": False},
            "secret_ref": "env://OPENAI_API_KEY",
            "secret_source_type": "env",
        }
    ]
    put_response = client.put("/v1/management/providers", json=providers)
    assert put_response.status_code == 200

    get_response = client.get("/v1/management/providers")
    assert get_response.status_code == 200
    data = get_response.json()
    assert data[0]["secret_ref"] == "env://OPENAI_API_KEY"
    assert data[0]["has_secret_ref"] is True
    assert data[0]["client_controls"]["temperature"] is True
    assert "api_key" not in data[0]


def test_provider_save_reports_orchestrator_reload_failure(app_module, monkeypatch):
    client = TestClient(app_module.app)
    login(client)

    class FakeReloadResponse:
        status_code = 503
        text = '{"error":"reload_not_configured"}'

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None):
            return FakeReloadResponse()

    monkeypatch.setattr(app_module, "ORCHESTRATOR_RELOAD_URL", "http://orchestrator-api:8004/v1/internal/reload")
    monkeypatch.setattr(app_module.httpx, "AsyncClient", FakeAsyncClient)

    response = client.put(
        "/v1/management/providers",
        json=[
            {
                "name": "openai",
                "type": "openai_compat",
                "base_url": "https://api.openai.com/v1",
                "require_api_key": True,
                "default_model": "gpt-test",
                "models": ["gpt-test"],
                "capabilities": {"tools": True, "json_schema": True, "streaming": True, "chunking": True},
            }
        ],
    )

    assert response.status_code == 502
    assert "orchestrator reload failed" in response.json()["detail"]["error"]


def test_runtime_defaults_seed_providers_policies_and_mcp(app_module):
    client = TestClient(app_module.app)
    login(client)

    providers = client.get("/v1/management/providers")
    policies = client.get("/v1/management/policies")
    mcp_settings = client.get("/v1/management/mcp-settings")

    assert providers.status_code == 200
    assert policies.status_code == 200
    assert mcp_settings.status_code == 200

    provider_names = {item["name"] for item in providers.json()}
    assert {"openai", "deepseek"}.issubset(provider_names)
    assert next(item for item in providers.json() if item["name"] == "openai")["client_controls"]["max_tokens"] is True
    assert policies.json()[0]["pipeline_id"] == "default"
    assert mcp_settings.json()["selected_servers"] == ["grafana", "viz"]


def test_policy_can_disable_corpus_scope(app_module):
    client = TestClient(app_module.app)
    login(client)

    payload = [
        {
            "pipeline_id": "writer",
            "default_corpus_id": None,
            "allowed_corpus_ids": [],
            "default_filters": {},
            "allowed_tools": [],
            "allowed_providers": ["openai", "deepseek"],
            "allowed_models": None,
            "max_input_tokens": None,
            "max_output_tokens": None,
            "max_total_tokens": None,
            "max_top_k": None,
            "default_provider": None,
            "default_model": None,
            "chunking": {"enabled": False},
        }
    ]

    response = client.put("/v1/management/policies", json=payload)

    assert response.status_code == 200
    saved = response.json()
    assert saved[0]["default_corpus_id"] is None
    assert saved[0]["allowed_corpus_ids"] == []

    snapshot = json.loads((Path(app_module.RUNTIME_DIR) / "policies.json").read_text(encoding="utf-8"))
    assert snapshot["writer"]["default_corpus_id"] is None
    assert snapshot["writer"]["allowed_corpus_ids"] == []


def test_bootstrap_files_sync_into_admin_store_on_startup(tmp_path, monkeypatch):
    db_path = tmp_path / "config_auth.db"
    runtime_dir = tmp_path / "runtime"
    providers_path = tmp_path / "bootstrap-providers.json"
    policies_path = tmp_path / "bootstrap-policies.json"
    processors_path = tmp_path / "bootstrap-processors.json"
    retrieval_profiles_path = tmp_path / "bootstrap-retrieval-profiles.json"

    def load_module(*, provider_name: str, pipeline_id: str):
        processor_id = f"{provider_name}.processor"
        retrieval_profile_id = f"{provider_name}.retrieval"
        providers_path.write_text(
            json.dumps(
                [
                    {
                        "name": provider_name,
                        "type": "openai_compat",
                        "base_url": "https://api.example.com/v1",
                        "require_api_key": False,
                        "default_model": "example-model",
                        "models": ["example-model"],
                        "capabilities": {
                            "tools": False,
                            "json_schema": False,
                            "streaming": True,
                            "max_context_window": 4096,
                            "default_context_window": 2048,
                        },
                        "client_controls": {"temperature": True, "max_tokens": True, "context_length": False},
                    }
                ]
            ),
            encoding="utf-8",
        )
        policies_path.write_text(
            json.dumps(
                {
                    pipeline_id: {
                        "default_corpus_id": "kb_public",
                        "allowed_corpus_ids": ["kb_public"],
                        "default_filters": {},
                        "allowed_tools": ["__none__"],
                        "allowed_providers": [provider_name],
                        "default_provider": provider_name,
                        "default_model": "example-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        processors_path.write_text(
            json.dumps(
                {
                    processor_id: {
                        "type": "generic",
                        "config": {"provider": provider_name},
                    }
                }
            ),
            encoding="utf-8",
        )
        retrieval_profiles_path.write_text(
            json.dumps(
                {
                    retrieval_profile_id: {
                        "config": {
                            "default_filters": {"provider": provider_name},
                            "filterable_fields": ["provider"],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setenv("CONFIG_AUTH_DB_PATH", str(db_path))
        monkeypatch.setenv("CONFIG_AUTH_RUNTIME_DIR", str(runtime_dir))
        monkeypatch.setenv("CONFIG_AUTH_DEV_BOOTSTRAP_ADMIN", "true")
        monkeypatch.setenv("CONFIG_AUTH_BOOTSTRAP_PROVIDERS_PATH", str(providers_path))
        monkeypatch.setenv("CONFIG_AUTH_BOOTSTRAP_POLICIES_PATH", str(policies_path))
        monkeypatch.setenv("CONFIG_AUTH_BOOTSTRAP_PROCESSORS_PATH", str(processors_path))
        monkeypatch.setenv("CONFIG_AUTH_BOOTSTRAP_RETRIEVAL_PROFILES_PATH", str(retrieval_profiles_path))
        monkeypatch.setenv("ORCHESTRATOR_RELOAD_URL", "")
        monkeypatch.setenv("DEFAULT_CORPUS_ID", "kb_default")
        monkeypatch.setenv("RETRIEVAL_API_URL", "http://retrieval-api:8000")

        sys.path.insert(0, str(PACKAGES_ROOT))
        sys.path.insert(0, str(COMMON_ROOT))
        try:
            for name in [
                "config_auth",
                "config_auth.app",
                "config_auth.app.main",
                "config_auth.app.db",
                "config_auth.app.models",
                "config_auth.app.security",
            ]:
                sys.modules.pop(name, None)
            module = importlib.import_module("config_auth.app.main")
            module.repo.ensure_schema()
            module.repo.bootstrap_admin_if_needed(True)
            module.repo.import_or_seed_runtime_defaults(corpora=["kb_public"])
            module.repo.export_runtime_snapshots()
            return module
        finally:
            for name in [
                "config_auth",
                "config_auth.app",
                "config_auth.app.main",
                "config_auth.app.db",
                "config_auth.app.models",
                "config_auth.app.security",
            ]:
                sys.modules.pop(name, None)
            if str(PACKAGES_ROOT) in sys.path:
                sys.path.remove(str(PACKAGES_ROOT))
            if str(COMMON_ROOT) in sys.path:
                sys.path.remove(str(COMMON_ROOT))

    first = load_module(provider_name="prod-tools", pipeline_id="default")
    assert [provider.name for provider in first.repo.list_providers()] == ["prod-tools"]
    assert [policy.pipeline_id for policy in first.repo.list_policies()] == ["default"]
    assert [processor.processor_id for processor in first.repo.list_processors()] == ["prod-tools.processor"]
    assert [profile.retrieval_profile_id for profile in first.repo.list_retrieval_profiles()] == [
        "prod-tools.retrieval"
    ]

    second = load_module(provider_name="test-tools", pipeline_id="openwebui_no_tools")
    assert [provider.name for provider in second.repo.list_providers()] == ["test-tools"]
    assert [policy.pipeline_id for policy in second.repo.list_policies()] == ["openwebui_no_tools"]
    assert [processor.processor_id for processor in second.repo.list_processors()] == ["test-tools.processor"]
    assert [profile.retrieval_profile_id for profile in second.repo.list_retrieval_profiles()] == [
        "test-tools.retrieval"
    ]


def test_rag_settings_support_selected_corpora(app_module):
    client = TestClient(app_module.app)
    login(client)

    response = client.put(
        "/v1/management/rag-settings",
        json={
            "default_corpus_id": "kb_archive",
            "selected_corpus_ids": ["kb_archive", "kb_public"],
            "default_top_k": 10,
            "retrieval_api_url": "http://retrieval-api:8000",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["default_corpus_id"] == "kb_archive"
    assert payload["selected_corpus_ids"] == ["kb_archive", "kb_public"]

    snapshot = json.loads((Path(app_module.RUNTIME_DIR) / "rag_settings.json").read_text(encoding="utf-8"))
    assert snapshot["selected_corpus_ids"] == ["kb_archive", "kb_public"]


def test_mcp_settings_export_selected_servers_only(app_module):
    client = TestClient(app_module.app)
    login(client)

    response = client.put(
        "/v1/management/mcp-settings",
        json={
            "selected_servers": ["viz"],
            "servers": [
                {"name": "grafana", "transport": "streamable_http", "url": "http://grafana:9000/mcp", "headers": {}},
                {"name": "viz", "transport": "streamable_http", "url": "http://viz:8101/mcp", "headers": {}},
            ],
            "timeout_s": 30,
            "strict": True,
            "max_tool_rounds": 12,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_servers"] == ["viz"]

    servers_snapshot = json.loads((Path(app_module.RUNTIME_DIR) / "mcp_servers.json").read_text(encoding="utf-8"))
    settings_snapshot = json.loads((Path(app_module.RUNTIME_DIR) / "mcp_settings.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in servers_snapshot] == ["viz"]
    assert settings_snapshot["strict"] is True
    assert settings_snapshot["max_tool_rounds"] == 12


def test_corpus_detail_and_add_url_resource(app_module):
    client = TestClient(app_module.app)
    login(client)

    client.post(
        "/v1/management/corpora",
        json={
            "corpus_id": "docs_public",
            "title": "Public Docs",
            "environment": "prod",
            "tenant_id": "tenant-a",
            "chunking": {"target_chars": 2200},
            "index": {"vector_distance": "cosine"},
            "metadata": {"owner": "docs"},
        },
    )

    detail_before = client.get("/v1/management/corpora/docs_public")
    assert detail_before.status_code == 200
    assert detail_before.json()["source_count"] == 0

    response = client.post(
        "/v1/management/corpora/docs_public/sources",
        json={
            "source_id": "remote_doc",
            "type": "url",
            "title": "Remote Doc",
            "url": "https://example.com/doc.html",
            "format": "html",
            "tags": ["remote"],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "remote_doc"
    assert payload["type"] == "url"

    detail_after = client.get("/v1/management/corpora/docs_public")
    assert detail_after.status_code == 200
    assert detail_after.json()["source_count"] == 1


def test_deleted_corpus_id_can_be_recreated(app_module):
    client = TestClient(app_module.app)
    login(client)

    create_response = client.post(
        "/v1/management/corpora",
        json={"corpus_id": "Research", "title": "Research"},
    )
    assert create_response.status_code == 200
    source_response = client.post(
        "/v1/management/corpora/Research/sources",
        json={
            "source_id": "DOC",
            "type": "url",
            "title": "Document",
            "url": "https://example.com/doc.html",
            "format": "html",
        },
    )
    assert source_response.status_code == 200

    delete_response = client.delete("/v1/management/corpora/Research")
    assert delete_response.status_code == 200
    assert client.get("/v1/management/corpora/Research").status_code == 404

    recreate_response = client.post(
        "/v1/management/corpora",
        json={"corpus_id": "Research", "title": "Research v2"},
    )
    assert recreate_response.status_code == 200
    detail = client.get("/v1/management/corpora/Research")
    assert detail.status_code == 200
    assert detail.json()["title"] == "Research v2"
    assert detail.json()["source_count"] == 0


def test_legacy_soft_deleted_corpus_id_can_be_recreated(app_module):
    client = TestClient(app_module.app)
    login(client)

    with sqlite3.connect(app_module.DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO corpora(
                corpus_id, title, chunking_json, index_json, metadata_json,
                created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Research",
                "Old Research",
                "{}",
                "{}",
                "{}",
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:01",
            ),
        )

    recreate_response = client.post(
        "/v1/management/corpora",
        json={"corpus_id": "Research", "title": "Research"},
    )

    assert recreate_response.status_code == 200
    detail = client.get("/v1/management/corpora/Research")
    assert detail.status_code == 200
    assert detail.json()["title"] == "Research"


def test_deleted_url_source_id_can_be_recreated(app_module):
    client = TestClient(app_module.app)
    login(client)

    client.post("/v1/management/corpora", json={"corpus_id": "research", "title": "Research"})
    create_response = client.post(
        "/v1/management/corpora/research/sources",
        json={
            "source_id": "DOC",
            "type": "url",
            "title": "Old Document",
            "url": "https://example.com/old.html",
            "format": "html",
        },
    )
    assert create_response.status_code == 200

    delete_response = client.delete("/v1/management/corpora/research/sources/DOC")
    assert delete_response.status_code == 200

    recreate_response = client.post(
        "/v1/management/corpora/research/sources",
        json={
            "source_id": "DOC",
            "type": "url",
            "title": "Document",
            "url": "https://example.com/new.html",
            "format": "html",
        },
    )
    assert recreate_response.status_code == 200
    payload = recreate_response.json()
    assert payload["id"] == "DOC"
    assert payload["url"] == "https://example.com/new.html"
    assert payload["deleted_at"] is None


def test_delete_source_with_purge_calls_ingestion_worker(app_module, monkeypatch):
    client = TestClient(app_module.app)
    login(client)
    calls = []

    class FakeResponse:
        status_code = 200
        text = "{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "purged",
                "corpus_id": "research",
                "source_id": "DOC",
                "deleted_chunks": 3,
                "deleted_qdrant_points": 3,
            }

    class FakeClient:
        def __init__(self, *, timeout):
            assert timeout == app_module.INGESTION_WORKER_TIMEOUT_S

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, json, headers):
            calls.append((url, json, headers))
            return FakeResponse()

    monkeypatch.setattr(app_module, "INGESTION_WORKER_URL", "http://ingestion-worker.test")
    monkeypatch.setattr(app_module.httpx, "Client", FakeClient)

    client.post("/v1/management/corpora", json={"corpus_id": "research", "title": "Research"})
    client.post(
        "/v1/management/corpora/research/sources",
        json={
            "source_id": "DOC",
            "type": "url",
            "title": "Document",
            "url": "https://example.com/doc.html",
            "format": "html",
        },
    )

    delete_response = client.delete("/v1/management/corpora/research/sources/DOC?purge=true")

    assert delete_response.status_code == 200
    assert delete_response.json() == {
        "status": "ok",
        "purge": {
            "status": "purged",
            "corpus_id": "research",
            "source_id": "DOC",
            "deleted_chunks": 3,
            "deleted_qdrant_points": 3,
        },
    }
    assert calls == [
        (
            "http://ingestion-worker.test/v1/purge/source",
            {"corpus_id": "research", "source_id": "DOC"},
            {"Authorization": "Bearer test-internal-token"},
        )
    ]
    assert client.get("/v1/management/corpora/research").json()["source_count"] == 0


def test_delete_source_with_purge_failure_keeps_source(app_module, monkeypatch):
    client = TestClient(app_module.app)
    login(client)

    class FakeResponse:
        status_code = 500
        text = "worker failed"

        def raise_for_status(self):
            response = SimpleNamespace(status_code=self.status_code, text=self.text)
            raise app_module.httpx.HTTPStatusError("worker failed", request=None, response=response)

    class FakeClient:
        def __init__(self, *, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, json, headers):
            return FakeResponse()

    monkeypatch.setattr(app_module, "INGESTION_WORKER_URL", "http://ingestion-worker.test")
    monkeypatch.setattr(app_module.httpx, "Client", FakeClient)

    client.post("/v1/management/corpora", json={"corpus_id": "research", "title": "Research"})
    client.post(
        "/v1/management/corpora/research/sources",
        json={
            "source_id": "DOC",
            "type": "url",
            "title": "Document",
            "url": "https://example.com/doc.html",
            "format": "html",
        },
    )

    delete_response = client.delete("/v1/management/corpora/research/sources/DOC?purge=true")

    assert delete_response.status_code == 502
    assert client.get("/v1/management/corpora/research").json()["source_count"] == 1


def test_corpus_file_upload_and_delete_resource(app_module):
    client = TestClient(app_module.app)
    login(client)
    storage = install_fake_object_storage(app_module)

    client.post(
        "/v1/management/corpora",
        json={
            "corpus_id": "docs_public",
            "title": "Public Docs",
            "environment": "prod",
            "tenant_id": "tenant-a",
            "chunking": {"target_chars": 2200},
            "index": {"vector_distance": "cosine"},
            "metadata": {"owner": "docs"},
        },
    )

    response = client.post(
        "/v1/management/corpora/docs_public/sources/upload",
        data={
            "source_id": "uploaded_html",
            "title": "Uploaded HTML",
            "format": "html",
            "tags_json": '["upload"]',
        },
        files={"upload": ("document.html", b"<html><body>" + b"a" * 128 + b"</body></html>", "text/html")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "uploaded_html"
    assert payload["type"] == "object"
    assert payload["object_uri"].startswith("s3://test-bucket/prod/tenant-a/docs_public/uploaded_html/")
    assert payload["object_uri"] in storage.objects

    delete_response = client.delete("/v1/management/corpora/docs_public/sources/uploaded_html")
    assert delete_response.status_code == 200

    detail = client.get("/v1/management/corpora/docs_public")
    assert detail.status_code == 200
    assert detail.json()["source_count"] == 0

    recreate_response = client.post(
        "/v1/management/corpora/docs_public/sources/upload",
        data={
            "source_id": "uploaded_html",
            "title": "Uploaded HTML Again",
            "format": "html",
            "tags_json": '["upload"]',
        },
        files={"upload": ("document-v2.html", b"<html><body>" + b"b" * 128 + b"</body></html>", "text/html")},
    )
    assert recreate_response.status_code == 200
    recreated = recreate_response.json()
    assert recreated["id"] == "uploaded_html"
    assert recreated["title"] == "Uploaded HTML Again"
    assert recreated["object_uri"] in storage.objects


def test_corpus_registry_export_import_round_trip(app_module):
    client = TestClient(app_module.app)
    login(client)
    storage = install_fake_object_storage(app_module)

    create_response = client.post(
        "/v1/management/corpora",
        json={
            "corpus_id": "portable_docs",
            "title": "Portable Docs",
            "environment": "prod",
            "tenant_id": "tenant-a",
            "chunking": {"target_chars": 1800, "strategy": "llm"},
            "index": {"vector_distance": "cosine"},
            "metadata": {"owner": "platform"},
        },
    )
    assert create_response.status_code == 200

    url_response = client.post(
        "/v1/management/corpora/portable_docs/sources",
        json={
            "source_id": "remote_page",
            "type": "url",
            "title": "Remote Page",
            "url": "https://example.com/page.html",
            "format": "html",
            "tags": ["remote"],
            "metadata": {"kind": "reference"},
        },
    )
    assert url_response.status_code == 200

    upload_response = client.post(
        "/v1/management/corpora/portable_docs/sources/upload",
        data={"source_id": "uploaded_pdf", "title": "Uploaded PDF", "format": "pdf"},
        files={"upload": ("manual.pdf", b"%PDF-" + b"x" * 128, "application/pdf")},
    )
    assert upload_response.status_code == 200
    uploaded = upload_response.json()
    assert uploaded["object_uri"] in storage.objects

    export_response = client.get("/v1/management/corpora/portable_docs/registry-export")
    assert export_response.status_code == 200
    bundle = export_response.json()
    assert bundle["schema_version"] == "config-auth.corpus-registry.v1"
    assert bundle["corpus"]["corpus_id"] == "portable_docs"
    assert len(bundle["corpus"]["sources"]) == 2

    delete_response = client.delete("/v1/management/corpora/portable_docs")
    assert delete_response.status_code == 200

    import_response = client.post(
        "/v1/management/corpora/registry-import",
        json={"bundle": bundle, "conflict_strategy": "fail"},
    )
    assert import_response.status_code == 200
    assert import_response.json()["sources_imported"] == 2

    detail_response = client.get("/v1/management/corpora/portable_docs")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["title"] == "Portable Docs"
    assert detail["chunking"]["target_chars"] == 1800
    sources = {source["id"]: source for source in detail["sources"]}
    assert sources["remote_page"]["url"] == "https://example.com/page.html"
    assert sources["remote_page"]["metadata"] == {"kind": "reference"}
    assert sources["uploaded_pdf"]["object_uri"] == uploaded["object_uri"]
    assert sources["uploaded_pdf"]["content_hash"] == uploaded["content_hash"]

    conflict_response = client.post(
        "/v1/management/corpora/registry-import",
        json={"bundle": bundle, "conflict_strategy": "fail"},
    )
    assert conflict_response.status_code == 400

    replace_bundle = json.loads(json.dumps(bundle))
    replace_bundle["corpus"]["title"] = "Portable Docs Replacement"
    replace_response = client.post(
        "/v1/management/corpora/registry-import",
        json={"bundle": replace_bundle, "conflict_strategy": "replace"},
    )
    assert replace_response.status_code == 200
    replaced = client.get("/v1/management/corpora/portable_docs").json()
    assert replaced["title"] == "Portable Docs Replacement"
