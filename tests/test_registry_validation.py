import sys
from pathlib import Path

import pytest


COMMON_ROOT = Path(__file__).resolve().parent.parent / "services" / "common"
if str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

from common.registry_validation import (  # noqa: E402
    normalize_corpus_id,
    normalize_source_format,
    normalize_source_id,
    validate_source_definition,
)


def test_registry_identifiers_allow_expected_portable_characters():
    assert normalize_corpus_id("tenant:kb.v1_2026-07") == "tenant:kb.v1_2026-07"
    assert normalize_source_id("source_01.pdf") == "source_01.pdf"


@pytest.mark.parametrize("value", ["", "has space", "slash/value", "name?"])
def test_registry_identifiers_reject_empty_or_unsafe_values(value):
    with pytest.raises(ValueError):
        normalize_corpus_id(value)


@pytest.mark.parametrize(
    "value, expected",
    [("HTML", "html"), ("md", "md"), (" markdown ", "markdown"), ("ZIP", "zip"), ("jsonl", "jsonl")],
)
def test_source_format_is_normalized_for_supported_formats(value, expected):
    assert normalize_source_format(value) == expected


def test_validate_source_definition_accepts_http_and_s3_sources():
    validate_source_definition(
        {
            "id": "remote-page",
            "type": "url",
            "format": "html",
            "url": "https://example.test/docs/page",
        }
    )
    validate_source_definition(
        {
            "source_id": "uploaded-pdf",
            "type": "object",
            "format": "pdf",
            "object_uri": "s3://rag-sources/env/tenant/corpus/source/hash/file.pdf",
        }
    )


@pytest.mark.parametrize(
    "source, match",
    [
        ({"id": "local", "type": "url", "format": "html", "url": "/relative/path"}, "absolute http/https"),
        ({"id": "bad-object", "type": "object", "format": "pdf", "object_uri": "file:///tmp/a.pdf"}, "s3://"),
        ({"id": "bad-format", "type": "url", "format": "exe", "url": "https://example.test"}, "unsupported"),
        ({"id": "bad-type", "type": "file", "format": "text", "path": "/tmp/a.txt"}, "unsupported source type"),
    ],
)
def test_validate_source_definition_rejects_unsupported_source_shapes(source, match):
    with pytest.raises(ValueError, match=match):
        validate_source_definition(source)
