from __future__ import annotations

import io
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import quote


@dataclass
class PdfPageExtraction:
    page_number: int
    text: str
    tables: list[str]
    width: float | None = None
    height: float | None = None
    extraction_method: str = "pdf_text"
    ocr_error: str | None = None
    ocr_regions_count: int = 0

    @property
    def combined_text(self) -> str:
        parts = [self.text, *self.tables]
        return "\n\n".join(part for part in parts if part and part.strip()).strip()


@dataclass
class PdfSection:
    section_id: str
    title: str | None
    text: str
    start_page: int
    end_page: int
    section_kind: str
    has_tables: bool
    extraction_methods: list[str]
    ocr_regions_count: int = 0


class PdfParserService:
    """
    Layout-aware PDF parser for corpus and document ingestion.

    pdfplumber/pdfminer is used because it gives better text positioning and table
    extraction than basic PDF text readers while remaining lightweight enough for
    the ingestion worker image.
    """

    DEFAULT_DROP_LINE_PATTERNS = (
        r"^\s*$",
        r"^\s*\d+\s*$",
    )
    DEFAULT_OCR_LANGUAGES = os.environ.get("PDF_OCR_LANGUAGES", "eng")
    DEFAULT_OCR_MAX_WORKERS = max(1, min(2, os.cpu_count() or 1))

    def parse_to_blocks(
        self,
        raw: dict[str, Any],
        src: dict[str, Any],
        corpus: dict[str, Any],
        rules: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        content = raw.get("content")
        if isinstance(content, str):
            raise TypeError("PDF parser expects raw PDF bytes, got str")
        if not content:
            return []

        rules = rules or {}
        pages, pdf_meta = self._extract_pages(bytes(content), rules)
        if not pages:
            return []

        sections = self._pages_to_sections(pages, rules)
        if not sections:
            ocr_errors = [page.ocr_error for page in pages if page.ocr_error]
            if ocr_errors:
                raise ValueError(
                    "PDF text extraction produced no embeddable text and OCR fallback failed. "
                    f"First OCR error: {ocr_errors[0]}"
                )
            raise ValueError(
                "PDF text extraction produced no embeddable text. "
                "The document may be scanned/image-only and needs OCR before ingestion."
            )

        source_url = raw.get("url") or src.get("url") or src.get("source_url")
        doc_title = (
            src.get("title")
            or self._clean_text(pdf_meta.get("Title") or "")
            or corpus.get("title")
            or src.get("id")
            or "PDF document"
        )
        doc_id = src.get("id") or source_url or raw.get("local_path") or "pdf"
        page_count = len(pages)

        blocks: list[dict[str, Any]] = []
        for section in sections:
            section_title = section.title or doc_title
            text_parts = [doc_title]
            if section.title and section.title != doc_title:
                text_parts.append(section.title)
            text_parts.append(section.text)
            text = "\n\n".join(part for part in text_parts if part and part.strip()).strip()

            blocks.append(
                {
                    "title": doc_title,
                    "section_id": section.section_id,
                    "text": text,
                    "source_url": self._page_source_url(source_url, section.start_page, section.end_page),
                    "language": src.get("language"),
                    "doc_id": doc_id,
                    "doc_type": src.get("doc_type", "pdf"),
                    "tags": src.get("tags", []),
                    "metadata": {
                        "format": "pdf",
                        "pdf_parser": "pdfplumber",
                        "pdf_title": doc_title,
                        "section_title": section_title,
                        "section_kind": section.section_kind,
                        "page_number": section.start_page,
                        "page_range": {"start": section.start_page, "end": section.end_page},
                        "page_count": page_count,
                        "has_tables": section.has_tables,
                        "extraction_method": self._primary_extraction_method(section.extraction_methods),
                        "extraction_methods": section.extraction_methods,
                        "ocr_regions_count": section.ocr_regions_count,
                        "local_path": raw.get("local_path"),
                        "source_filename": src.get("path") or src.get("local_path"),
                        "source_title": src.get("title") or doc_title,
                        "source_standard_year": src.get("standard_year"),
                        "source_edition": src.get("edition"),
                        "source_version_date": src.get("version_date"),
                        "source_corrected_version_date": src.get("corrected_version_date"),
                        "metadata": self._safe_pdf_metadata(pdf_meta),
                    },
                }
            )
        return blocks

    def _extract_pages(
        self,
        content: bytes,
        rules: dict[str, Any],
    ) -> tuple[list[PdfPageExtraction], dict[str, Any]]:
        try:
            import pdfplumber
        except ImportError as exc:
            raise RuntimeError(
                "PDF parsing requires the 'pdfplumber' package. "
                "Install services/ingestion_worker/requirements.txt before ingesting PDFs."
            ) from exc

        laparams = rules.get("laparams") or {}
        extract_text_kwargs = {
            "x_tolerance": rules.get("x_tolerance", 1.5),
            "y_tolerance": rules.get("y_tolerance", 3),
            "layout": rules.get("layout", True),
        }
        table_settings = {
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
            **(rules.get("table_settings") or {}),
        }
        ocr_enabled = rules.get("ocr_enabled", True)
        ocr_languages = rules.get("ocr_languages") or rules.get("ocr_lang") or self.DEFAULT_OCR_LANGUAGES
        image_region_ocr_enabled = rules.get("image_region_ocr_enabled", True)

        pages: list[PdfPageExtraction] = []
        page_ocr_candidates: list[int] = []
        region_ocr_candidates: list[tuple[int, list[dict[str, float]]]] = []
        with pdfplumber.open(io.BytesIO(content), laparams=laparams or None) as pdf:
            metadata = dict(pdf.metadata or {})
            for page in pdf.pages:
                raw_text = page.extract_text(**extract_text_kwargs) or ""
                text = self._normalize_extracted_text(raw_text)

                table_texts: list[str] = []
                if rules.get("extract_tables", True):
                    try:
                        tables = page.extract_tables(table_settings=table_settings) or []
                    except Exception:
                        tables = []
                    for table in tables:
                        markdown = self._table_to_markdown(table)
                        if markdown:
                            table_texts.append(markdown)

                extraction_method = "pdf_text"
                image_regions = self._image_regions_for_page(page, rules) if image_region_ocr_enabled else []

                pages.append(
                    PdfPageExtraction(
                        page_number=int(page.page_number),
                        text=text,
                        tables=table_texts,
                        width=float(page.width) if page.width else None,
                        height=float(page.height) if page.height else None,
                        extraction_method=extraction_method,
                    )
                )
                page_idx = len(pages) - 1
                if ocr_enabled and image_regions:
                    region_ocr_candidates.append((page_idx, image_regions))
                elif ocr_enabled and self._needs_ocr(text, table_texts, rules):
                    page_ocr_candidates.append(page_idx)

        if region_ocr_candidates:
            self._apply_region_ocr_fallbacks(
                pages,
                region_ocr_candidates,
                content=content,
                languages=ocr_languages,
                rules=rules,
            )
        if page_ocr_candidates:
            self._apply_ocr_fallbacks(
                pages,
                page_ocr_candidates,
                content=content,
                languages=ocr_languages,
                rules=rules,
            )

        pages = self._remove_repeated_headers_and_footers(pages, rules)
        pages = self._drop_configured_lines(pages, rules)
        return pages, metadata

    def _apply_ocr_fallbacks(
        self,
        pages: list[PdfPageExtraction],
        candidate_indexes: list[int],
        *,
        content: bytes,
        languages: str,
        rules: dict[str, Any],
    ) -> None:
        max_workers = self._ocr_max_workers(rules, len(candidate_indexes))
        if max_workers <= 1 or len(candidate_indexes) == 1:
            for page_idx in candidate_indexes:
                self._apply_ocr_to_page(
                    pages,
                    page_idx,
                    content=content,
                    languages=languages,
                    rules=rules,
                )
            return

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pdf-ocr") as executor:
            futures = {
                executor.submit(
                    self._ocr_page,
                    content,
                    page_index=pages[page_idx].page_number - 1,
                    languages=languages,
                    rules=rules,
                ): page_idx
                for page_idx in candidate_indexes
            }
            for future in as_completed(futures):
                page_idx = futures[future]
                try:
                    ocr_text = future.result()
                    ocr_error = None
                except Exception as exc:
                    ocr_text = ""
                    ocr_error = f"{type(exc).__name__}: {exc}"
                self._merge_ocr_result(pages[page_idx], ocr_text, ocr_error, rules)

    def _apply_region_ocr_fallbacks(
        self,
        pages: list[PdfPageExtraction],
        candidate_regions: list[tuple[int, list[dict[str, float]]]],
        *,
        content: bytes,
        languages: str,
        rules: dict[str, Any],
    ) -> None:
        tasks = [
            (page_idx, region_idx, region)
            for page_idx, regions in candidate_regions
            for region_idx, region in enumerate(regions, start=1)
        ]
        if not tasks:
            return

        max_workers = self._ocr_max_workers(rules, len(tasks))
        results: dict[int, list[tuple[int, str, str | None]]] = {}

        if max_workers <= 1 or len(tasks) == 1:
            for page_idx, region_idx, region in tasks:
                try:
                    text = self._ocr_page_region(
                        content,
                        page_index=pages[page_idx].page_number - 1,
                        region=region,
                        languages=languages,
                        rules=rules,
                    )
                    error = None
                except Exception as exc:
                    text = ""
                    error = f"{type(exc).__name__}: {exc}"
                results.setdefault(page_idx, []).append((region_idx, text, error))
        else:
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pdf-region-ocr") as executor:
                futures = {
                    executor.submit(
                        self._ocr_page_region,
                        content,
                        page_index=pages[page_idx].page_number - 1,
                        region=region,
                        languages=languages,
                        rules=rules,
                    ): (page_idx, region_idx)
                    for page_idx, region_idx, region in tasks
                }
                for future in as_completed(futures):
                    page_idx, region_idx = futures[future]
                    try:
                        text = future.result()
                        error = None
                    except Exception as exc:
                        text = ""
                        error = f"{type(exc).__name__}: {exc}"
                    results.setdefault(page_idx, []).append((region_idx, text, error))

        for page_idx, page_results in results.items():
            page_results.sort(key=lambda item: item[0])
            self._merge_region_ocr_results(pages[page_idx], page_results, rules)

    def _apply_ocr_to_page(
        self,
        pages: list[PdfPageExtraction],
        page_idx: int,
        *,
        content: bytes,
        languages: str,
        rules: dict[str, Any],
    ) -> None:
        page = pages[page_idx]
        try:
            ocr_text = self._ocr_page(
                content,
                page_index=page.page_number - 1,
                languages=languages,
                rules=rules,
            )
            ocr_error = None
        except Exception as exc:
            ocr_text = ""
            ocr_error = f"{type(exc).__name__}: {exc}"
        self._merge_ocr_result(page, ocr_text, ocr_error, rules)

    def _merge_ocr_result(
        self,
        page: PdfPageExtraction,
        ocr_text: str,
        ocr_error: str | None,
        rules: dict[str, Any],
    ) -> None:
        ocr_text = self._normalize_extracted_text(ocr_text)
        page.ocr_error = ocr_error
        if ocr_text and self._prefer_ocr_text(page.text, ocr_text, rules):
            if page.text and rules.get("ocr_keep_native_text", False):
                page.text = self._clean_text(f"{page.text}\n\nOCR fallback:\n{ocr_text}")
                page.extraction_method = "pdf_text+ocr"
            else:
                page.text = ocr_text
                page.extraction_method = "ocr"
        elif not page.text and ocr_error:
            page.extraction_method = "ocr_failed"

    def _merge_region_ocr_results(
        self,
        page: PdfPageExtraction,
        region_results: list[tuple[int, str, str | None]],
        rules: dict[str, Any],
    ) -> None:
        texts: list[str] = []
        errors: list[str] = []
        for _, text, error in region_results:
            clean = self._normalize_extracted_text(text)
            if clean and len(clean) >= int(rules.get("image_ocr_min_chars", 8)):
                texts.append(clean)
            if error:
                errors.append(error)

        if errors:
            page.ocr_error = "; ".join(errors[:3])
        if not texts:
            if not page.text and errors:
                page.extraction_method = "ocr_failed"
            return

        image_text = "\n\n".join(f"OCR image region {idx}:\n{text}" for idx, text in enumerate(texts, start=1))
        if page.text:
            page.text = self._clean_text(f"{page.text}\n\n{image_text}")
            page.extraction_method = "pdf_text+ocr"
        else:
            page.text = self._clean_text(image_text)
            page.extraction_method = "ocr"
        page.ocr_regions_count = len(texts)

    def _ocr_max_workers(self, rules: dict[str, Any], candidate_count: int) -> int:
        configured = rules.get("ocr_max_workers")
        if configured is None:
            configured = os.environ.get("PDF_OCR_MAX_WORKERS")
        try:
            workers = int(configured) if configured is not None else self.DEFAULT_OCR_MAX_WORKERS
        except (TypeError, ValueError):
            workers = self.DEFAULT_OCR_MAX_WORKERS
        return max(1, min(workers, candidate_count, 4))

    def _pages_to_sections(
        self,
        pages: list[PdfPageExtraction],
        rules: dict[str, Any],
    ) -> list[PdfSection]:
        heading_patterns = self._compile_heading_patterns(rules)
        if not heading_patterns:
            return self._page_sections(pages)

        sections: list[PdfSection] = []
        current_lines: list[str] = []
        current_title: str | None = None
        current_id: str | None = None
        current_kind = "section"
        current_start_page: int | None = None
        current_end_page: int | None = None
        current_has_tables = False
        current_methods: set[str] = set()
        current_ocr_regions_count = 0
        preface_by_page: dict[int, list[str]] = {}

        def flush() -> None:
            nonlocal current_lines, current_ocr_regions_count
            if not current_lines or current_start_page is None or current_end_page is None:
                current_lines = []
                current_ocr_regions_count = 0
                return
            text = self._clean_text("\n".join(current_lines))
            if not text:
                current_lines = []
                current_ocr_regions_count = 0
                return
            section_id = current_id or f"page_{current_start_page:04d}"
            sections.append(
                PdfSection(
                    section_id=section_id,
                    title=current_title,
                    text=text,
                    start_page=current_start_page,
                    end_page=current_end_page,
                    section_kind=current_kind,
                    has_tables=current_has_tables,
                    extraction_methods=sorted(current_methods) or ["pdf_text"],
                    ocr_regions_count=current_ocr_regions_count,
                )
            )
            current_lines = []
            current_methods.clear()
            current_ocr_regions_count = 0

        for page in pages:
            page_text = page.combined_text
            if not page_text:
                continue
            for line in page_text.splitlines():
                line = line.strip()
                if not line:
                    if current_lines:
                        current_lines.append("")
                    continue
                match_info = self._match_heading(line, heading_patterns)
                if match_info:
                    if current_lines:
                        flush()
                    elif current_start_page is None:
                        preface = "\n".join(preface_by_page.get(page.page_number, []))
                        if preface.strip():
                            sections.append(
                                PdfSection(
                                    section_id=f"page_{page.page_number:04d}_preface",
                                    title=None,
                                    text=self._clean_text(preface),
                                    start_page=page.page_number,
                                    end_page=page.page_number,
                                    section_kind="preface",
                                    has_tables=bool(page.tables),
                                    extraction_methods=[page.extraction_method],
                                    ocr_regions_count=page.ocr_regions_count,
                                )
                            )

                    current_id, current_title, current_kind = match_info
                    current_start_page = page.page_number
                    current_end_page = page.page_number
                    current_has_tables = bool(page.tables)
                    current_methods = {page.extraction_method}
                    current_ocr_regions_count = page.ocr_regions_count
                    current_lines = [line]
                    continue

                if current_start_page is None:
                    preface_by_page.setdefault(page.page_number, []).append(line)
                    continue

                current_end_page = page.page_number
                current_has_tables = current_has_tables or bool(page.tables)
                current_methods.add(page.extraction_method)
                current_ocr_regions_count += page.ocr_regions_count
                current_lines.append(line)

        flush()

        if not sections:
            return self._page_sections(pages)
        return sections

    def _page_sections(self, pages: list[PdfPageExtraction]) -> list[PdfSection]:
        sections: list[PdfSection] = []
        for page in pages:
            text = self._clean_text(page.combined_text)
            if not text:
                continue
            first_line = next((line.strip() for line in text.splitlines() if line.strip()), None)
            sections.append(
                PdfSection(
                    section_id=f"page_{page.page_number:04d}",
                    title=first_line,
                    text=text,
                    start_page=page.page_number,
                    end_page=page.page_number,
                    section_kind="page",
                    has_tables=bool(page.tables),
                    extraction_methods=[page.extraction_method],
                    ocr_regions_count=page.ocr_regions_count,
                )
            )
        return sections

    def _needs_ocr(
        self,
        text: str,
        table_texts: list[str],
        rules: dict[str, Any],
    ) -> bool:
        if rules.get("ocr_force", False):
            return True
        combined = self._clean_text("\n".join([text, *table_texts]))
        if not combined:
            return True
        min_chars = int(rules.get("ocr_min_chars", 40))
        min_words = int(rules.get("ocr_min_words", 6))
        if len(combined) < min_chars:
            return True
        words = re.findall(r"\w+", combined, flags=re.UNICODE)
        return len(words) < min_words

    def _prefer_ocr_text(self, native_text: str, ocr_text: str, rules: dict[str, Any]) -> bool:
        native = self._clean_text(native_text)
        ocr = self._clean_text(ocr_text)
        if not ocr:
            return False
        if not native:
            return True
        if rules.get("ocr_force_replace", False):
            return True
        min_gain = float(rules.get("ocr_min_gain_ratio", 1.25))
        return len(ocr) >= int(len(native) * min_gain)

    def _ocr_page(
        self,
        content: bytes,
        *,
        page_index: int,
        languages: str,
        rules: dict[str, Any],
    ) -> str:
        try:
            import pypdfium2 as pdfium
            import pytesseract
        except ImportError as exc:
            raise RuntimeError(
                "OCR fallback requires 'pypdfium2' and 'pytesseract'. "
                "Install services/ingestion_worker/requirements.txt and the Tesseract binary."
            ) from exc

        dpi = int(rules.get("ocr_dpi", 300))
        psm = int(rules.get("ocr_psm", 6))
        oem = int(rules.get("ocr_oem", 1))
        config = rules.get("ocr_config") or f"--oem {oem} --psm {psm}"

        pdf = pdfium.PdfDocument(content)
        try:
            page = pdf[page_index]
            try:
                bitmap = page.render(scale=dpi / 72)
                image = bitmap.to_pil()
            finally:
                close_page = getattr(page, "close", None)
                if callable(close_page):
                    close_page()
            return pytesseract.image_to_string(image, lang=languages, config=config) or ""
        finally:
            close_pdf = getattr(pdf, "close", None)
            if callable(close_pdf):
                close_pdf()

    def _ocr_page_region(
        self,
        content: bytes,
        *,
        page_index: int,
        region: dict[str, float],
        languages: str,
        rules: dict[str, Any],
    ) -> str:
        try:
            import pypdfium2 as pdfium
            import pytesseract
        except ImportError as exc:
            raise RuntimeError(
                "Image-region OCR requires 'pypdfium2' and 'pytesseract'. "
                "Install services/ingestion_worker/requirements.txt and the Tesseract binary."
            ) from exc

        dpi = int(rules.get("image_ocr_dpi", rules.get("ocr_dpi", 300)))
        psm = int(rules.get("image_ocr_psm", rules.get("ocr_psm", 6)))
        oem = int(rules.get("ocr_oem", 1))
        config = rules.get("image_ocr_config") or rules.get("ocr_config") or f"--oem {oem} --psm {psm}"

        pdf = pdfium.PdfDocument(content)
        try:
            page = pdf[page_index]
            try:
                crop = (
                    region["x0"],
                    region["page_height"] - region["bottom"],
                    region["page_width"] - region["x1"],
                    region["top"],
                )
                bitmap = page.render(scale=dpi / 72, crop=crop)
                image = bitmap.to_pil()
            finally:
                close_page = getattr(page, "close", None)
                if callable(close_page):
                    close_page()
            return pytesseract.image_to_string(image, lang=languages, config=config) or ""
        finally:
            close_pdf = getattr(pdf, "close", None)
            if callable(close_pdf):
                close_pdf()

    def _image_regions_for_page(self, page: Any, rules: dict[str, Any]) -> list[dict[str, float]]:
        min_width = float(rules.get("image_ocr_min_width", 80))
        min_height = float(rules.get("image_ocr_min_height", 30))
        min_area = float(rules.get("image_ocr_min_area", 2400))
        padding = float(rules.get("image_ocr_padding", 2))
        max_regions = int(rules.get("image_ocr_max_regions_per_page", 8))
        page_width = float(page.width or 0)
        page_height = float(page.height or 0)

        regions: list[dict[str, float]] = []
        for image in page.images or []:
            x0 = max(0.0, float(image.get("x0") or 0) - padding)
            x1 = min(page_width, float(image.get("x1") or 0) + padding)
            top = max(0.0, float(image.get("top") or 0) - padding)
            bottom = min(page_height, float(image.get("bottom") or 0) + padding)
            width = x1 - x0
            height = bottom - top
            if width < min_width or height < min_height or (width * height) < min_area:
                continue
            regions.append(
                {
                    "x0": x0,
                    "x1": x1,
                    "top": top,
                    "bottom": bottom,
                    "page_width": page_width,
                    "page_height": page_height,
                }
            )

        regions.sort(key=lambda r: (r["top"], r["x0"]))
        return self._dedupe_regions(regions)[:max_regions]

    def _dedupe_regions(self, regions: list[dict[str, float]]) -> list[dict[str, float]]:
        deduped: list[dict[str, float]] = []
        for region in regions:
            if any(self._region_overlap_ratio(region, existing) > 0.9 for existing in deduped):
                continue
            deduped.append(region)
        return deduped

    def _region_overlap_ratio(self, a: dict[str, float], b: dict[str, float]) -> float:
        x0 = max(a["x0"], b["x0"])
        x1 = min(a["x1"], b["x1"])
        top = max(a["top"], b["top"])
        bottom = min(a["bottom"], b["bottom"])
        if x1 <= x0 or bottom <= top:
            return 0.0
        overlap = (x1 - x0) * (bottom - top)
        area_a = max(1.0, (a["x1"] - a["x0"]) * (a["bottom"] - a["top"]))
        area_b = max(1.0, (b["x1"] - b["x0"]) * (b["bottom"] - b["top"]))
        return overlap / min(area_a, area_b)

    def _compile_heading_patterns(self, rules: dict[str, Any]) -> list[tuple[str, re.Pattern[str]]]:
        specs: list[tuple[str, str]] = []
        if rules.get("heading_regex"):
            specs.append(("heading", rules["heading_regex"]))
        if rules.get("item_regex"):
            specs.append(("item", rules["item_regex"]))
        for item in rules.get("section_regexes") or []:
            if isinstance(item, str):
                specs.append(("section", item))
            elif isinstance(item, dict) and item.get("regex"):
                specs.append((item.get("kind") or "section", item["regex"]))

        compiled: list[tuple[str, re.Pattern[str]]] = []
        for kind, pattern in specs:
            compiled.append((kind, re.compile(pattern)))
        return compiled

    def _match_heading(
        self,
        line: str,
        patterns: list[tuple[str, re.Pattern[str]]],
    ) -> tuple[str, str, str] | None:
        for kind, pattern in patterns:
            match = pattern.match(line)
            if not match:
                continue
            code = match.group(1) if match.groups() else line[:80]
            title = match.group(2) if len(match.groups()) >= 2 else line
            section_id = self._section_id(code or title)
            return section_id, self._clean_text(title or line), kind
        return None

    def _drop_configured_lines(
        self,
        pages: list[PdfPageExtraction],
        rules: dict[str, Any],
    ) -> list[PdfPageExtraction]:
        patterns = [
            re.compile(pattern) for pattern in (rules.get("drop_lines_regex") or self.DEFAULT_DROP_LINE_PATTERNS)
        ]
        out: list[PdfPageExtraction] = []
        for page in pages:
            lines = []
            for line in page.text.splitlines():
                if any(pattern.match(line) for pattern in patterns):
                    continue
                lines.append(line)
            out.append(
                PdfPageExtraction(
                    page_number=page.page_number,
                    text=self._clean_text("\n".join(lines)),
                    tables=page.tables,
                    width=page.width,
                    height=page.height,
                    extraction_method=page.extraction_method,
                    ocr_error=page.ocr_error,
                    ocr_regions_count=page.ocr_regions_count,
                )
            )
        return out

    def _remove_repeated_headers_and_footers(
        self,
        pages: list[PdfPageExtraction],
        rules: dict[str, Any],
    ) -> list[PdfPageExtraction]:
        if len(pages) < 3 or not rules.get("remove_repeated_headers_footers", True):
            return pages

        candidates: dict[str, int] = {}
        edge_window = max(1, min(int(rules.get("repeated_line_window", 8)), 20))
        for page in pages:
            lines = [line.strip() for line in page.text.splitlines() if line.strip()]
            edge_lines = lines[:edge_window] + lines[-edge_window:]
            for line in edge_lines:
                normalized = self._normalize_repeated_line(line)
                if 4 <= len(normalized) <= 220:
                    candidates[normalized] = candidates.get(normalized, 0) + 1

        threshold_ratio = float(rules.get("repeated_line_threshold_ratio", 0.55))
        threshold = max(3, int(len(pages) * threshold_ratio))
        repeated = {line for line, count in candidates.items() if count >= threshold}
        if not repeated:
            return pages

        cleaned: list[PdfPageExtraction] = []
        for page in pages:
            lines = []
            for line in page.text.splitlines():
                if self._normalize_repeated_line(line) in repeated:
                    continue
                lines.append(line)
            cleaned.append(
                PdfPageExtraction(
                    page_number=page.page_number,
                    text=self._clean_text("\n".join(lines)),
                    tables=page.tables,
                    width=page.width,
                    height=page.height,
                    extraction_method=page.extraction_method,
                    ocr_error=page.ocr_error,
                    ocr_regions_count=page.ocr_regions_count,
                )
            )
        return cleaned

    def _normalize_extracted_text(self, text: str) -> str:
        text = (text or "").replace("\r", "\n")
        text = text.replace("\u00a0", " ").replace("\ufeff", "")
        text = text.replace("\u00ad", "")
        text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return self._clean_text(text)

    def _clean_text(self, text: str) -> str:
        text = (text or "").replace("\r", "\n")
        lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _table_to_markdown(self, table: Iterable[Iterable[Any]]) -> str:
        rows = self._normalize_table_rows(table)
        if not rows:
            return ""

        header_count = self._infer_table_header_rows(rows)
        headers = self._combined_table_headers(rows[:header_count])
        body = rows[header_count:]
        lines = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in range(len(headers))) + " |",
        ]
        lines.extend("| " + " | ".join(row) + " |" for row in body)
        row_facts = self._table_row_facts(headers, body)
        parts = ["PDF table:\n" + "\n".join(lines)]
        if row_facts:
            parts.append("PDF table row facts:\n" + "\n".join(row_facts))
        return "\n\n".join(parts)

    def _normalize_table_rows(self, table: Iterable[Iterable[Any]]) -> list[list[str]]:
        rows = [
            [self._clean_table_cell(cell) for cell in row]
            for row in table
            if row is not None and any(self._clean_table_cell(cell) for cell in row)
        ]
        if not rows:
            return []
        width = max(len(row) for row in rows)
        return [row + [""] * (width - len(row)) for row in rows]

    def _infer_table_header_rows(self, rows: list[list[str]]) -> int:
        if len(rows) < 2:
            return 1
        width = len(rows[0])
        first_empty = sum(1 for cell in rows[0] if not cell)
        second = rows[1]
        second_nonempty = sum(1 for cell in second if cell)
        second_text = " ".join(second)

        # ISO-style matrices often encode a spanning top-level header in row 1
        # and concrete column labels such as EAL1..EAL7 in row 2.
        if re.search(r"\b[A-Z]{2,}\d+\b", second_text) or re.search(r"\bEAL\s*\d+\b", second_text):
            return 2
        if width >= 4 and first_empty >= max(1, width // 3) and second_nonempty >= 2:
            return 2
        return 1

    def _combined_table_headers(self, header_rows: list[list[str]]) -> list[str]:
        if not header_rows:
            return []
        width = len(header_rows[0])
        expanded_rows: list[list[str]] = []
        for row in header_rows:
            expanded: list[str] = []
            last = ""
            for cell in row:
                if cell:
                    last = cell
                    expanded.append(cell)
                else:
                    expanded.append(last)
            expanded_rows.append(expanded)

        headers: list[str] = []
        for col in range(width):
            parts: list[str] = []
            seen: set[str] = set()
            for row in expanded_rows:
                part = row[col].strip()
                if not part or part in seen:
                    continue
                seen.add(part)
                parts.append(part)
            headers.append(" / ".join(parts) if parts else f"Column {col + 1}")
        return headers

    def _table_row_facts(self, headers: list[str], body: list[list[str]]) -> list[str]:
        if not headers or not body:
            return []
        label_cols = self._table_label_column_count(headers)
        last_labels = [""] * label_cols
        facts: list[str] = []

        for row in body:
            if not any(row):
                continue
            labels: list[str] = []
            for col in range(label_cols):
                if row[col]:
                    last_labels[col] = row[col]
                if last_labels[col]:
                    labels.append(f"{headers[col]}={last_labels[col]}")

            if label_cols >= 2 and not last_labels[label_cols - 1]:
                continue

            values: list[str] = []
            for col in range(label_cols, min(len(headers), len(row))):
                if row[col]:
                    values.append(f"{headers[col]}={row[col]}")
            if not values:
                continue

            prefix = "; ".join(labels) if labels else "row"
            facts.append(f"- {prefix}: {'; '.join(values)}")
        return facts

    def _table_label_column_count(self, headers: list[str]) -> int:
        if len(headers) <= 1:
            return 0
        first = headers[0].lower()
        second = headers[1].lower() if len(headers) > 1 else ""
        label_cols = 1 if any(word in first for word in ("class", "category", "name", "family")) else 0
        if len(headers) > 3 and any(word in second for word in ("family", "subfamily", "group")):
            label_cols = 2
        return min(label_cols, len(headers) - 1)

    def _clean_table_cell(self, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).replace("\r", "\n").replace("\n", " ")
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"(?<=\w)- (?=\w)", "", text)
        text = text.replace("|", "\\|")
        return text.strip()

    def _normalize_repeated_line(self, line: str) -> str:
        line = re.sub(r"\d+", "#", line or "")
        line = re.sub(r"\s+", " ", line)
        return line.strip().lower()

    def _section_id(self, value: str) -> str:
        value = (value or "").strip().strip(".")
        value = re.sub(r"\s+", "-", value)
        value = re.sub(r"[^0-9A-Za-z_.:-]+", "-", value)
        return value.strip("-")[:120] or "section"

    def _page_source_url(self, source_url: str | None, start_page: int, end_page: int) -> str | None:
        if not source_url:
            return None
        if start_page == end_page:
            return f"{source_url}#page={start_page}"
        return f"{source_url}#page={start_page}&page_end={quote(str(end_page))}"

    def _primary_extraction_method(self, methods: list[str]) -> str:
        methods_set = set(methods or [])
        if "pdf_text+ocr" in methods_set or {"pdf_text", "ocr"}.issubset(methods_set):
            return "pdf_text+ocr"
        if "ocr" in methods_set:
            return "ocr"
        if "ocr_failed" in methods_set and len(methods_set) == 1:
            return "ocr_failed"
        return "pdf_text"

    def _safe_pdf_metadata(self, metadata: dict[str, Any]) -> dict[str, str]:
        safe: dict[str, str] = {}
        for key, value in metadata.items():
            if value is None:
                continue
            text = self._clean_text(str(value))
            if text:
                safe[str(key)] = text[:500]
        return safe
