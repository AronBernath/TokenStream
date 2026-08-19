from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List

_CELEX_RE = re.compile(r"\b3\d{4}[A-Z]\d{4}\b", flags=re.IGNORECASE)
_REGULATION_ID_RE = re.compile(r"\b\d{4}/\d{2,4}\b")
_NIST_CONTROL_RE = re.compile(r"\b[A-Z]{2,4}-\d+(?:\(\d+\))?\b")
_DECIMAL_CONTROL_RE = re.compile(r"\b\d+\.\d+(?:\.\d+)*(?:\.)?\b")
_SECTION_SYMBOL_RE = re.compile(r"\b(\d+[A-Za-z]?)\.\s*§")
_ARTICLE_EN_RE = re.compile(r"\barticle\s+(\d+[A-Za-z]?)\b", flags=re.IGNORECASE)
_ARTICLE_HU_RE = re.compile(r"\b(\d+[A-Za-z]?)\.\s*cikk\b", flags=re.IGNORECASE)
_RECITAL_RE = re.compile(r"\brecital\s+(\d+)\b", flags=re.IGNORECASE)
_ANNEX_EN_RE = re.compile(r"\bannex\s+([IVXLCDM]+|\d+)\b", flags=re.IGNORECASE)
_ANNEX_HU_RE = re.compile(r"\b([IVXLCDM]+|\d+)\.\s*mell[ée]klet\b", flags=re.IGNORECASE)


def normalize_graph_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("§", " section ")
    text = text.replace("_", " ")
    text = re.sub(r"[^\w\s/-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def unique_aliases(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        norm = normalize_graph_text(value)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def extract_reference_aliases(text: str) -> List[str]:
    raw = str(text or "")
    aliases: List[str] = []

    for match in _CELEX_RE.finditer(raw):
        celex = match.group(0).upper()
        aliases.extend([celex, f"celex {celex}"])

    for match in _REGULATION_ID_RE.finditer(raw):
        ref = match.group(0)
        aliases.extend([ref, f"regulation {ref}", f"directive {ref}"])

    for match in _NIST_CONTROL_RE.finditer(raw):
        aliases.append(match.group(0).upper())

    for match in _SECTION_SYMBOL_RE.finditer(raw):
        num = match.group(1)
        aliases.extend([f"section {num}", f"{num} section", f"{num}. §"])

    for match in _ARTICLE_EN_RE.finditer(raw):
        num = match.group(1)
        aliases.extend([f"article {num}", f"{num} article"])

    for match in _ARTICLE_HU_RE.finditer(raw):
        num = match.group(1)
        aliases.extend([f"article {num}", f"{num}. cikk"])

    for match in _RECITAL_RE.finditer(raw):
        num = match.group(1)
        aliases.extend([f"recital {num}", f"({num})"])

    for match in _ANNEX_EN_RE.finditer(raw):
        annex = match.group(1).upper()
        aliases.extend([f"annex {annex}", annex])

    for match in _ANNEX_HU_RE.finditer(raw):
        annex = match.group(1).upper()
        aliases.extend([f"annex {annex}", f"{annex}. melléklet"])

    for match in _DECIMAL_CONTROL_RE.finditer(raw):
        code = match.group(0).rstrip(".")
        if code.count(".") >= 1:
            aliases.extend([code, f"control {code}"])

    return unique_aliases(aliases)


def extract_query_aliases(query: str) -> List[str]:
    aliases = extract_reference_aliases(query)
    full = normalize_graph_text(query)
    if full:
        aliases = unique_aliases([full, *aliases])
    return aliases
