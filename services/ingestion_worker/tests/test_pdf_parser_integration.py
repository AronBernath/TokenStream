from pathlib import Path
import sys

import pytest


SERVICES_ROOT = Path(__file__).resolve().parents[2]
INGESTION_WORKER_ROOT = SERVICES_ROOT / "ingestion_worker"
COMMON_ROOT = SERVICES_ROOT / "common"

if str(INGESTION_WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(INGESTION_WORKER_ROOT))
if str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

from worker import parsers  # noqa: E402

pytestmark = pytest.mark.integration


def test_parse_to_blocks_routes_pdf_to_pdf_parser_service(monkeypatch):
    calls = {}

    def fake_parse(self, raw, src, corpus, rules=None):
        calls["raw"] = raw
        calls["src"] = src
        calls["corpus"] = corpus
        calls["rules"] = rules
        return [
            {
                "title": "PDF Doc",
                "section_id": "page_0001",
                "text": "Parsed PDF text",
                "source_url": "urn:test:pdf",
                "language": "en",
                "doc_id": "pdf_doc",
                "doc_type": "standard",
                "tags": ["pdf"],
                "metadata": {"format": "pdf"},
            }
        ]

    monkeypatch.setattr(parsers.PdfParserService, "parse_to_blocks", fake_parse)

    raw = {
        "format": "pdf",
        "content": b"%PDF-1.7 fake content",
        "local_path": "/tmp/test.pdf",
        "content_hash": "abc123",
        "url": "urn:test:pdf",
    }
    src = {
        "id": "pdf_doc",
        "title": "PDF Doc",
        "doc_type": "standard",
        "tags": ["pdf"],
        "language": "en",
    }
    corpus = {"title": "Corpus Title"}
    rules = {"pdf": {"ocr_enabled": False}}

    blocks = parsers.parse_to_blocks(raw, src, corpus, rules=rules)

    assert calls["raw"]["format"] == "pdf"
    assert calls["src"]["local_path"] == "/tmp/test.pdf"
    assert calls["rules"] == {"ocr_enabled": False}
    assert blocks[0]["metadata"]["format"] == "pdf"
    assert blocks[0]["metadata"]["source_content_hash"] == "abc123"
