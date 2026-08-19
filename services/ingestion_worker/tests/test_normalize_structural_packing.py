from pathlib import Path
import sys


SERVICES_ROOT = Path(__file__).resolve().parents[2]
INGESTION_WORKER_ROOT = SERVICES_ROOT / "ingestion_worker"
COMMON_ROOT = SERVICES_ROOT / "common"

if str(INGESTION_WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(INGESTION_WORKER_ROOT))
if str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

from worker.normalize import blocks_to_chunks  # noqa: E402


def _block(section_id: str, text: str) -> dict:
    return {
        "doc_id": "doc",
        "title": "Document",
        "section_id": section_id,
        "text": text,
        "source_url": "https://example.test/doc",
        "language": "en",
        "doc_type": "html",
        "tags": ["test"],
        "metadata": {
            "format": "html",
            "parser_version": "generic-core-v1",
            "source_fingerprint": "generic-core-v1:hash",
        },
    }


def test_small_structural_blocks_are_packed_without_llm_call():
    def chat_fn(_system: str, _user: str) -> str:
        raise AssertionError("small structural blocks should not call the LLM")

    chunks = blocks_to_chunks(
        [
            _block("p-0001", "First paragraph."),
            _block("p-0002", "Second paragraph."),
        ],
        {"corpus_id": "corpus", "chunking": {"strategy": "llm", "target_chars": 80}},
        version_date=None,
        chat_fn=chat_fn,
    )

    assert len(chunks) == 1
    assert chunks[0].section_id == "p-0001--p-0002"
    assert chunks[0].text == "First paragraph.\n\nSecond paragraph."
    assert chunks[0].metadata["source_block_count"] == 2
    assert chunks[0].metadata["source_section_ids"] == ["p-0001", "p-0002"]


def test_oversized_structural_block_uses_llm():
    calls = []

    def chat_fn(_system: str, _user: str) -> str:
        calls.append(True)
        return '{"chunks":[{"start":0,"end":11},{"start":11,"end":22}]}'

    chunks = blocks_to_chunks(
        [_block("p-0001", "alpha beta gamma delta")],
        {"corpus_id": "corpus", "chunking": {"strategy": "llm", "target_chars": 10}},
        version_date=None,
        chat_fn=chat_fn,
    )

    assert calls
    assert len(chunks) >= 2
    assert chunks[0].section_id == "p-0001:part_0"
