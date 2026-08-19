from __future__ import annotations

import json
from typing import Any, Mapping


STANDARD_FILTER_FIELDS = frozenset(
    {
        "chunk_id",
        "doc_id",
        "doc_type",
        "section_id",
        "version_date",
        "jurisdiction",
        "language",
        "source_url",
        "tags",
    }
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _retrieval_profile_config(corpus: Mapping[str, Any] | None) -> dict[str, Any]:
    profile = _as_dict((corpus or {}).get("retrieval_profile"))
    if profile and not bool(profile.get("enabled", True)):
        return {}
    return _as_dict(profile.get("config"))


def get_retrieval_config(corpus: Mapping[str, Any] | None) -> dict[str, Any]:
    config = _deep_merge({}, _retrieval_profile_config(corpus))
    config = _deep_merge(config, _as_dict((corpus or {}).get("retrieval_config")))
    return config


def merge_default_filters(
    corpus: Mapping[str, Any] | None, requested_filters: Mapping[str, Any] | None
) -> dict[str, Any]:
    config = get_retrieval_config(corpus)
    merged = dict(_as_dict(config.get("default_filters")))
    merged.update(_as_dict(requested_filters))
    return merged


def invalid_filter_fields(corpus: Mapping[str, Any] | None, filters: Mapping[str, Any] | None) -> list[str]:
    config = get_retrieval_config(corpus)
    if not bool(config.get("strict_filters")):
        return []
    allowed = set(STANDARD_FILTER_FIELDS)
    allowed.update(str(field) for field in _as_list(config.get("filterable_fields")) if str(field).strip())
    allowed.update(_as_dict(config.get("default_filters")).keys())
    return sorted(str(field) for field in _as_dict(filters).keys() if str(field) not in allowed)


def retrieval_config_fields(corpus: Mapping[str, Any] | None, key: str) -> list[str]:
    config = get_retrieval_config(corpus)
    fields: list[str] = []
    seen: set[str] = set()
    for field in _as_list(config.get(key)):
        text = str(field or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        fields.append(text)
    return fields


def _metadata_from_chunk(chunk: Any) -> dict[str, Any]:
    if isinstance(chunk, dict):
        return _as_dict(chunk.get("metadata"))
    return _as_dict(getattr(chunk, "metadata", None))


def chunk_field_value(chunk: Any, field: str) -> Any:
    if isinstance(chunk, dict):
        if field in chunk and chunk[field] is not None:
            return chunk[field]
    else:
        value = getattr(chunk, field, None)
        if value is not None:
            return value
    return _metadata_from_chunk(chunk).get(field)


def _flatten_lexical_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_flatten_lexical_value(item))
        return out
    if isinstance(value, dict):
        return [json.dumps(value, sort_keys=True, ensure_ascii=False)]
    text = str(value).strip()
    return [text] if text else []


def lexical_field_values(corpus: Mapping[str, Any] | None, chunk: Any) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for field in retrieval_config_fields(corpus, "lexical_fields"):
        for value in _flatten_lexical_value(chunk_field_value(chunk, field)):
            if value in seen:
                continue
            seen.add(value)
            values.append(value)
    return values


def build_citation(chunk: Any, corpus: Mapping[str, Any] | None) -> dict[str, Any]:
    citation = {
        "title": chunk_field_value(chunk, "title"),
        "section_id": chunk_field_value(chunk, "section_id"),
        "version_date": chunk_field_value(chunk, "version_date"),
        "source_url": chunk_field_value(chunk, "source_url"),
    }
    for field in retrieval_config_fields(corpus, "citation_fields"):
        value = chunk_field_value(chunk, field)
        if value is None:
            continue
        citation[field] = value
    return citation
