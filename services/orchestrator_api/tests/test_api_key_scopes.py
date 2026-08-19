import hashlib
import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import AuthRegistry


@pytest.fixture
def mock_auth_registry(monkeypatch):
    payload = [
        {
            "key_id": "test-key",
            "key_hash": hashlib.sha256(b"test-secret").hexdigest(),
            "subject": "test-user",
            "scopes": ["models:list", "chat:invoke"],
            "allowed_providers": ["openai"],
            "max_output_tokens": 100,
        },
        {
            "key_id": "admin-key",
            "key_hash": hashlib.sha256(b"admin-secret").hexdigest(),
            "subject": "admin",
            "scopes": ["admin:*"],
        },
    ]
    monkeypatch.setenv("ORCHESTRATOR_API_KEYS_JSON", json.dumps(payload))
    monkeypatch.delenv("ORCHESTRATOR_API_KEYS_PATH", raising=False)

    registry = AuthRegistry.load()
    monkeypatch.setattr("app.main.auth_registry", registry)
    return registry


def test_auth_context_enforces_scopes(mock_auth_registry):
    client = TestClient(app)

    # test-key has models:list and chat:invoke, but not rag:query
    response = client.get("/v1/models", headers={"Authorization": "Bearer test-secret"})
    assert response.status_code == 200

    response = client.post("/v1/rag/query", json={"query": "test"}, headers={"Authorization": "Bearer test-secret"})
    assert response.status_code == 403
    assert "rag:query" in response.json()["error"]

    # admin-key has admin:*, so it should be able to access everything
    response = client.get("/v1/models", headers={"Authorization": "Bearer admin-secret"})
    assert response.status_code == 200

    # Missing auth
    response = client.get("/v1/models")
    assert response.status_code == 401

    # Invalid auth
    response = client.get("/v1/models", headers={"Authorization": "Bearer invalid-secret"})
    assert response.status_code == 401


def test_models_endpoint_filters_by_allowed_providers(mock_auth_registry):
    client = TestClient(app)

    response = client.get("/v1/models", headers={"Authorization": "Bearer test-secret"})
    assert response.status_code == 200
    data = response.json()

    for model in data["data"]:
        assert model["owned_by"] == "openai"
