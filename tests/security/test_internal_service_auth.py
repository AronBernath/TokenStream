from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import evidence_marker


@evidence_marker(
    "AUTHN-INTERNAL-001",
    "Missing internal token on config-auth internal corpora endpoint is rejected",
    "config_auth",
    "401 unauthorized",
    control_ids=["CTRL-IAM-001"],
    risk_ids=["CONFIG-S-01"],
    cra_requirements=["Annex I Part I 1"],
)
def test_config_auth_internal_endpoint_requires_token(config_auth_module):
    client = TestClient(config_auth_module.app)

    response = client.get("/internal/corpora")

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "missing authorization header"


@evidence_marker(
    "AUTHN-INTERNAL-002",
    "Invalid internal token on config-auth internal corpora endpoint is rejected",
    "config_auth",
    "403 forbidden",
    control_ids=["CTRL-IAM-001"],
    risk_ids=["CONFIG-S-01"],
    cra_requirements=["Annex I Part I 1"],
)
def test_config_auth_internal_endpoint_rejects_bad_token(config_auth_module):
    client = TestClient(config_auth_module.app)

    response = client.get("/internal/corpora", headers={"authorization": "Bearer wrong-token"})

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "invalid internal token"


@evidence_marker(
    "AUTHN-INGEST-001",
    "Missing internal token on ingestion source purge is rejected",
    "ingestion-worker",
    "401 unauthorized",
    control_ids=["CTRL-IAM-001"],
    risk_ids=["INGEST-S-01"],
    cra_requirements=["Annex I Part I 1"],
)
def test_ingestion_source_purge_requires_token(ingestion_server):
    client = TestClient(ingestion_server.app)

    response = client.post("/v1/purge/source", json={"corpus_id": "docs", "source_id": "src"})

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "missing bearer token"


@evidence_marker(
    "AUTHN-INGEST-002",
    "Invalid internal token on ingestion source purge is rejected",
    "ingestion-worker",
    "403 forbidden",
    control_ids=["CTRL-IAM-001"],
    risk_ids=["INGEST-S-01"],
    cra_requirements=["Annex I Part I 1"],
)
def test_ingestion_source_purge_rejects_bad_token(ingestion_server):
    client = TestClient(ingestion_server.app)

    response = client.post(
        "/v1/purge/source",
        json={"corpus_id": "docs", "source_id": "src"},
        headers={"authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "invalid bearer token"
