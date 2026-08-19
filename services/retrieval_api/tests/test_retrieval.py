import sys
from pathlib import Path

SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICES_ROOT / "common") not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT / "common"))
if str(SERVICES_ROOT / "retrieval_api") not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT / "retrieval_api"))

from fastapi.testclient import TestClient


def test_corpora_listing_uses_registry(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.test:6333")
    monkeypatch.setenv("EMBEDDER_URL", "http://embedder.test:80")

    # Mock the registry client
    def fake_list_corpora():
        return ["corpus_a", "corpus_b"]

    monkeypatch.setattr("app.main.list_corpora", fake_list_corpora)

    from app.main import app

    client = TestClient(app)

    response = client.get("/corpora")
    assert response.status_code == 200
    assert response.json() == {"corpora": ["corpus_a", "corpus_b"]}


def test_lookup_endpoint_returns_query_response(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.test:6333")
    monkeypatch.setenv("EMBEDDER_URL", "http://embedder.test:80")

    async def fake_lookup(req):
        assert req.terms == ["/v1/query", "IngestionJob"]
        assert req.corpus_id == "docs"
        assert req.top_k == 5
        return (
            {
                "api_version": "v1",
                "answer": "ok",
                "citations": [],
                "chunks": [],
            },
            {"lexical_hits": 2, "field_hits": 1, "exact_hits": 0, "returned_chunks": 0},
        )

    from app import main

    monkeypatch.setattr(main, "lexical_lookup_with_metrics", fake_lookup)
    client = TestClient(main.app)

    response = client.post(
        "/v1/lookup",
        json={"terms": ["/v1/query", "IngestionJob"], "corpus_id": "docs", "top_k": 5},
    )

    assert response.status_code == 200
    assert response.json()["api_version"] == "v1"


def test_scoped_index_naming():
    from common.index_naming import qdrant_collection_name, lexical_index_path

    col_name = qdrant_collection_name("env1", "tenant1", "my_corpus")
    assert col_name == "corp_env1_tenant1_my_corpus"

    lex_path = lexical_index_path("env1", "tenant1", "my_corpus", "/data")
    assert lex_path.replace("\\", "/") == "/data/lexical/corp_env1_tenant1_my_corpus.db"
