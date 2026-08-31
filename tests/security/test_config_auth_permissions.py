from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import evidence_marker


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _seed_role_users(module) -> None:
    from config_auth.app.models import UserWrite

    module.repo.replace_users(
        [
            UserWrite(username="admin", roles=["admin"], is_active=True, must_rotate_password=True),
            UserWrite(username="viewer", password="viewer-password", roles=["viewer"], is_active=True),
            UserWrite(username="operator", password="operator-password", roles=["operator"], is_active=True),
        ],
        actor="security-test",
    )


@evidence_marker(
    "AUTHN-CONFIG-001",
    "Management read endpoint without session or API key is rejected",
    "config_auth",
    "401 unauthorized",
    control_ids=["CTRL-IAM-001"],
    risk_ids=["CONFIG-S-01"],
    cra_requirements=["Annex I Part I 1"],
)
def test_management_endpoint_requires_authentication(config_auth_module):
    client = TestClient(config_auth_module.app)

    response = client.get("/v1/management/providers")

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "unauthorized"


@evidence_marker(
    "AUTHN-CONFIG-002",
    "Invalid bearer API key on management endpoint is rejected",
    "config_auth",
    "401 unauthorized",
    control_ids=["CTRL-IAM-001"],
    risk_ids=["CONFIG-S-01"],
    cra_requirements=["Annex I Part I 1"],
)
def test_invalid_management_bearer_is_rejected(config_auth_module):
    client = TestClient(config_auth_module.app)

    response = client.get("/v1/management/providers", headers={"authorization": "Bearer wrong-token"})

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "unauthorized"


@evidence_marker(
    "AUTHZ-CONFIG-001",
    "Viewer cannot update providers",
    "config_auth",
    "403 forbidden",
    control_ids=["CTRL-IAM-002"],
    risk_ids=["CONFIG-E-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_viewer_cannot_update_providers(config_auth_module):
    _seed_role_users(config_auth_module)
    client = TestClient(config_auth_module.app)
    _login(client, "viewer", "viewer-password")

    response = client.put("/v1/management/providers", json=[])

    assert response.status_code == 403
    assert "providers:write" in response.json()["detail"]["error"]


@evidence_marker(
    "AUTHZ-CONFIG-002",
    "Viewer cannot update policies",
    "config_auth",
    "403 forbidden",
    control_ids=["CTRL-IAM-002"],
    risk_ids=["CONFIG-E-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_viewer_cannot_update_policies(config_auth_module):
    _seed_role_users(config_auth_module)
    client = TestClient(config_auth_module.app)
    _login(client, "viewer", "viewer-password")

    response = client.put("/v1/management/policies", json=[])

    assert response.status_code == 403
    assert "policies:write" in response.json()["detail"]["error"]


@evidence_marker(
    "AUTHZ-CONFIG-003",
    "Viewer cannot create API keys",
    "config_auth",
    "403 forbidden",
    control_ids=["CTRL-IAM-002"],
    risk_ids=["CONFIG-E-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_viewer_cannot_create_api_keys(config_auth_module):
    _seed_role_users(config_auth_module)
    client = TestClient(config_auth_module.app)
    _login(client, "viewer", "viewer-password")

    response = client.post("/v1/management/api-keys", json={"subject": "svc", "scopes": ["status:read"]})

    assert response.status_code == 403
    assert "keys:write" in response.json()["detail"]["error"]


@evidence_marker(
    "AUTHZ-CONFIG-004",
    "Operator can write providers but cannot write users",
    "config_auth",
    "200 provider write and 403 user write",
    control_ids=["CTRL-IAM-002"],
    risk_ids=["CONFIG-E-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_operator_write_permissions_match_role_model(config_auth_module):
    _seed_role_users(config_auth_module)
    client = TestClient(config_auth_module.app)
    _login(client, "operator", "operator-password")

    providers_response = client.put(
        "/v1/management/providers",
        json=[
            {
                "name": "mock",
                "type": "openai_compat",
                "base_url": "http://mock-provider.test/v1",
                "require_api_key": False,
                "default_model": "mock-chat",
                "models": ["mock-chat"],
                "capabilities": {"tools": False, "json_schema": True, "streaming": False},
            }
        ],
    )
    users_response = client.put("/v1/management/users", json=[])

    assert providers_response.status_code == 200
    assert users_response.status_code == 403
    assert "users:write" in users_response.json()["detail"]["error"]


@evidence_marker(
    "AUTHZ-CONFIG-007",
    "Service API key with status:read cannot call management write endpoint",
    "config_auth",
    "403 forbidden",
    control_ids=["CTRL-IAM-002"],
    risk_ids=["CONFIG-E-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_service_api_key_with_read_scope_cannot_write(config_auth_module):
    client = TestClient(config_auth_module.app)
    _login(client, "admin", "admin")
    create_response = client.post(
        "/v1/management/api-keys",
        json={"subject": "status-reader", "scopes": ["status:read"]},
    )
    assert create_response.status_code == 200
    token = create_response.json()["plaintext_key"]

    response = client.put(
        "/v1/management/providers",
        json=[],
        headers={"authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert "providers:write" in response.json()["detail"]["error"]


@evidence_marker(
    "SNAPSHOT-001",
    "Management write exports updated runtime provider snapshot",
    "config_auth",
    "runtime providers snapshot updated",
    control_ids=["CTRL-CFG-001"],
    risk_ids=["CONFIG-T-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_provider_management_write_exports_runtime_snapshot(config_auth_module):
    client = TestClient(config_auth_module.app)
    _login(client, "admin", "admin")

    response = client.put(
        "/v1/management/providers",
        json=[
            {
                "name": "mock",
                "type": "openai_compat",
                "base_url": "http://mock-provider.test/v1",
                "require_api_key": False,
                "default_model": "mock-chat",
                "models": ["mock-chat"],
                "capabilities": {"tools": False, "json_schema": True, "streaming": False},
            }
        ],
    )

    assert response.status_code == 200
    snapshot = json.loads((Path(config_auth_module.RUNTIME_DIR) / "providers.json").read_text(encoding="utf-8"))
    assert snapshot == [
        {
            "name": "mock",
            "type": "openai_compat",
            "base_url": "http://mock-provider.test/v1",
            "require_api_key": False,
            "default_model": "mock-chat",
            "models": ["mock-chat"],
            "capabilities": {
                "tools": False,
                "json_schema": True,
                "streaming": False,
                "chunking": False,
                "max_context_window": 8192,
                "default_context_window": 8192,
            },
            "client_controls": {
                "temperature": True,
                "max_tokens": True,
                "context_length": False,
            },
            "secret_ref": None,
            "secret_source_type": None,
        }
    ]
