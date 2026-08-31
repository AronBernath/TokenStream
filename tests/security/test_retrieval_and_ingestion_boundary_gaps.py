from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import evidence_marker


@pytest.mark.xfail(reason="Retrieval API currently has no direct caller authentication")
@evidence_marker(
    "AUTHN-RETR-001",
    "Direct retrieval query requires service authentication",
    "retrieval-api",
    "401 or 403",
    control_ids=["CTRL-IAM-001"],
    risk_ids=["RETR-S-01"],
    cra_requirements=["Annex I Part I 1"],
    notes="Known gap: direct retrieval query is currently unauthenticated.",
)
def test_direct_retrieval_query_requires_internal_auth(retrieval_main, monkeypatch):
    async def fake_query(req):
        return (
            {"api_version": "v1", "answer": "ok", "citations": [], "chunks": []},
            {"returned_chunks": 0, "vector_hits": 0, "lexical_hits": 0},
        )

    monkeypatch.setattr(retrieval_main, "hybrid_query_with_metrics", fake_query)
    client = TestClient(retrieval_main.app)

    response = client.post("/v1/query", json={"query": "secret", "corpus_id": "docs", "top_k": 1})

    assert response.status_code in {401, 403}


@pytest.mark.xfail(reason="Retrieval corpus listing is currently unauthenticated")
@evidence_marker(
    "AUTHN-RETR-002",
    "Direct retrieval corpora listing requires service authentication",
    "retrieval-api",
    "401 or 403",
    control_ids=["CTRL-IAM-001"],
    risk_ids=["RETR-S-01"],
    cra_requirements=["Annex I Part I 1"],
    notes="Known gap: /corpora is public if retrieval-api is exposed.",
)
def test_direct_retrieval_corpora_requires_internal_auth(retrieval_main, monkeypatch):
    monkeypatch.setattr(retrieval_main, "list_corpora", lambda: ["docs"])
    client = TestClient(retrieval_main.app)

    response = client.get("/corpora")

    assert response.status_code in {401, 403}


@pytest.mark.xfail(reason="Dry-run chunking endpoint currently does not require internal auth")
@evidence_marker(
    "AUTHN-INGEST-003",
    "Direct unauthenticated ingestion dry-run is rejected",
    "ingestion-worker",
    "401 or 403",
    control_ids=["CTRL-IAM-001"],
    risk_ids=["INGEST-S-01"],
    cra_requirements=["Annex I Part I 1"],
    notes="Known gap: dry-run chunking endpoint is unauthenticated.",
)
def test_ingestion_dry_run_requires_internal_auth(ingestion_server, monkeypatch):
    monkeypatch.setattr(
        ingestion_server,
        "dry_run_chunking",
        lambda **kwargs: {"corpus_id": kwargs["corpus_id"], "preview_chunks": []},
    )
    client = TestClient(ingestion_server.app)

    response = client.post("/v1/dry-run/chunking", json={"corpus_id": "docs", "max_preview_chunks": 1})

    assert response.status_code in {401, 403}
