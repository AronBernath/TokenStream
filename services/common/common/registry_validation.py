"""Shared validation rules for Dynamic RAG Registry identifiers and sources."""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlparse

IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9._:-]+")
SUPPORTED_SOURCE_TYPES = frozenset({"url", "object"})
SUPPORTED_SOURCE_FORMATS = frozenset(
    {"html", "xlsx", "pdf", "yaml", "markdown", "md", "text", "json", "jsonl", "zip", "tar", "binary"}
)


def normalize_registry_id(value: Any, *, field_name: str) -> str:
    """Return a safe stable resource identifier or raise ValueError."""
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    if not IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field_name} may only contain letters, digits, dot, underscore, colon, or hyphen")
    return normalized


def normalize_corpus_id(value: Any) -> str:
    return normalize_registry_id(value, field_name="corpus_id")


def normalize_source_id(value: Any) -> str:
    return normalize_registry_id(value, field_name="source_id")


def normalize_source_format(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in SUPPORTED_SOURCE_FORMATS:
        raise ValueError(f"unsupported source format: {normalized}")
    return normalized


def validate_source_definition(source: Mapping[str, Any]) -> None:
    """Validate the registry fields shared by file-backed and DB-backed sources."""
    normalize_source_id(source.get("source_id", source.get("id")))
    source_type = str(source.get("type") or "").strip()
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError(f"unsupported source type: {source_type}")
    normalize_source_format(source.get("format", "html"))

    if source_type == "url":
        url = str(source.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL sources require an absolute http/https URL")
    elif source_type == "object":
        object_uri = str(source.get("object_uri") or "").strip()
        if not object_uri:
            raise ValueError("object sources require object_uri")
        if not object_uri.startswith("s3://"):
            raise ValueError("object sources require an s3:// object_uri")
