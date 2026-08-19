from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from common.models import Chunk
from worker.structured_archive_processor import process_structured_archive

DEFAULT_PROCESSOR_ID = "default"
GENERIC_PROCESSOR_TYPES = {"default", "generic", "generic-core", "generic_core"}
STRUCTURED_ARCHIVE_PROCESSOR_TYPES = {"structured_archive", "structured-archive"}


@dataclass
class ProcessorContext:
    corpus: dict[str, Any]
    source: dict[str, Any]
    raw: dict[str, Any]
    version_date: str | None
    pipeline_id: str | None
    chunking_model: str | None
    processor_id: str
    processor_config: dict[str, Any] = field(default_factory=dict)
    processor_registry: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessorResult:
    chunks: list[Chunk]
    blocks_parsed: int = 0
    stats: dict[str, Any] = field(default_factory=dict)


DefaultProcessorCallable = Callable[[ProcessorContext], ProcessorResult]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _json_fingerprint(value: dict[str, Any]) -> str:
    payload = json.dumps(value or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def resolve_processor_id(
    corpus: dict[str, Any],
    source: dict[str, Any],
    requested_processor_id: str | None = None,
) -> str:
    return (
        str(requested_processor_id or "").strip()
        or str(source.get("processor_id") or "").strip()
        or str(corpus.get("processor_id") or "").strip()
        or DEFAULT_PROCESSOR_ID
    )


def resolve_processor_config(
    corpus: dict[str, Any],
    source: dict[str, Any],
    requested_processor_config: dict[str, Any] | None = None,
    *,
    processor_id: str | None = None,
) -> dict[str, Any]:
    resolved_processor_id = processor_id or resolve_processor_id(corpus, source)
    processor_record = _processor_record(corpus, resolved_processor_id)
    config = _deep_merge({}, _as_dict(processor_record.get("config")))
    config = _deep_merge(config, _as_dict(corpus.get("processor_config")))
    config = _deep_merge(config, _as_dict(source.get("processor_config")))
    config = _deep_merge(config, _as_dict(requested_processor_config))
    return config


def processor_config_hash(processor_config: dict[str, Any] | None) -> str:
    return _json_fingerprint(_as_dict(processor_config))


def source_processing_fingerprint(
    content_hash: str | None,
    processor_id: str,
    processor_config: dict[str, Any] | None,
) -> str | None:
    content_hash = str(content_hash or "").strip()
    if not content_hash:
        return None
    config = _as_dict(processor_config)
    if processor_id == DEFAULT_PROCESSOR_ID and not config:
        from worker.parsers import source_fingerprint

        return source_fingerprint(content_hash)
    return f"processor:{processor_id}:{processor_config_hash(config)}:{content_hash}"


def annotate_processor_metadata(
    chunks: list[Chunk],
    *,
    registry_source_id: str,
    processor_id: str,
    processor_config: dict[str, Any],
    content_hash: str | None,
) -> None:
    fingerprint = source_processing_fingerprint(content_hash, processor_id, processor_config)
    config_hash = processor_config_hash(processor_config)
    for chunk in chunks:
        metadata = dict(chunk.metadata or {})
        metadata["registry_source_id"] = registry_source_id
        metadata["ingestion_processor_id"] = processor_id
        metadata["ingestion_processor_config_hash"] = config_hash
        if content_hash:
            metadata.setdefault("source_content_hash", content_hash)
        if fingerprint:
            metadata["source_fingerprint"] = fingerprint
        chunk.metadata = metadata


def _processor_record(corpus: dict[str, Any] | None, processor_id: str) -> dict[str, Any]:
    registry = _as_dict((corpus or {}).get("processor_registry"))
    record = registry.get(processor_id)
    return _as_dict(record)


def _context_processor_record(context: ProcessorContext) -> dict[str, Any]:
    registry = context.processor_registry or _as_dict(context.corpus.get("processor_registry"))
    return _as_dict(registry.get(context.processor_id))


def _processor_type(record: dict[str, Any] | None) -> str:
    return str(_as_dict(record).get("type") or "generic").strip().lower()


def _run_registered_processor(
    context: ProcessorContext,
    default_processor: DefaultProcessorCallable,
) -> ProcessorResult | dict[str, Any] | Iterable[Chunk]:
    registry = context.processor_registry or _as_dict(context.corpus.get("processor_registry"))
    record = _as_dict(registry.get(context.processor_id))
    if not record:
        raise RuntimeError(
            f"Unknown ingestion processor '{context.processor_id}'. "
            f"Publish a processor registry object or use '{DEFAULT_PROCESSOR_ID}'."
        )
    if not bool(record.get("enabled", True)):
        raise RuntimeError(f"Ingestion processor '{context.processor_id}' is disabled")

    processor_type = _processor_type(record)
    if processor_type in GENERIC_PROCESSOR_TYPES:
        return default_processor(context)
    if processor_type in STRUCTURED_ARCHIVE_PROCESSOR_TYPES:
        return process_structured_archive(context)
    raise RuntimeError(f"Unsupported ingestion processor type for '{context.processor_id}': {processor_type}")


def _coerce_chunks(value: Any) -> list[Chunk]:
    if not isinstance(value, list):
        value = list(value or [])
    chunks: list[Chunk] = []
    for item in value:
        chunks.append(item if isinstance(item, Chunk) else Chunk.model_validate(item))
    return chunks


def _coerce_result(result: ProcessorResult | dict[str, Any] | Iterable[Chunk]) -> ProcessorResult:
    if isinstance(result, ProcessorResult):
        result.chunks = _coerce_chunks(result.chunks)
        return result
    if isinstance(result, dict):
        return ProcessorResult(
            chunks=_coerce_chunks(result.get("chunks") or []),
            blocks_parsed=int(result.get("blocks_parsed") or 0),
            stats=_as_dict(result.get("stats")),
        )
    return ProcessorResult(chunks=_coerce_chunks(result), blocks_parsed=0, stats={})


def run_processor(context: ProcessorContext, default_processor: DefaultProcessorCallable) -> ProcessorResult:
    if context.processor_id == DEFAULT_PROCESSOR_ID and not _context_processor_record(context):
        result = default_processor(context)
    else:
        result = _run_registered_processor(context, default_processor)
    coerced = _coerce_result(result)
    annotate_processor_metadata(
        coerced.chunks,
        registry_source_id=str(
            context.source.get("id") or context.source.get("source_id") or context.source.get("doc_id") or ""
        ),
        processor_id=context.processor_id,
        processor_config=context.processor_config,
        content_hash=context.raw.get("content_hash"),
    )
    return coerced
