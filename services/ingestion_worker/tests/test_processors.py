import io
import sys
import zipfile
from pathlib import Path

import pytest


SERVICES_ROOT = Path(__file__).resolve().parents[2]
INGESTION_WORKER_ROOT = SERVICES_ROOT / "ingestion_worker"
COMMON_ROOT = SERVICES_ROOT / "common"

if str(INGESTION_WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(INGESTION_WORKER_ROOT))
if str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

from worker.processors import (  # noqa: E402
    DEFAULT_PROCESSOR_ID,
    ProcessorContext,
    ProcessorResult,
    processor_config_hash,
    resolve_processor_config,
    resolve_processor_id,
    run_processor,
    source_processing_fingerprint,
)


def test_processor_resolution_uses_job_then_source_then_corpus_and_merges_config():
    corpus = {
        "processor_id": "corpus.processor",
        "processor_config": {"include": ["*.md"], "nested": {"b": "corpus"}, "corpus": True},
        "processor_registry": {
            "source.processor": {
                "processor_id": "source.processor",
                "type": "generic",
                "config": {"nested": {"a": 1}, "registry": True},
            }
        },
    }
    source = {
        "processor_id": "source.processor",
        "processor_config": {"nested": {"c": 2}, "source": True},
    }

    assert resolve_processor_id(corpus, source) == "source.processor"
    assert resolve_processor_id(corpus, source, "job.processor") == "job.processor"
    assert resolve_processor_id({}, {}) == DEFAULT_PROCESSOR_ID

    assert resolve_processor_config(
        corpus,
        source,
        {"nested": {"d": 3}, "job": True},
        processor_id="source.processor",
    ) == {
        "include": ["*.md"],
        "nested": {"a": 1, "b": "corpus", "c": 2, "d": 3},
        "registry": True,
        "corpus": True,
        "source": True,
        "job": True,
    }


def test_source_processing_fingerprint_preserves_default_legacy_shape_and_hashes_custom_config():
    assert source_processing_fingerprint("abc", DEFAULT_PROCESSOR_ID, {}) == "generic-core-v1:abc"

    first = source_processing_fingerprint("abc", "custom.processor", {"x": 1})
    second = source_processing_fingerprint("abc", "custom.processor", {"x": 2})

    assert first != second
    assert first == f"processor:custom.processor:{processor_config_hash({'x': 1})}:abc"


def test_run_processor_uses_registered_generic_adapter_and_annotates_metadata():
    context = ProcessorContext(
        corpus={"corpus_id": "corpus"},
        source={"id": "snapshot"},
        raw={"format": "text", "content": "hello", "content_hash": "abc"},
        version_date=None,
        pipeline_id="writer",
        chunking_model=None,
        processor_id="custom.processor",
        processor_config={"mode": "test"},
        processor_registry={
            "custom.processor": {
                "processor_id": "custom.processor",
                "type": "generic",
            }
        },
    )

    def default_processor(default_context: ProcessorContext):
        assert default_context.processor_config == {"mode": "test"}
        return ProcessorResult(
            blocks_parsed=1,
            stats={"parser_name": "generic-core", "parser_version": "generic-core-v1"},
            chunks=[
                {
                    "chunk_id": "0123456789abcdef",
                    "corpus_id": "corpus",
                    "doc_id": "file-a.py",
                    "title": "file-a.py",
                    "section_id": "function:demo",
                    "version_date": None,
                    "language": "python",
                    "jurisdiction": None,
                    "source_url": "repo://corpus/abc/file-a.py#L1-L5",
                    "text": "def demo(): pass",
                    "metadata": {"path": "file-a.py"},
                }
            ],
        )

    result = run_processor(context, default_processor)

    assert result.blocks_parsed == 1
    assert result.stats == {"parser_name": "generic-core", "parser_version": "generic-core-v1"}
    assert result.chunks[0].metadata["registry_source_id"] == "snapshot"
    assert result.chunks[0].metadata["ingestion_processor_id"] == "custom.processor"
    assert result.chunks[0].metadata["source_fingerprint"].startswith("processor:custom.processor:")


def test_run_processor_uses_worker_owned_structured_archive_adapter():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("src/app.py", "def create_app():\n    return 'ok'\n")
        zf.writestr(".env", "SECRET=do-not-index\n")

    context = ProcessorContext(
        corpus={
            "corpus_id": "corpus",
            "processor_registry": {
                "archive.processor.v1": {
                    "processor_id": "archive.processor.v1",
                    "type": "structured_archive",
                }
            },
            "sources": [{"id": "snapshot"}],
        },
        source={"id": "snapshot", "metadata": {"branch": "main", "commit_sha": "abc123"}},
        raw={
            "format": "zip",
            "content": archive.getvalue(),
            "content_hash": "abc",
            "url": "s3://bucket/snapshot.zip",
        },
        version_date=None,
        pipeline_id="writer",
        chunking_model=None,
        processor_id="archive.processor.v1",
        processor_config={
            "include": ["src/**"],
            "metadata_defaults": {"repo": "orchestrator"},
        },
    )

    result = run_processor(context, lambda _context: ProcessorResult(chunks=[]))

    assert result.blocks_parsed == 1
    assert result.stats["parser_name"] == "structured-archive"
    assert len(result.chunks) == 1
    chunk = result.chunks[0]
    assert chunk.doc_id == "src/app.py"
    assert chunk.language == "python"
    assert chunk.metadata["repo"] == "orchestrator"
    assert chunk.metadata["branch"] == "main"
    assert chunk.metadata["commit_sha"] == "abc123"
    assert chunk.metadata["path"] == "src/app.py"
    assert chunk.metadata["source_kind"] == "code"
    assert chunk.metadata["start_line"] == 1
    assert chunk.metadata["end_line"] == 2
    assert chunk.metadata["ingestion_processor_id"] == "archive.processor.v1"


def test_run_processor_rejects_disabled_and_unsupported_processors():
    base_context = {
        "corpus": {"corpus_id": "corpus"},
        "source": {"id": "snapshot"},
        "raw": {"format": "text", "content": "hello", "content_hash": "abc"},
        "version_date": None,
        "pipeline_id": "writer",
        "chunking_model": None,
        "processor_config": {},
    }

    disabled = ProcessorContext(
        **base_context,
        processor_id="disabled.processor",
        processor_registry={
            "disabled.processor": {
                "processor_id": "disabled.processor",
                "type": "generic",
                "enabled": False,
            }
        },
    )
    with pytest.raises(RuntimeError, match="disabled"):
        run_processor(disabled, lambda _context: ProcessorResult(chunks=[]))

    unsupported = ProcessorContext(
        **base_context,
        processor_id="unsupported.processor",
        processor_registry={
            "unsupported.processor": {
                "processor_id": "unsupported.processor",
                "type": "unknown_adapter",
            }
        },
    )
    with pytest.raises(RuntimeError, match="Unsupported ingestion processor type"):
        run_processor(unsupported, lambda _context: ProcessorResult(chunks=[]))
