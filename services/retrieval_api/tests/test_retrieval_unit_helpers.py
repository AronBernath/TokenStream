import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest


SERVICES_ROOT = Path(__file__).resolve().parents[2]
RETRIEVAL_ROOT = SERVICES_ROOT / "retrieval_api"
COMMON_ROOT = SERVICES_ROOT / "common"
if str(RETRIEVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(RETRIEVAL_ROOT))
if str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

from app import registry_client, reranker
from app import sqlite_fts_client
from app.sqlite_fts_client import _build_filter_sql, _build_match_query, _rows_to_chunks, sqlite_lexical_lookup


def test_sqlite_match_query_tokenizes_user_input_for_fts():
    assert _build_match_query("tax-credit 2026!") == '"tax" AND "credit" AND "2026"'
    assert _build_match_query("?!") == '""'


def test_sqlite_filter_sql_handles_tags_columns_and_metadata_filters():
    where, params = _build_filter_sql(
        {
            "tags": ["risk", "policy"],
            "doc_type": ["act", "guide"],
            "language": "en",
            "custom_field": "custom-value",
            "empty_list": [],
            "ignored": None,
        }
    )

    assert where[0] == (
        "(EXISTS (SELECT 1 FROM json_each(c.tags_json) WHERE json_each.value = ?) "
        "OR EXISTS (SELECT 1 FROM json_each(c.tags_json) WHERE json_each.value = ?))"
    )
    assert "COALESCE(c.doc_type, json_extract(c.metadata_json, '$.doc_type')) IN (?,?)" in where
    assert "c.language = ?" in where
    assert "json_extract(c.metadata_json, ?) = ?" in where
    assert params == ["risk", "policy", "act", "guide", "en", "$.custom_field", "custom-value"]


def test_rows_to_chunks_is_tolerant_of_bad_json_metadata():
    rows = [
        (
            "chunk-1",
            1.25,
            "doc-1",
            "policy",
            "chunk text",
            "Title",
            "section-1",
            "https://example.test/doc",
            "2026-07-23",
            '["risk", 3]',
            '{"source": "registry"}',
        ),
        ("chunk-2", 0.5, None, None, "text", "", None, None, None, "not-json", "also-not-json"),
    ]

    chunks = _rows_to_chunks(rows)

    assert chunks[0]["tags"] == ["risk", "3"]
    assert chunks[0]["metadata"] == {"source": "registry"}
    assert chunks[1]["doc_id"] == ""
    assert chunks[1]["tags"] is None
    assert chunks[1]["metadata"] == {}


def test_sqlite_lexical_lookup_matches_paths_symbols_and_metadata(monkeypatch, tmp_path):
    db_path = tmp_path / "lexical.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT,
                doc_type TEXT,
                text TEXT NOT NULL,
                title TEXT,
                section_id TEXT,
                version_date TEXT,
                jurisdiction TEXT,
                language TEXT,
                source_url TEXT,
                tags_json TEXT,
                metadata_json TEXT NOT NULL
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO chunks (
                chunk_id, doc_id, doc_type, text, title, section_id, version_date,
                jurisdiction, language, source_url, tags_json, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            [
                (
                    "chunk-route",
                    "services/orchestrator_api/app/main.py",
                    "code",
                    "Defines POST /v1/chat/completions routing.",
                    "main.py",
                    "route",
                    None,
                    None,
                    "python",
                    "https://repo/main.py",
                    "[]",
                    json.dumps({"path": "services/orchestrator_api/app/main.py", "source_kind": "code"}),
                ),
                (
                    "chunk-model",
                    "packages/config_auth/app/models.py",
                    "code",
                    "class IngestionJob defines source processing state.",
                    "models.py",
                    "IngestionJob",
                    None,
                    None,
                    "python",
                    "https://repo/models.py",
                    "[]",
                    json.dumps({"symbol": "IngestionJob", "path": "packages/config_auth/app/models.py"}),
                ),
            ],
        )
    monkeypatch.setattr(sqlite_fts_client, "_sqlite_path", lambda corpus: str(db_path))

    route_hits = asyncio.run(sqlite_lexical_lookup({"corpus_id": "docs"}, "/v1/chat/completions", 5, {}))
    symbol_hits = asyncio.run(sqlite_lexical_lookup({"corpus_id": "docs"}, "IngestionJob", 5, {}))

    assert [hit["chunk_id"] for hit in route_hits] == ["chunk-route"]
    assert [hit["chunk_id"] for hit in symbol_hits] == ["chunk-model"]


def test_rerank_hits_sorts_by_reranker_score_then_original_score(monkeypatch):
    class FakeModel:
        def predict(self, pairs, *, batch_size, show_progress_bar):
            assert pairs == [("query", "low original"), ("query", "high original"), ("query", "weak")]
            assert batch_size == reranker.RERANKER_BATCH_SIZE
            assert show_progress_bar is False
            return [0.8, 0.8, 0.2]

    monkeypatch.setattr(reranker, "_load_model", lambda: FakeModel())
    monkeypatch.setattr(reranker, "RERANKER_MAX_CANDIDATES", 3)

    hits = [
        {"chunk_id": "a", "text": "low original", "score": 0.1},
        {"chunk_id": "b", "text": "high original", "score": 0.9},
        {"chunk_id": "c", "text": "weak", "score": 0.99},
    ]

    assert [hit["chunk_id"] for hit in reranker.rerank_hits("query", hits)] == ["b", "a", "c"]


def test_rerank_hits_returns_original_hits_when_scoring_fails(monkeypatch):
    class FailingModel:
        def predict(self, pairs, *, batch_size, show_progress_bar):
            raise RuntimeError("model failed")

    monkeypatch.setattr(reranker, "_load_model", lambda: FailingModel())

    hits = [{"chunk_id": "a", "text": "text", "score": 1.0}]
    assert reranker.rerank_hits("query", hits) is hits


def test_registry_client_uses_internal_token_and_expected_urls(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *, timeout):
            assert timeout == 10.0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, *, headers):
            calls.append((url, headers))
            if url.endswith("/corpora"):
                return FakeResponse({"corpora": ["a", "b"]})
            return FakeResponse({"corpus_id": "a"})

    monkeypatch.setattr(registry_client, "REGISTRY_INTERNAL_URL", "http://config-auth.internal")
    monkeypatch.setattr(registry_client, "CONFIG_AUTH_INTERNAL_TOKEN", "secret-token")
    monkeypatch.setattr(registry_client.httpx, "Client", FakeClient)

    assert registry_client.get_corpus("a") == {"corpus_id": "a"}
    assert registry_client.list_corpora() == ["a", "b"]
    assert calls == [
        ("http://config-auth.internal/corpora/a", {"Authorization": "Bearer secret-token"}),
        ("http://config-auth.internal/corpora", {"Authorization": "Bearer secret-token"}),
    ]


def test_registry_client_attaches_retrieval_profile(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *, timeout):
            assert timeout == 10.0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, *, headers):
            calls.append((url, headers))
            if url.endswith("/retrieval-profiles"):
                return FakeResponse(
                    [
                        {
                            "retrieval_profile_id": "docs.retrieval.v1",
                            "config": {"citation_fields": ["path"]},
                        }
                    ]
                )
            return FakeResponse({"corpus_id": "docs", "retrieval_profile_id": "docs.retrieval.v1"})

    monkeypatch.setattr(registry_client, "REGISTRY_INTERNAL_URL", "http://config-auth.internal")
    monkeypatch.setattr(registry_client, "CONFIG_AUTH_INTERNAL_TOKEN", "secret-token")
    monkeypatch.setattr(registry_client.httpx, "Client", FakeClient)

    corpus = registry_client.get_corpus("docs")

    assert corpus["retrieval_profile"]["config"] == {"citation_fields": ["path"]}
    assert calls == [
        ("http://config-auth.internal/corpora/docs", {"Authorization": "Bearer secret-token"}),
        ("http://config-auth.internal/retrieval-profiles", {"Authorization": "Bearer secret-token"}),
    ]


def test_registry_client_prefers_mounted_retrieval_profile_snapshot(monkeypatch, tmp_path):
    snapshot = tmp_path / "retrieval_profiles.json"
    snapshot.write_text(
        '{"docs.retrieval.v1": {"type": "hybrid", "config": {"citation_fields": ["path"]}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_client, "RETRIEVAL_PROFILE_REGISTRY_PATH", str(snapshot))
    monkeypatch.setattr(registry_client, "RETRIEVAL_PROFILE_REGISTRY_URL", "http://unused.internal/retrieval-profiles")

    class FailClient:
        def __init__(self, *, timeout):
            raise AssertionError("API fallback should not be used when mounted retrieval profiles exist")

    monkeypatch.setattr(registry_client.httpx, "Client", FailClient)

    profiles = registry_client.list_retrieval_profiles()

    assert profiles == [
        {
            "retrieval_profile_id": "docs.retrieval.v1",
            "type": "hybrid",
            "config": {"citation_fields": ["path"]},
        }
    ]


def test_registry_client_requires_url_and_token(monkeypatch):
    monkeypatch.setattr(registry_client, "REGISTRY_INTERNAL_URL", "")
    monkeypatch.setattr(registry_client, "CONFIG_AUTH_INTERNAL_TOKEN", "secret-token")
    with pytest.raises(ValueError, match="REGISTRY_INTERNAL_URL"):
        registry_client._get_headers()

    monkeypatch.setattr(registry_client, "REGISTRY_INTERNAL_URL", "http://config-auth.internal")
    monkeypatch.setattr(registry_client, "CONFIG_AUTH_INTERNAL_TOKEN", "")
    with pytest.raises(ValueError, match="CONFIG_AUTH_INTERNAL_TOKEN"):
        registry_client._get_headers()


def test_tei_embedder_posts_inputs_to_embed_endpoint(monkeypatch):
    from app import embedder

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [[0.1, 0.2], [0.3, 0.4]]

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            assert timeout == 12.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, json):
            assert url == "http://embedder.test/embed"
            assert json == {"inputs": ["hello", "world"]}
            return FakeResponse()

    monkeypatch.setattr(embedder.httpx, "AsyncClient", FakeAsyncClient)

    vectors = asyncio.run(embedder.TEIEmbedder("http://embedder.test/", timeout=12.0).embed(["hello", "world"]))

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
