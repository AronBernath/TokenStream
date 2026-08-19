import importlib
import json
import os
import sqlite3
import sys
import types
from pathlib import Path

import pytest


SERVICES_ROOT = Path(__file__).resolve().parents[2]
INGESTION_WORKER_ROOT = SERVICES_ROOT / "ingestion_worker"
COMMON_ROOT = SERVICES_ROOT / "common"

if str(INGESTION_WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(INGESTION_WORKER_ROOT))
if str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

os.environ.setdefault("EMBEDDER_URL", "http://embedder.test")
os.environ.setdefault("QDRANT_URL", "http://qdrant.test:6333")

from common.models import Chunk  # noqa: E402
from worker import embed  # noqa: E402


def test_sanitize_texts_stringifies_and_truncates(monkeypatch):
    monkeypatch.setattr(embed, "EMBED_MAX_CHARS", 5)

    assert embed._sanitize_texts(["abcdef", 123]) == ["abcde", "123"]


def test_embed_batch_splits_oversized_payloads_on_413(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise AssertionError("raise_for_status should not be called for split 413 responses")

        def json(self):
            return self._payload

    class FakeClient:
        def post(self, url, *, json):
            assert url == "http://embedder.test/embed"
            inputs = json["inputs"]
            calls.append(list(inputs))
            if len(inputs) > 2:
                return FakeResponse(413)
            return FakeResponse(200, [[float(len(text))] for text in inputs])

    monkeypatch.setattr(embed, "EMBEDDER_URL", "http://embedder.test")

    vectors = embed._embed_batch(FakeClient(), ["aa", "bbb", "c", "dddd"], start_idx=0, total=4)

    assert calls == [["aa", "bbb", "c", "dddd"], ["aa", "bbb"], ["c", "dddd"]]
    assert vectors == [[2.0], [3.0], [1.0], [4.0]]


def test_embed_batch_rejects_unexpected_response_size():
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [[0.1]]

    class FakeClient:
        def post(self, url, *, json):
            return FakeResponse()

    with pytest.raises(ValueError, match="Unexpected embedder response size"):
        embed._embed_batch(FakeClient(), ["one", "two"], start_idx=0, total=2)


@pytest.fixture
def indexers_module(monkeypatch):
    qdrant_mod = types.ModuleType("qdrant_client")
    qdrant_http_mod = types.ModuleType("qdrant_client.http")
    qdrant_models_mod = types.ModuleType("qdrant_client.http.models")

    class FakeQdrantClient:
        def __init__(self, *, url):
            self.url = url

    class FakeDistance:
        COSINE = "Cosine"

    class FakeVectorParams:
        def __init__(self, *, size, distance):
            self.size = size
            self.distance = distance

    class FakePointStruct:
        def __init__(self, *, id, vector, payload):
            self.id = id
            self.vector = vector
            self.payload = payload

    qdrant_mod.QdrantClient = FakeQdrantClient
    qdrant_models_mod.Distance = FakeDistance
    qdrant_models_mod.VectorParams = FakeVectorParams
    qdrant_models_mod.PointStruct = FakePointStruct
    qdrant_models_mod.PointIdsList = lambda *, points: {"points": points}
    qdrant_models_mod.FilterSelector = lambda *, filter: {"filter": filter}
    qdrant_models_mod.Filter = lambda **kwargs: kwargs
    qdrant_models_mod.FieldCondition = lambda **kwargs: kwargs
    qdrant_models_mod.MatchValue = lambda **kwargs: kwargs
    qdrant_http_mod.models = qdrant_models_mod

    monkeypatch.setitem(sys.modules, "qdrant_client", qdrant_mod)
    monkeypatch.setitem(sys.modules, "qdrant_client.http", qdrant_http_mod)
    monkeypatch.setitem(sys.modules, "qdrant_client.http.models", qdrant_models_mod)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.test:6333")

    sys.modules.pop("worker.indexers", None)
    module = importlib.import_module("worker.indexers")
    try:
        yield module
    finally:
        sys.modules.pop("worker.indexers", None)


def test_indexer_names_are_stable_and_include_legacy_collection_alias(indexers_module):
    corpus = {
        "corpus_id": "policy",
        "environment": "dev",
        "tenant_id": "tenant",
        "index": {"qdrant_collection": "legacy_collection"},
    }

    assert indexers_module._point_id_from_chunk_id("0123456789abcdef-extra") == int("0123456789abcdef", 16)
    assert indexers_module._qdrant_collection_name(corpus) == "corp_dev_tenant_policy"
    assert indexers_module._qdrant_collections_for_corpus(corpus) == ["corp_dev_tenant_policy", "legacy_collection"]


def test_upsert_lexical_persists_chunks_graph_material_and_metadata_values(indexers_module, tmp_path):
    indexers_module.LEXICAL_INDEX_DIR = str(tmp_path)
    corpus = {
        "corpus_id": "policy",
        "environment": "dev",
        "tenant_id": "tenant",
    }
    chunk = Chunk(
        chunk_id="0123456789abcdef",
        corpus_id="policy",
        doc_id="source-1",
        title="Policy",
        section_id="sec-1",
        version_date="2026-07-23",
        language="en",
        jurisdiction="EU",
        source_url="https://example.test/policy",
        text="Policy text for lexical search.",
        metadata={
            "doc_type": "guidance",
            "tags": ["risk", "policy"],
            "symbol": "PolicyNode",
            "path": "services/policy.py",
            "graph_primary_node": {
                "node_id": "node-policy",
                "node_type": "policy",
                "label": "Policy Node",
                "aliases": ["Policy Node"],
                "metadata": {"doc_id": "source-1", "section_id": "sec-1"},
            },
            "graph_edges": [
                {
                    "src_node_id": "node-policy",
                    "edge_type": "refers_to",
                    "dst_alias": "Related Rule",
                    "weight": 0.7,
                }
            ],
        },
    )

    indexers_module.ensure_indexes(corpus)
    count, db_path = indexers_module.upsert_lexical(corpus, [chunk])

    assert count == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT doc_id, tags_json, metadata_json FROM chunks WHERE chunk_id = ?;", (chunk.chunk_id,)
        ).fetchone()
        assert row[0] == "source-1"
        assert json.loads(row[1]) == ["risk", "policy"]
        metadata = json.loads(row[2])
        assert metadata["environment"] == "dev"
        assert metadata["tenant_id"] == "tenant"
        assert metadata["corpus_id"] == "policy"
        fts_tags = conn.execute(
            "SELECT tags FROM chunks_fts WHERE chunk_id = ?;",
            (chunk.chunk_id,),
        ).fetchone()[0]
        assert "PolicyNode" in fts_tags
        assert "services/policy.py" in fts_tags

        assert (
            conn.execute("SELECT label FROM canonical_nodes WHERE node_id = 'node-policy';").fetchone()[0]
            == "Policy Node"
        )
        assert (
            conn.execute("SELECT alias_norm FROM node_aliases WHERE node_id = 'node-policy';").fetchone()[0]
            == "policy node"
        )
        assert (
            conn.execute("SELECT link_role FROM chunk_node_links WHERE chunk_id = ?;", (chunk.chunk_id,)).fetchone()[0]
            == "primary"
        )
        assert conn.execute(
            "SELECT dst_alias, weight FROM node_edges WHERE src_node_id = 'node-policy';"
        ).fetchone() == (
            "related rule",
            0.7,
        )


def test_source_hashes_and_cleanup_use_registry_source_id(indexers_module, tmp_path):
    indexers_module.LEXICAL_INDEX_DIR = str(tmp_path)
    corpus = {"corpus_id": "bundle_docs", "environment": "dev", "tenant_id": "tenant"}
    chunks = [
        Chunk(
            chunk_id="1111111111111111",
            corpus_id="bundle_docs",
            doc_id="src/app.py",
            title="app.py",
            section_id="function:app",
            version_date=None,
            language="python",
            jurisdiction=None,
            source_url="repo://bundle/src/app.py#L1-L4",
            text="def app(): pass",
            metadata={
                "doc_type": "code",
                "registry_source_id": "snapshot",
                "source_fingerprint": "processor:custom:hash:content",
            },
        ),
        Chunk(
            chunk_id="2222222222222222",
            corpus_id="bundle_docs",
            doc_id="tests/test_app.py",
            title="test_app.py",
            section_id="test:test_app",
            version_date=None,
            language="python",
            jurisdiction=None,
            source_url="repo://bundle/tests/test_app.py#L1-L5",
            text="def test_app(): pass",
            metadata={
                "doc_type": "test",
                "registry_source_id": "snapshot",
                "source_fingerprint": "processor:custom:hash:content",
            },
        ),
    ]

    indexers_module.ensure_indexes(corpus)
    count, db_path = indexers_module.upsert_lexical(corpus, chunks)

    assert count == 2
    assert indexers_module.get_corpus_source_hashes(corpus) == {"snapshot": "processor:custom:hash:content"}

    deleted_chunks, _deleted_qdrant = indexers_module.delete_corpus_source_artifacts(corpus, "snapshot")

    assert deleted_chunks == 2
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chunks;").fetchone()[0] == 0
