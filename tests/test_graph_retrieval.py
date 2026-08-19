import asyncio
import importlib
import sys
import sqlite3
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "common"))
sys.path.insert(0, str(ROOT / "services" / "ingestion_worker"))
sys.path.insert(0, str(ROOT / "services" / "retrieval_api"))

if "qdrant_client" not in sys.modules:
    qdrant_client_module = types.ModuleType("qdrant_client")

    class _DummyQdrantClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_collections(self):
            return types.SimpleNamespace(collections=[])

        def create_collection(self, *args, **kwargs):
            return None

        def upsert(self, *args, **kwargs):
            return None

    qdrant_client_module.QdrantClient = _DummyQdrantClient

    http_module = types.ModuleType("qdrant_client.http")
    models_module = types.ModuleType("qdrant_client.http.models")

    class _DummyModel:
        def __init__(self, *args, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class _DummyDistance:
        COSINE = "cosine"

    models_module.PointStruct = _DummyModel
    models_module.VectorParams = _DummyModel
    models_module.Distance = _DummyDistance
    models_module.FieldCondition = _DummyModel
    models_module.MatchValue = _DummyModel
    models_module.Filter = _DummyModel
    models_module.Range = _DummyModel
    models_module.FilterSelector = _DummyModel

    sys.modules["qdrant_client"] = qdrant_client_module
    sys.modules["qdrant_client.http"] = http_module
    sys.modules["qdrant_client.http.models"] = models_module

from common.models import Chunk, QueryRequest
from worker.parsers import parse_to_blocks
from worker.normalize import blocks_to_chunks


def _load_graph_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("LEXICAL_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    sys.modules.pop("worker.indexers", None)
    sys.modules.pop("app.sqlite_fts_client", None)
    indexers = importlib.import_module("worker.indexers")
    sqlite_fts_client = importlib.import_module("app.sqlite_fts_client")
    return indexers, sqlite_fts_client


def test_parse_to_blocks_html_extracts_generic_sections():
    html = """
    <html>
      <head><title>Demo Document</title></head>
      <body>
        <main>
          <p>First paragraph introduces the topic.</p>
          <p>Second paragraph has an identifier ABC-123.</p>
        </main>
      </body>
    </html>
    """
    src = {
        "id": "demo_doc",
        "url": "https://example.test/doc",
        "doc_type": "reference",
        "language": "en",
        "tags": ["demo"],
    }
    corpus = {"corpus_id": "demo", "title": "Demo corpus"}
    rules = {}

    blocks = parse_to_blocks({"format": "html", "content": html, "url": src["url"]}, src, corpus, rules=rules)

    assert len(blocks) == 2
    assert all(block["section_id"].startswith("p-") for block in blocks)
    assert all(block["metadata"]["parser_version"] == "generic-core-v1" for block in blocks)

    second_block = blocks[1]
    graph_node = second_block["metadata"]["graph_primary_node"]
    assert graph_node["node_type"] == "p"
    assert "abc-123" in graph_node["aliases"]
    assert any(edge["edge_type"] == "part_of" for edge in second_block["metadata"]["graph_edges"])


def test_blocks_to_chunks_preserve_graph_metadata():
    corpus = {
        "corpus_id": "demo",
        "chunking": {"strategy": "llm", "target_chars": 32, "overlap_chars": 0},
    }
    block = {
        "doc_id": "doc-1",
        "title": "Demo",
        "section_id": "article_1",
        "text": "This article text is intentionally long enough to split into multiple chunks.",
        "source_url": "https://example.test/doc-1",
        "metadata": {
            "graph_primary_node": {
                "node_id": "article:doc-1:article_1",
                "node_type": "article",
                "label": "Article 1",
                "aliases": ["article 1"],
                "metadata": {"doc_id": "doc-1", "section_id": "article_1"},
            }
        },
    }

    def mock_chat(system: str, user: str) -> str:
        return '{"chunks":[{"start": 0, "end": 34}, {"start": 35, "end": 77}]}'

    chunks = blocks_to_chunks([block], corpus, version_date="2026-01-01", chat_fn=mock_chat)

    assert len(chunks) >= 2
    assert all(chunk.metadata["graph_primary_node"]["node_id"] == "article:doc-1:article_1" for chunk in chunks)


def test_sqlite_graph_reference_lookup_and_expansion(tmp_path, monkeypatch):
    indexers, sqlite_fts_client = _load_graph_modules(tmp_path, monkeypatch)
    corpus = {"corpus_id": "graph_demo", "index": {"qdrant_collection": "corp_graph_demo"}}

    doc_node = {
        "node_id": "doc:eu_act_2022/2555",
        "node_type": "document",
        "label": "Directive (EU) 2022/2555",
        "aliases": ["2022/2555", "celex 32022L2555"],
        "metadata": {"doc_id": "eu_act_2022/2555", "source_url": "https://example.test/nis2"},
    }
    article_node = {
        "node_id": "article:eu_act_2022/2555:article_21",
        "node_type": "article",
        "label": "Article 21",
        "aliases": ["article 21", "21. cikk"],
        "metadata": {"doc_id": "eu_act_2022/2555", "section_id": "article_21"},
    }
    recital_node = {
        "node_id": "recital:eu_act_2022/2555:recital_12",
        "node_type": "recital",
        "label": "Recital 12",
        "aliases": ["recital 12", "(12)"],
        "metadata": {"doc_id": "eu_act_2022/2555", "section_id": "recital_12"},
    }

    chunks = [
        Chunk(
            chunk_id="article-21",
            corpus_id="graph_demo",
            doc_id="eu_act_2022/2555",
            title="Directive (EU) 2022/2555",
            section_id="article_21",
            version_date="2026-01-01",
            language="hu",
            jurisdiction="EU",
            source_url="https://example.test/nis2#article-21",
            text="Article 21 sets cyber risk-management measures.",
            metadata={
                "doc_type": "act",
                "tags": ["law", "nis2"],
                "graph_document_node": doc_node,
                "graph_primary_node": article_node,
                "graph_edges": [
                    {
                        "src_node_id": article_node["node_id"],
                        "edge_type": "refers_to",
                        "dst_node_id": recital_node["node_id"],
                        "weight": 0.9,
                        "metadata": {},
                    }
                ],
            },
        ),
        Chunk(
            chunk_id="recital-12",
            corpus_id="graph_demo",
            doc_id="eu_act_2022/2555",
            title="Directive (EU) 2022/2555",
            section_id="recital_12",
            version_date="2026-01-01",
            language="hu",
            jurisdiction="EU",
            source_url="https://example.test/nis2#recital-12",
            text="Recital 12 explains why Article 21 is important.",
            metadata={
                "doc_type": "act",
                "tags": ["law", "nis2"],
                "graph_document_node": doc_node,
                "graph_primary_node": recital_node,
                "graph_edges": [],
            },
        ),
    ]

    indexers.ensure_indexes(corpus)
    indexers.upsert_lexical(corpus, chunks)

    exact_hits = asyncio.run(
        sqlite_fts_client.sqlite_exact_reference_search(corpus, ["article 21"], top_k=5, filters={})
    )
    assert [hit["chunk_id"] for hit in exact_hits][:1] == ["article-21"]

    expanded = asyncio.run(sqlite_fts_client.sqlite_graph_expand(corpus, ["article-21"], top_k=5, filters={}))
    assert any(hit["chunk_id"] == "recital-12" for hit in expanded)


def test_ensure_indexes_creates_nested_lexical_parent(tmp_path, monkeypatch):
    indexers, _ = _load_graph_modules(tmp_path / "base", monkeypatch)
    corpus = {"corpus_id": "nested_parent_demo"}

    indexers.ensure_indexes(corpus)

    expected_db = tmp_path / "base" / "lexical" / "corp_default-env_default-tenant_nested_parent_demo.db"
    assert expected_db.exists()


def test_hybrid_query_uses_reranker_and_graph_expansion(monkeypatch):
    monkeypatch.setenv("EMBEDDER_URL", "http://dummy-embedder")
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    from app import hybrid_retrieval

    importlib.reload(hybrid_retrieval)

    class DummyEmbedder:
        async def embed(self, texts):
            return [[0.1, 0.2, 0.3]]

    async def fake_qdrant_search(corpus_id, vector, top_k, filters):
        return [
            {
                "chunk_id": "article-21",
                "score": 0.9,
                "doc_id": "doc",
                "doc_type": "act",
                "text": "Article 21 establishes measures.",
                "title": "Source Doc",
                "section_id": "article_21",
                "source_url": "https://example.test/article-21",
                "tags": ["law"],
                "metadata": {},
            }
        ]

    async def fake_fts_search(corpus_id, query, top_k, filters):
        return [
            {
                "chunk_id": "article-21",
                "score": 2.0,
                "doc_id": "doc",
                "doc_type": "act",
                "text": "Article 21 establishes measures.",
                "title": "Source Doc",
                "section_id": "article_21",
                "source_url": "https://example.test/article-21",
                "tags": ["law"],
                "metadata": {},
            },
            {
                "chunk_id": "secondary-guide",
                "score": 1.5,
                "doc_id": "doc",
                "doc_type": "guidance",
                "text": "A guidance summary of Article 21.",
                "title": "Guide",
                "section_id": "guide_21",
                "source_url": "https://example.test/guide",
                "tags": ["guidance"],
                "metadata": {},
            },
        ]

    async def fake_exact_search(corpus_id, aliases, top_k, filters):
        return [
            {
                "chunk_id": "article-21",
                "score": 3.0,
                "doc_id": "doc",
                "doc_type": "act",
                "text": "Article 21 establishes measures.",
                "title": "Source Doc",
                "section_id": "article_21",
                "source_url": "https://example.test/article-21",
                "tags": ["law"],
                "metadata": {},
            }
        ]

    async def fake_graph_expand(corpus_id, seed_chunk_ids, top_k, filters):
        return [
            {
                "chunk_id": "recital-12",
                "score": 0.8,
                "doc_id": "doc",
                "doc_type": "act",
                "text": "Recital 12 gives context for Article 21.",
                "title": "Source Doc",
                "section_id": "recital_12",
                "source_url": "https://example.test/recital-12",
                "tags": ["law"],
                "metadata": {},
            }
        ]

    async def fake_fetch_chunks(corpus_id, chunk_ids, filters):
        return []

    def fake_rerank(query, hits):
        out = []
        for hit in hits:
            updated = dict(hit)
            updated["rerank_score"] = 9.0 if hit["chunk_id"] == "article-21" else 6.0
            out.append(updated)
        return out

    monkeypatch.setattr(hybrid_retrieval, "TEIEmbedder", lambda base_url: DummyEmbedder())
    monkeypatch.setattr(hybrid_retrieval, "get_corpus", lambda corpus_id: {"corpus_id": corpus_id})
    monkeypatch.setattr(hybrid_retrieval, "qdrant_corpus_exists", lambda corpus_id: True)
    monkeypatch.setattr(hybrid_retrieval, "sqlite_corpus_exists", lambda corpus_id: True)
    monkeypatch.setattr(hybrid_retrieval, "qdrant_search", fake_qdrant_search)
    monkeypatch.setattr(hybrid_retrieval, "sqlite_fts_search", fake_fts_search)
    monkeypatch.setattr(hybrid_retrieval, "sqlite_exact_reference_search", fake_exact_search)
    monkeypatch.setattr(hybrid_retrieval, "sqlite_graph_expand", fake_graph_expand)
    monkeypatch.setattr(hybrid_retrieval, "sqlite_fetch_chunks_by_ids", fake_fetch_chunks)
    monkeypatch.setattr(hybrid_retrieval, "rerank_hits", fake_rerank)

    response, metrics = asyncio.run(
        hybrid_retrieval.hybrid_query_with_metrics(QueryRequest(query="Article 21", corpus_id="graph_demo", top_k=2))
    )

    assert response.chunks[0].chunk_id == "article-21"
    assert any(chunk.chunk_id == "recital-12" for chunk in response.chunks)
    assert metrics["exact_hits"] == 1
    assert metrics["graph_hits"] == 1


def test_hybrid_query_applies_retrieval_config_defaults_and_citation_fields(monkeypatch):
    monkeypatch.setenv("EMBEDDER_URL", "http://embedder.test")
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    from app import hybrid_retrieval

    importlib.reload(hybrid_retrieval)
    captured_filters = []

    class DummyEmbedder:
        async def embed(self, texts):
            return [[0.1, 0.2, 0.3]]

    async def fake_qdrant_search(corpus, vector, top_k, filters):
        captured_filters.append(("vector", dict(filters)))
        return [
            {
                "chunk_id": "code-1",
                "score": 0.9,
                "doc_id": "services/app.py",
                "doc_type": "code",
                "text": "def create_ingestion_job(): pass",
                "title": "create_ingestion_job",
                "section_id": "function:create_ingestion_job",
                "source_url": "repo://orchestrator/abc/services/app.py#L10-L20",
                "tags": ["code"],
                "metadata": {
                    "repo": "orchestrator",
                    "source_kind": "code",
                    "path": "services/app.py",
                    "start_line": 10,
                    "end_line": 20,
                },
            }
        ]

    async def fake_empty(*args, **kwargs):
        if "filters" in kwargs:
            captured_filters.append(("other", dict(kwargs["filters"])))
        return []

    monkeypatch.setattr(hybrid_retrieval, "TEIEmbedder", lambda base_url: DummyEmbedder())
    monkeypatch.setattr(
        hybrid_retrieval,
        "get_corpus",
        lambda corpus_id: {
            "corpus_id": corpus_id,
            "retrieval_config": {
                "default_filters": {"repo": "orchestrator"},
                "citation_fields": ["path", "start_line", "end_line"],
            },
        },
    )
    monkeypatch.setattr(hybrid_retrieval, "qdrant_corpus_exists", lambda corpus: True)
    monkeypatch.setattr(hybrid_retrieval, "sqlite_corpus_exists", lambda corpus: True)
    monkeypatch.setattr(hybrid_retrieval, "qdrant_search", fake_qdrant_search)
    monkeypatch.setattr(hybrid_retrieval, "sqlite_fts_search", fake_empty)
    monkeypatch.setattr(hybrid_retrieval, "sqlite_exact_reference_search", fake_empty)
    monkeypatch.setattr(hybrid_retrieval, "sqlite_graph_expand", fake_empty)
    monkeypatch.setattr(hybrid_retrieval, "sqlite_fetch_chunks_by_ids", fake_empty)
    monkeypatch.setattr(hybrid_retrieval, "rerank_hits", lambda query, hits: hits)

    response, _metrics = asyncio.run(
        hybrid_retrieval.hybrid_query_with_metrics(
            QueryRequest(
                query="create ingestion job",
                corpus_id="graph_demo",
                filters={"source_kind": "code"},
                top_k=1,
            )
        )
    )

    assert captured_filters
    assert all(filters["repo"] == "orchestrator" for _channel, filters in captured_filters)
    assert all(filters["source_kind"] == "code" for _channel, filters in captured_filters)
    assert response.citations[0]["path"] == "services/app.py"
    assert response.citations[0]["start_line"] == 10
    assert response.citations[0]["end_line"] == 20


def test_hybrid_query_surfaces_registry_configuration_errors(monkeypatch):
    monkeypatch.setenv("EMBEDDER_URL", "http://embedder.test")
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    from app import hybrid_retrieval

    importlib.reload(hybrid_retrieval)
    monkeypatch.setattr(
        hybrid_retrieval,
        "get_corpus",
        lambda corpus_id: (_ for _ in ()).throw(ValueError("unknown retrieval profile docs.v1")),
    )

    with pytest.raises(hybrid_retrieval.RetrievalConfigurationError, match="unknown retrieval profile"):
        asyncio.run(
            hybrid_retrieval.hybrid_query_with_metrics(QueryRequest(query="anything", corpus_id="graph_demo", top_k=1))
        )


def test_delete_corpus_document_removes_graph_and_chunks(monkeypatch, tmp_path):
    indexers, sqlite_fts_client = _load_graph_modules(tmp_path, monkeypatch)
    corpus = {"corpus_id": "delete_demo"}
    indexers.ensure_indexes(corpus)

    doc_node = {
        "node_id": "doc:demo_delete",
        "node_type": "document",
        "label": "Demo document",
        "aliases": ["demo_delete"],
        "metadata": {"doc_id": "demo_delete", "source_url": "https://example.test/docs/demo_delete"},
    }
    primary_node = {
        "node_id": "article:demo_delete:article_1",
        "node_type": "article",
        "label": "Article 1",
        "aliases": ["article 1"],
        "metadata": {"doc_id": "demo_delete", "section_id": "article_1"},
    }
    secondary_node = {
        "node_id": "article:demo_delete:article_2",
        "node_type": "article",
        "label": "Article 2",
        "aliases": ["article 2"],
        "metadata": {"doc_id": "demo_delete", "section_id": "article_2"},
    }

    chunks = [
        Chunk(
            chunk_id="0000000000000001",
            corpus_id="delete_demo",
            doc_id="demo_delete",
            title="Demo doc",
            section_id="article_1",
            version_date="2026-01-01",
            language="en",
            jurisdiction="EU",
            source_url="https://example.test/docs/demo_delete#article-1",
            text="First deleted chunk.",
            metadata={
                "doc_type": "act",
                "tags": ["policy"],
                "graph_document_node": doc_node,
                "graph_primary_node": primary_node,
                "graph_edges": [
                    {
                        "src_node_id": primary_node["node_id"],
                        "edge_type": "refers_to",
                        "dst_node_id": secondary_node["node_id"],
                        "weight": 1.0,
                        "metadata": {},
                    }
                ],
            },
        ),
        Chunk(
            chunk_id="0000000000000002",
            corpus_id="delete_demo",
            doc_id="demo_delete",
            title="Demo doc",
            section_id="article_2",
            version_date="2026-01-01",
            language="en",
            jurisdiction="EU",
            source_url="https://example.test/docs/demo_delete#article-2",
            text="Second deleted chunk.",
            metadata={
                "doc_type": "act",
                "tags": ["policy"],
                "graph_document_node": doc_node,
                "graph_primary_node": secondary_node,
                "graph_edges": [],
            },
        ),
    ]

    indexers.upsert_lexical(corpus, chunks)
    indexers.upsert_qdrant(corpus, chunks, [[0.1, 0.2], [0.1, 0.2]])

    db_path = indexers._sqlite_path(corpus)
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(1) FROM chunks WHERE doc_id = ?;", ("demo_delete",))
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT COUNT(1) FROM chunk_node_links;")
        assert cur.fetchone()[0] >= 3
        cur.execute("SELECT COUNT(1) FROM canonical_nodes;")
        assert cur.fetchone()[0] >= 3

    deleted_chunks, deleted_qdrant = indexers.delete_corpus_document(corpus, "demo_delete")
    assert deleted_chunks == 2
    assert deleted_qdrant >= 0

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(1) FROM chunks WHERE doc_id = ?;", ("demo_delete",))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(1) FROM chunks_fts WHERE chunk_id IN ('0000000000000001', '0000000000000002');")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(1) FROM chunk_node_links;")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(1) FROM canonical_nodes;")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(1) FROM node_aliases WHERE node_id LIKE 'article:demo_delete%';")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(1) FROM node_edges;")
        assert cur.fetchone()[0] == 0
