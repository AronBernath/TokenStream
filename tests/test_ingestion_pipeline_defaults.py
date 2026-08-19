import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "common"))
sys.path.insert(0, str(ROOT / "services" / "ingestion_worker"))

from common.models import Chunk


def test_run_ingest_defaults_missing_pipeline_id_to_default(monkeypatch):
    monkeypatch.setenv("EMBEDDER_URL", "http://embedder.test")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.test")
    monkeypatch.delenv("INGESTION_PIPELINE_ID", raising=False)

    from worker import main as worker_main

    importlib.reload(worker_main)
    captured = {}

    corpus = {
        "corpus_id": "demo",
        "sources": [{"id": "doc1", "url": "https://example.test/doc1", "format": "html"}],
        "chunking": {"strategy": "llm"},
    }
    block = {
        "doc_id": "doc1",
        "title": "Doc 1",
        "text": "Some document text.",
        "metadata": {},
    }
    chunk = Chunk(
        chunk_id="chunk1",
        corpus_id="demo",
        doc_id="doc1",
        title="Doc 1",
        section_id=None,
        version_date=None,
        language=None,
        jurisdiction=None,
        source_url=None,
        text="Some document text.",
        metadata={},
    )

    monkeypatch.setattr(worker_main, "load_corpus", lambda corpus_id: corpus)
    monkeypatch.setattr(worker_main, "ensure_indexes", lambda corpus: None)
    monkeypatch.setattr(worker_main, "get_corpus_source_hashes", lambda corpus: {})
    monkeypatch.setattr(worker_main, "fetch_source", lambda src, data_dir: {"format": "html", "content": "<p>x</p>"})
    monkeypatch.setattr(worker_main, "delete_corpus_source_artifacts", lambda corpus, source_id: (0, 0))
    monkeypatch.setattr(worker_main, "parse_to_blocks", lambda raw, src, corpus, rules=None: [block])
    monkeypatch.setattr(worker_main, "embed_texts", lambda texts: [[0.1, 0.2]])
    monkeypatch.setattr(worker_main, "_qdrant_collection_name", lambda corpus: "corp_demo")
    monkeypatch.setattr(worker_main, "upsert_qdrant", lambda corpus, chunks, vectors: len(chunks))
    monkeypatch.setattr(worker_main, "upsert_lexical", lambda corpus, chunks: (len(chunks), "/tmp/demo.sqlite"))

    def fake_blocks_to_chunks(blocks, corpus, version_date=None, pipeline_id=None, chunking_model=None):
        captured["pipeline_id"] = pipeline_id
        captured["chunking_model"] = chunking_model
        return [chunk]

    monkeypatch.setattr(worker_main, "blocks_to_chunks", fake_blocks_to_chunks)

    stats = worker_main.run_ingest("demo", pipeline_id=None)

    assert captured["pipeline_id"] == "default"
    assert captured["chunking_model"] is None
    assert stats["chunks_produced"] == 1


def test_run_ingest_passes_chunking_model_to_normalization(monkeypatch):
    monkeypatch.setenv("EMBEDDER_URL", "http://embedder.test")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.test")

    from worker import main as worker_main

    captured = {}
    corpus = {
        "corpus_id": "demo",
        "sources": [{"id": "doc1", "url": "https://example.test/doc1", "format": "html"}],
        "chunking": {"strategy": "llm"},
    }
    block = {
        "doc_id": "doc1",
        "title": "Doc 1",
        "text": "Some document text.",
        "metadata": {},
    }
    chunk = Chunk(
        chunk_id="chunk1",
        corpus_id="demo",
        doc_id="doc1",
        title="Doc 1",
        section_id=None,
        version_date=None,
        language=None,
        jurisdiction=None,
        source_url=None,
        text="Some document text.",
        metadata={},
    )

    monkeypatch.setattr(worker_main, "load_corpus", lambda corpus_id: corpus)
    monkeypatch.setattr(worker_main, "ensure_indexes", lambda corpus: None)
    monkeypatch.setattr(worker_main, "get_corpus_source_hashes", lambda corpus: {})
    monkeypatch.setattr(worker_main, "fetch_source", lambda src, data_dir: {"format": "html", "content": "<p>x</p>"})
    monkeypatch.setattr(worker_main, "delete_corpus_source_artifacts", lambda corpus, source_id: (0, 0))
    monkeypatch.setattr(worker_main, "parse_to_blocks", lambda raw, src, corpus, rules=None: [block])
    monkeypatch.setattr(worker_main, "embed_texts", lambda texts: [[0.1, 0.2]])
    monkeypatch.setattr(worker_main, "_qdrant_collection_name", lambda corpus: "corp_demo")
    monkeypatch.setattr(worker_main, "upsert_qdrant", lambda corpus, chunks, vectors: len(chunks))
    monkeypatch.setattr(worker_main, "upsert_lexical", lambda corpus, chunks: (len(chunks), "/tmp/demo.sqlite"))

    def fake_blocks_to_chunks(blocks, corpus, version_date=None, pipeline_id=None, chunking_model=None):
        captured["pipeline_id"] = pipeline_id
        captured["chunking_model"] = chunking_model
        return [chunk]

    monkeypatch.setattr(worker_main, "blocks_to_chunks", fake_blocks_to_chunks)

    worker_main.run_ingest("demo", pipeline_id="default", chunking_model="openai:gpt-5.4-mini")

    assert captured == {"pipeline_id": "default", "chunking_model": "openai:gpt-5.4-mini"}


def test_run_ingest_fails_zero_chunk_source_without_deleting_existing_artifacts(monkeypatch):
    monkeypatch.setenv("EMBEDDER_URL", "http://embedder.test")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.test")

    from worker import main as worker_main

    corpus = {
        "corpus_id": "demo",
        "sources": [{"id": "doc1", "url": "https://example.test/doc1", "format": "html"}],
        "chunking": {"strategy": "llm"},
    }
    block = {
        "doc_id": "doc1",
        "title": "Doc 1",
        "text": "Challenge page.",
        "metadata": {},
    }
    delete_calls = []

    monkeypatch.setattr(worker_main, "load_corpus", lambda corpus_id: corpus)
    monkeypatch.setattr(worker_main, "ensure_indexes", lambda corpus: None)
    monkeypatch.setattr(worker_main, "get_corpus_source_hashes", lambda corpus: {})
    monkeypatch.setattr(
        worker_main, "fetch_source", lambda src, data_dir: {"format": "html", "content": "<p>challenge</p>"}
    )
    monkeypatch.setattr(
        worker_main,
        "delete_corpus_source_artifacts",
        lambda corpus, source_id: delete_calls.append(source_id) or (1, 0),
    )
    monkeypatch.setattr(worker_main, "parse_to_blocks", lambda raw, src, corpus, rules=None: [block])
    monkeypatch.setattr(worker_main, "blocks_to_chunks", lambda *args, **kwargs: [])

    with pytest.raises(RuntimeError, match="All sources failed for corpus demo"):
        worker_main.run_ingest("demo", pipeline_id="default")

    assert delete_calls == []


def test_run_ingest_selective_failure_is_not_reported_as_no_matching_sources(monkeypatch):
    monkeypatch.setenv("EMBEDDER_URL", "http://embedder.test")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.test")

    from worker import main as worker_main

    corpus = {
        "corpus_id": "demo",
        "sources": [
            {"id": "doc1", "url": "https://example.test/doc1", "format": "html"},
            {"id": "doc2", "url": "https://example.test/doc2", "format": "html"},
        ],
        "chunking": {"strategy": "llm"},
    }

    monkeypatch.setattr(worker_main, "load_corpus", lambda corpus_id: corpus)
    monkeypatch.setattr(worker_main, "ensure_indexes", lambda corpus: None)
    monkeypatch.setattr(worker_main, "get_corpus_source_hashes", lambda corpus: {})
    monkeypatch.setattr(worker_main, "fetch_source", lambda src, data_dir: {"format": "html", "content": "<p>x</p>"})
    monkeypatch.setattr(worker_main, "parse_to_blocks", lambda raw, src, corpus, rules=None: [])

    with pytest.raises(RuntimeError, match="All sources failed for corpus demo: doc2"):
        worker_main.run_ingest("demo", pipeline_id="default", source_ids=["doc2"], force_reembed=True)
