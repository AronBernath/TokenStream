import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER_ROOT = REPO_ROOT / "services" / "ingestion_worker"
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

from worker.pdf_parser_service import PdfParserService


def make_minimal_pdf(lines: list[str]) -> bytes:
    escaped_lines = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    content_lines = ["BT", "/F1 12 Tf", "72 720 Td"]
    for index, line in enumerate(escaped_lines):
        if index:
            content_lines.append("0 -18 Td")
        content_lines.append(f"({line}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref_offset = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n").encode("ascii")
    )
    return bytes(out)


def test_pdf_parser_extracts_page_block_with_metadata():
    pdf = make_minimal_pdf(
        [
            "Acme Information Security Policy",
            "1. Purpose",
            "PDF parser should extract this text for embeddings.",
        ]
    )

    blocks = PdfParserService().parse_to_blocks(
        {"format": "pdf", "content": pdf, "url": "https://example.test/policy.pdf"},
        {"id": "policy_pdf", "language": "en", "doc_type": "policy", "tags": ["security"]},
        {"title": "Policy Corpus", "corpus_id": "policies"},
    )

    assert len(blocks) == 1
    block = blocks[0]
    assert "PDF parser should extract this text" in block["text"]
    assert block["source_url"] == "https://example.test/policy.pdf#page=1"
    assert block["metadata"]["format"] == "pdf"
    assert block["metadata"]["page_range"] == {"start": 1, "end": 1}


def test_pdf_parser_splits_configured_section_headings():
    pdf = make_minimal_pdf(
        [
            "Policy",
            "1.1. First control",
            "First control body.",
            "1.2. Second control",
            "Second control body.",
        ]
    )

    blocks = PdfParserService().parse_to_blocks(
        {"format": "pdf", "content": pdf, "url": "https://example.test/catalog.pdf"},
        {"id": "catalog_pdf", "language": "en", "doc_type": "control_catalog"},
        {"title": "Catalog", "corpus_id": "catalog"},
        rules={"heading_regex": r"^(\d+\.\d+\.)\s+(.+)$"},
    )

    section_ids = [block["section_id"] for block in blocks]
    assert "1.1" in section_ids
    assert "1.2" in section_ids
    assert any("First control body" in block["text"] for block in blocks)
    assert any("Second control body" in block["text"] for block in blocks)


def test_pdf_parser_uses_ocr_for_empty_native_text(monkeypatch):
    pdf = make_minimal_pdf([])
    service = PdfParserService()

    def fake_ocr_page(content: bytes, *, page_index: int, languages: str, rules: dict) -> str:
        assert page_index == 0
        assert languages == "hun+eng"
        return "OCR extracted security policy text."

    monkeypatch.setattr(service, "_ocr_page", fake_ocr_page)

    blocks = service.parse_to_blocks(
        {"format": "pdf", "content": pdf, "url": "https://example.test/scanned.pdf"},
        {"id": "scanned_pdf", "language": "en", "doc_type": "policy"},
        {"title": "Scanned Policy", "corpus_id": "policies"},
        rules={"ocr_languages": "hun+eng"},
    )

    assert len(blocks) == 1
    assert "OCR extracted security policy text" in blocks[0]["text"]
    assert blocks[0]["metadata"]["extraction_method"] == "ocr"
    assert blocks[0]["metadata"]["extraction_methods"] == ["ocr"]


def test_pdf_parser_caps_ocr_parallelism():
    service = PdfParserService()

    assert service._ocr_max_workers({"ocr_max_workers": 8}, candidate_count=10) == 4
    assert service._ocr_max_workers({"ocr_max_workers": 3}, candidate_count=2) == 2
    assert service._ocr_max_workers({"ocr_max_workers": 0}, candidate_count=10) == 1


def test_pdf_parser_image_regions_filter_and_sort():
    class FakePage:
        width = 600
        height = 800
        images = [
            {"x0": 300, "x1": 500, "top": 200, "bottom": 320},
            {"x0": 10, "x1": 20, "top": 10, "bottom": 20},
            {"x0": 50, "x1": 250, "top": 100, "bottom": 220},
        ]

    regions = PdfParserService()._image_regions_for_page(FakePage(), {})

    assert len(regions) == 2
    assert regions[0]["x0"] == 48
    assert regions[0]["top"] == 98
    assert regions[1]["x0"] == 298
    assert regions[1]["top"] == 198


def test_pdf_parser_merges_image_region_ocr_without_replacing_native_text():
    from worker.pdf_parser_service import PdfPageExtraction

    page = PdfPageExtraction(page_number=1, text="Native paragraph.", tables=[])

    PdfParserService()._merge_region_ocr_results(
        page,
        [(1, "Text inside an embedded diagram.", None)],
        {},
    )

    assert "Native paragraph." in page.text
    assert "OCR image region 1:" in page.text
    assert "embedded diagram" in page.text
    assert page.extraction_method == "pdf_text+ocr"
    assert page.ocr_regions_count == 1


def test_pdf_table_markdown_combines_multirow_headers_and_row_facts():
    table = [
        ["Assurance class", "Assurance family", "Assurance components by evaluation assurance level", "", ""],
        ["", "", "EAL1", "EAL2", "EAL3"],
        ["Development", "ADV_ARC", "", "1", "1"],
        ["", "ADV_FSP", "1", "2", "3"],
    ]

    text = PdfParserService()._table_to_markdown(table)

    assert "Assurance components by evaluation assurance level / EAL2" in text
    assert "PDF table row facts:" in text
    assert "Assurance class=Development; Assurance family=ADV_ARC" in text
    assert "EAL2=1" in text
    assert "Assurance family=ADV_FSP" in text
    assert "EAL3=3" in text


def test_pdf_table_cell_cleanup_repairs_soft_hyphenation():
    text = PdfParserService()._table_to_markdown(
        [
            ["Assurance class", "Assurance components"],
            ["APE: Protection Profile Evalu- ation", "APE_INT.1 PP introduction"],
        ]
    )

    assert "Evaluation" in text
