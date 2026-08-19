from pathlib import Path
import sys


SERVICES_ROOT = Path(__file__).resolve().parents[2]
INGESTION_WORKER_ROOT = SERVICES_ROOT / "ingestion_worker"
COMMON_ROOT = SERVICES_ROOT / "common"

if str(INGESTION_WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(INGESTION_WORKER_ROOT))
if str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

from worker.parsers import parse_to_blocks  # noqa: E402


def test_html_parser_uses_generic_structure_even_when_legacy_kind_is_configured():
    raw = {
        "format": "html",
        "url": "https://example.test/document",
        "content": """
        <html>
          <body>
            <h1>Document</h1>
            <h2>Section A</h2>
            <p>First paragraph.</p>
            <p>Second paragraph.</p>
            <ul><li>First item.</li><li>Second item.</li></ul>
          </body>
        </html>
        """,
    }
    src = {"id": "doc", "format": "html", "type": "url"}
    corpus = {"corpus_id": "corpus", "title": "Corpus"}
    rules = {"html": {"default_kind": "eurlex_act"}}

    blocks = parse_to_blocks(raw, src, corpus, rules=rules)

    assert [block["metadata"]["section_kind"] for block in blocks] == ["p", "p", "li", "li"]
    assert all(block["metadata"]["format"] == "html" for block in blocks)
    assert all("html_kind" not in block["metadata"] for block in blocks)
    assert all("Section A" in block["text"] for block in blocks)
