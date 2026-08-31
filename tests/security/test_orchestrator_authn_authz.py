from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from fastapi.testclient import TestClient

from conftest import evidence_marker


def _configure_api_keys(main, monkeypatch, specs: dict[str, list[str]]) -> None:
    from app.auth import AuthRegistry

    payload = [
        {
            "key_id": name,
            "key_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "subject": name,
            "scopes": scopes,
        }
        for token, (name, scopes) in {f"{name}-token": (name, scopes) for name, scopes in specs.items()}.items()
    ]
    monkeypatch.setenv("ORCHESTRATOR_API_KEYS_JSON", json.dumps(payload))
    monkeypatch.delenv("ORCHESTRATOR_API_KEYS_PATH", raising=False)
    monkeypatch.setattr(main, "settings", replace(main.settings, service_api_key=None))
    monkeypatch.setattr(main, "auth_registry", AuthRegistry.load())


@evidence_marker(
    "AUTHN-ORCH-001",
    "Missing Authorization header on models endpoint is rejected",
    "orchestrator-api",
    "401 unauthorized",
    control_ids=["CTRL-IAM-001"],
    risk_ids=["ORCH-S-01"],
    cra_requirements=["Annex I Part I 1"],
)
def test_missing_authorization_on_models_is_rejected(orchestrator_main):
    client = TestClient(orchestrator_main.app)

    response = client.get("/v1/models")

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


@evidence_marker(
    "AUTHN-ORCH-002",
    "Invalid bearer token on models endpoint is rejected",
    "orchestrator-api",
    "401 unauthorized",
    control_ids=["CTRL-IAM-001"],
    risk_ids=["ORCH-S-01"],
    cra_requirements=["Annex I Part I 1"],
)
def test_invalid_bearer_on_models_is_rejected(orchestrator_main):
    client = TestClient(orchestrator_main.app)

    response = client.get("/v1/models", headers={"authorization": "Bearer wrong-token"})

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


@evidence_marker(
    "AUTHN-ORCH-003",
    "Auth registry and legacy key both absent fail closed",
    "orchestrator-api",
    "503 auth_not_configured",
    control_ids=["CTRL-IAM-001"],
    risk_ids=["ORCH-S-01"],
    cra_requirements=["Annex I Part I 1"],
)
def test_absent_auth_registry_and_legacy_key_fail_closed(orchestrator_main):
    from app.auth import AuthRegistry

    orchestrator_main.settings = replace(orchestrator_main.settings, service_api_key=None)
    orchestrator_main.auth_registry = AuthRegistry(entries=[], legacy_key=None)
    client = TestClient(orchestrator_main.app)

    response = client.get("/v1/models", headers={"authorization": "Bearer any-token"})

    assert response.status_code == 503
    assert response.json()["error"] == "auth_not_configured"


@evidence_marker(
    "AUTHN-ORCH-004",
    "Legacy key authenticates when explicitly configured",
    "orchestrator-api",
    "200 on legacy allowed scope",
    control_ids=["CTRL-IAM-001"],
    risk_ids=["ORCH-S-LEGACY"],
    cra_requirements=["Annex I Part I 1"],
    notes="Legacy shared-key support is preserved as explicit risk evidence.",
)
def test_legacy_key_authenticates_when_configured(orchestrator_main):
    client = TestClient(orchestrator_main.app)

    response = client.get("/v1/models", headers={"authorization": "Bearer legacy-test-token"})

    assert response.status_code == 200


@evidence_marker(
    "AUTHZ-ORCH-001",
    "Machine key without rag:query cannot call RAG query endpoint",
    "orchestrator-api",
    "403 forbidden",
    control_ids=["CTRL-IAM-001", "CTRL-IAM-002"],
    risk_ids=["ORCH-E-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_key_without_rag_scope_cannot_call_rag_query(orchestrator_main, monkeypatch):
    _configure_api_keys(orchestrator_main, monkeypatch, {"client_chat_only": ["chat:invoke"]})
    client = TestClient(orchestrator_main.app)

    response = client.post(
        "/v1/rag/query",
        json={"query": "policy test", "corpus_id": "docs"},
        headers={"authorization": "Bearer client_chat_only-token"},
    )

    assert response.status_code == 403
    assert "rag:query" in response.json()["error"]


@evidence_marker(
    "AUTHZ-ORCH-002",
    "Machine key without models:list cannot call models endpoint",
    "orchestrator-api",
    "403 forbidden",
    control_ids=["CTRL-IAM-001", "CTRL-IAM-002"],
    risk_ids=["ORCH-E-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_key_without_models_scope_cannot_call_models(orchestrator_main, monkeypatch):
    _configure_api_keys(orchestrator_main, monkeypatch, {"client_chat_only": ["chat:invoke"]})
    client = TestClient(orchestrator_main.app)

    response = client.get("/v1/models", headers={"authorization": "Bearer client_chat_only-token"})

    assert response.status_code == 403
    assert "models:list" in response.json()["error"]


@evidence_marker(
    "AUTHZ-ORCH-003",
    "Machine key without chat:invoke cannot call chat completions",
    "orchestrator-api",
    "403 forbidden",
    control_ids=["CTRL-IAM-001", "CTRL-IAM-002"],
    risk_ids=["ORCH-E-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_key_without_chat_scope_cannot_call_chat(orchestrator_main, monkeypatch):
    _configure_api_keys(orchestrator_main, monkeypatch, {"client_models_only": ["models:list"]})
    client = TestClient(orchestrator_main.app)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "openai:gpt-5.1", "messages": [{"role": "user", "content": "hello"}]},
        headers={"authorization": "Bearer client_models_only-token"},
    )

    assert response.status_code == 403
    assert "chat:invoke" in response.json()["error"]
