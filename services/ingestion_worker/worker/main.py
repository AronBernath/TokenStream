import os
import argparse
import sys
import logging
from pathlib import Path
from typing import Any, Callable

# The image sets PYTHONPATH=/app/common.  Add the same source root for direct
# local execution and tests before importing worker modules that use common.
_COMMON_ROOT = Path(__file__).resolve().parents[2] / "common"
if str(_COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(_COMMON_ROOT))

from common.logging_config import configure_logging
from worker.fetchers import fetch_source
from worker.parsers import PARSER_VERSION, parse_to_blocks
from worker.normalize import blocks_to_chunks
from worker.processors import (
    ProcessorContext,
    ProcessorResult,
    processor_config_hash,
    resolve_processor_config,
    resolve_processor_id,
    run_processor,
    source_processing_fingerprint,
)
from worker.embed import embed_texts
from worker.indexers import (
    ensure_indexes,
    delete_corpus_source_artifacts,
    _qdrant_collection_name,
    upsert_qdrant,
    upsert_lexical,
    get_corpus_source_hashes,
)
from common.registry_validation import (
    normalize_corpus_id,
    validate_source_definition,
)

from worker.registry_client import get_corpus, list_corpus_sources, list_processors

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DEFAULT_INGESTION_PIPELINE_ID = os.environ.get("INGESTION_PIPELINE_ID", "default").strip() or None
configure_logging("ingestion-worker")
logger = logging.getLogger("ingestion-worker.pipeline")


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    idx = min(len(values) - 1, max(0, round((len(values) - 1) * ratio)))
    return values[idx]


def _chunk_quality(chunks: list) -> dict[str, Any]:
    lengths = sorted(len(chunk.text or "") for chunk in chunks)
    if not lengths:
        return {
            "chunks": 0,
            "min_chars": 0,
            "avg_chars": 0,
            "p50_chars": 0,
            "p95_chars": 0,
            "max_chars": 0,
            "empty_chunks": 0,
            "very_small_chunks": 0,
            "oversized_chunks": 0,
            "chunks_with_section_id": 0,
            "chunks_missing_section_id": 0,
            "sections_covered": 0,
        }
    section_ids = {chunk.section_id for chunk in chunks if chunk.section_id}
    return {
        "chunks": len(chunks),
        "min_chars": lengths[0],
        "avg_chars": round(sum(lengths) / len(lengths), 1),
        "p50_chars": _percentile(lengths, 0.5),
        "p95_chars": _percentile(lengths, 0.95),
        "max_chars": lengths[-1],
        "empty_chunks": sum(1 for length in lengths if length <= 0),
        "very_small_chunks": sum(1 for length in lengths if 0 < length < 300),
        "oversized_chunks": sum(1 for length in lengths if length > 4500),
        "chunks_with_section_id": sum(1 for chunk in chunks if chunk.section_id),
        "chunks_missing_section_id": sum(1 for chunk in chunks if not chunk.section_id),
        "sections_covered": len(section_ids),
    }


def _chunk_preview(chunks: list, *, limit: int = 5, max_chars: int = 1200) -> list[dict[str, Any]]:
    preview = []
    for idx, chunk in enumerate(chunks[: max(0, limit)]):
        text = chunk.text or ""
        preview.append(
            {
                "index": idx,
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "title": chunk.title,
                "section_id": chunk.section_id,
                "chars": len(text),
                "source_url": chunk.source_url,
                "text_preview": text[:max_chars],
                "truncated": len(text) > max_chars,
            }
        )
    return preview


def _index_stats(
    *,
    collection_name: str,
    qdrant_count: int | None = None,
    lexical_count: int | None = None,
    lexical_path: str | None = None,
) -> dict[str, Any]:
    return {
        "qdrant": {
            "status": "ok" if qdrant_count is not None else "not_run",
            "collection": collection_name,
            "points_upserted": qdrant_count or 0,
        },
        "lexical": {
            "status": "ok" if lexical_count is not None else "not_run",
            "rows_written": lexical_count or 0,
            "path": lexical_path,
        },
    }


def _normalize_id_list(values: list[str] | None) -> set[str]:
    out: set[str] = set()
    if not values:
        return out
    for value in values:
        if not isinstance(value, str):
            continue
        raw = value.strip()
        if raw:
            out.add(raw)
    return out


def _source_doc_id(src: dict) -> str:
    return str(src.get("id") or src.get("doc_id") or src.get("url") or "doc").strip()


def validate_corpus_tree(corpus: dict) -> list[str]:
    errors = []
    try:
        normalize_corpus_id(corpus.get("corpus_id"))
    except ValueError as exc:
        errors.append(str(exc))

    sources = corpus.get("sources")
    if not isinstance(sources, list):
        errors.append("'sources' must be a list")
    else:
        for i, src in enumerate(sources):
            if not isinstance(src, dict):
                errors.append(f"Source at index {i} is not a dictionary")
                continue

            try:
                validate_source_definition(src)
            except ValueError as exc:
                errors.append(f"Source at index {i}: {exc}")

    return errors


def load_corpus(corpus_id: str) -> dict:
    try:
        corpus_detail = get_corpus(corpus_id)
        sources = list_corpus_sources(corpus_id)
        processors = list_processors()
    except Exception as exc:
        raise FileNotFoundError(f"Failed to load corpus '{corpus_id}' from registry: {exc}") from exc
    processor_registry = {
        str(record.get("processor_id") or "").strip(): record
        for record in processors
        if isinstance(record, dict) and str(record.get("processor_id") or "").strip()
    }
    retrieval_profile_id = str(corpus_detail.get("retrieval_profile_id") or "").strip()

    corpus = {
        "corpus_id": corpus_detail["corpus_id"],
        "title": corpus_detail.get("title"),
        "description": corpus_detail.get("description"),
        "environment": corpus_detail.get("environment"),
        "tenant_id": corpus_detail.get("tenant_id"),
        "chunking": corpus_detail.get("chunking", {}),
        "index": corpus_detail.get("index", {}),
        "processor_id": corpus_detail.get("processor_id"),
        "processor_config": corpus_detail.get("processor_config", {}),
        "processor_registry": processor_registry,
        "retrieval_profile_id": retrieval_profile_id or None,
        "retrieval_config": corpus_detail.get("retrieval_config", {}),
        "metadata": corpus_detail.get("metadata", {}),
        "sources": sources,
        "rules": {},  # rules are no longer supported via registry
    }

    errors = validate_corpus_tree(corpus)
    if errors:
        raise ValueError(f"Corpus validation failed for '{corpus_id}': " + "; ".join(errors))
    return corpus


def purge_source_artifacts(corpus_id: str, source_id: str) -> dict[str, Any]:
    corpus = load_corpus(corpus_id)
    deleted_chunks, deleted_qdrant_points = delete_corpus_source_artifacts(corpus=corpus, source_id=source_id)
    return {
        "status": "purged",
        "corpus_id": corpus["corpus_id"],
        "source_id": source_id,
        "deleted_chunks": deleted_chunks,
        "deleted_qdrant_points": deleted_qdrant_points,
    }


def plan_corpus_load(
    corpus_id: str,
    source_ids: list[str] | None = None,
    doc_ids: list[str] | None = None,
    force_reembed: bool = False,
    processor_id: str | None = None,
    processor_config: dict[str, Any] | None = None,
) -> dict:
    corpus = load_corpus(corpus_id)
    normalized_source_ids = _normalize_id_list(source_ids)
    normalized_doc_ids = _normalize_id_list(doc_ids)
    selective = bool(normalized_source_ids or normalized_doc_ids)

    existing_hashes = get_corpus_source_hashes(corpus)

    to_embed_doc_ids = set()
    skipped_doc_ids = set()

    for src in corpus.get("sources", []):
        src_id = _source_doc_id(src)

        if selective and src_id not in normalized_source_ids and src_id not in normalized_doc_ids:
            continue

        if force_reembed:
            to_embed_doc_ids.add(src_id)
            continue

        # Try to fetch the source to get its hash
        try:
            raw = fetch_source(src, data_dir=DATA_DIR)
            content_hash = raw.get("content_hash")
            resolved_processor_id = resolve_processor_id(corpus, src, processor_id)
            resolved_processor_config = resolve_processor_config(
                corpus,
                src,
                processor_config,
                processor_id=resolved_processor_id,
            )
            fingerprint = source_processing_fingerprint(
                content_hash,
                resolved_processor_id,
                resolved_processor_config,
            )
            if not fingerprint:
                to_embed_doc_ids.add(src_id)
            elif existing_hashes.get(src_id) != fingerprint:
                to_embed_doc_ids.add(src_id)
            else:
                skipped_doc_ids.add(src_id)
        except Exception as e:
            logger.warning("Failed to fetch source %s during planning: %s", src_id, e)
            to_embed_doc_ids.add(src_id)

    return {
        "corpus_id": corpus_id,
        "to_embed_doc_ids": list(to_embed_doc_ids),
        "skipped_doc_ids": list(skipped_doc_ids),
    }


def _default_processor(context: ProcessorContext) -> ProcessorResult:
    blocks = parse_to_blocks(context.raw, context.source, context.corpus, rules=context.corpus.get("rules", {}))
    chunks = blocks_to_chunks(
        blocks,
        context.corpus,
        version_date=context.version_date,
        pipeline_id=context.pipeline_id,
        chunking_model=context.chunking_model,
    )
    return ProcessorResult(
        chunks=chunks,
        blocks_parsed=len(blocks),
        stats={"parser_name": "generic-core", "parser_version": PARSER_VERSION},
    )


def run_ingest(
    corpus_id: str,
    version_date: str | None = None,
    *,
    pipeline_id: str | None = None,
    source_ids: list[str] | None = None,
    doc_ids: list[str] | None = None,
    force_reembed: bool = False,
    chunking_model: str | None = None,
    processor_id: str | None = None,
    processor_config: dict[str, Any] | None = None,
    check_cancelled: Callable[[], bool] | None = None,
    report_progress: Callable[[dict[str, Any]], None] | None = None,
):
    pipeline_id = (pipeline_id or DEFAULT_INGESTION_PIPELINE_ID or "").strip() or None
    chunking_model = (chunking_model or "").strip() or None

    def progress(**stats: Any) -> None:
        if report_progress:
            report_progress(stats)

    logger.info(
        "Starting ingestion for corpus_id=%s pipeline_id=%s chunking_model=%s processor_id=%s",
        corpus_id,
        pipeline_id or "<none>",
        chunking_model or "<policy-default>",
        processor_id or "<resolved-per-source>",
    )
    corpus = load_corpus(corpus_id)
    corpus_id = corpus["corpus_id"]
    logger.info("Loaded corpus %s with %d source(s)", corpus_id, len(corpus.get("sources", [])))
    progress(stage="initializing", sources_total=len(corpus.get("sources", [])))

    ensure_indexes(corpus)
    logger.info("Initialized lexical index for corpus %s", corpus_id)

    normalized_source_ids = _normalize_id_list(source_ids)
    normalized_doc_ids = _normalize_id_list(doc_ids)
    selective = bool(normalized_source_ids or normalized_doc_ids)

    existing_hashes = get_corpus_source_hashes(corpus)

    all_chunks = []
    failed_sources = []
    skipped_unchanged = []
    source_reports: list[dict[str, Any]] = []
    total_sources = len(corpus["sources"])
    matched_sources = 0
    for i, src in enumerate(corpus["sources"], start=1):
        src_id = _source_doc_id(src)
        should_process = True
        if selective:
            should_process = src_id in normalized_source_ids or src_id in normalized_doc_ids

        if not should_process:
            logger.info("Skipping source %s on selective ingest", src_id)
            continue
        matched_sources += 1

        if check_cancelled and check_cancelled():
            logger.warning("Ingestion cancelled for corpus %s", corpus_id)
            raise InterruptedError("Job cancelled")

        try:
            logger.info("Source %d/%d: fetching %s", i, total_sources, src_id)
            progress(
                stage="processing_source",
                source_id=src_id,
                sources_total=total_sources,
                sources_completed=i - 1,
            )
            raw = fetch_source(src, data_dir=DATA_DIR)

            content_hash = raw.get("content_hash")
            resolved_processor_id = resolve_processor_id(corpus, src, processor_id)
            resolved_processor_config = resolve_processor_config(
                corpus,
                src,
                processor_config,
                processor_id=resolved_processor_id,
            )
            resolved_processor_config_hash = processor_config_hash(resolved_processor_config)
            fingerprint = source_processing_fingerprint(
                content_hash,
                resolved_processor_id,
                resolved_processor_config,
            )
            if not force_reembed and fingerprint and existing_hashes.get(src_id) == fingerprint:
                logger.info("Skipping source %s because source fingerprint is unchanged", src_id)
                skipped_unchanged.append(src_id)
                source_reports.append(
                    {
                        "source_id": src_id,
                        "status": "skipped_unchanged",
                        "content_hash": content_hash,
                        "source_fingerprint": fingerprint,
                        "processor_id": resolved_processor_id,
                        "processor_config_hash": resolved_processor_config_hash,
                        "blocks_parsed": 0,
                        "chunks_produced": 0,
                    }
                )
                continue

            content = raw.get("content")
            content_len = len(content) if isinstance(content, (bytes, str)) else -1
            logger.info(
                "Fetched source %s format=%s content_bytes=%d local_path=%s",
                src_id,
                raw.get("format"),
                content_len,
                raw.get("local_path"),
            )
            result = run_processor(
                ProcessorContext(
                    corpus=corpus,
                    source=src,
                    raw=raw,
                    version_date=version_date,
                    pipeline_id=pipeline_id,
                    chunking_model=chunking_model,
                    processor_id=resolved_processor_id,
                    processor_config=resolved_processor_config,
                    processor_registry=corpus.get("processor_registry", {}),
                ),
                _default_processor,
            )
            chunks = result.chunks
            logger.info(
                "Processor %s parsed %d blocks and created %d chunks from %s",
                resolved_processor_id,
                result.blocks_parsed,
                len(chunks),
                src_id,
            )
            logger.info("Created %d chunks from %s", len(chunks), src_id)
            if not chunks:
                raise RuntimeError(f"Source {src_id} produced no chunks")

            deleted_chunks = 0
            try:
                deleted_chunks, _ = delete_corpus_source_artifacts(corpus=corpus, source_id=src_id)
                logger.info("Deleted prior document artifacts for %s: chunks=%d", src_id, deleted_chunks)
            except Exception:
                logger.exception("Failed delete before re-ingest for source %s", src_id)

            all_chunks.extend(chunks)
            source_reports.append(
                {
                    "source_id": src_id,
                    "status": "processed",
                    "content_hash": content_hash,
                    "source_fingerprint": fingerprint,
                    "processor_id": resolved_processor_id,
                    "processor_config_hash": resolved_processor_config_hash,
                    "content_bytes": content_len,
                    "blocks_parsed": result.blocks_parsed,
                    "chunks_produced": len(chunks),
                    "deleted_prior_chunks": deleted_chunks,
                    "processor_stats": result.stats,
                    "chunk_quality": _chunk_quality(chunks),
                    "chunk_preview": _chunk_preview(chunks, limit=3, max_chars=800),
                }
            )
            progress(
                stage="processing_source",
                source_id=src_id,
                sources_total=total_sources,
                sources_completed=i,
                chunks_produced=len(all_chunks),
            )
        except Exception:
            failed_sources.append(src_id)
            logger.exception("Source processing failed for %s", src_id)
            source_reports.append(
                {
                    "source_id": src_id,
                    "status": "failed",
                    "chunks_produced": 0,
                }
            )
            progress(
                stage="processing_source",
                source_id=src_id,
                sources_total=total_sources,
                sources_completed=i,
                sources_failed=len(failed_sources),
                chunks_produced=len(all_chunks),
            )

    if selective and matched_sources == 0:
        logger.warning(
            "No matching sources processed for corpus=%s with filters source_ids=%s doc_ids=%s",
            corpus_id,
            sorted(normalized_source_ids),
            sorted(normalized_doc_ids),
        )
        return {"status": "skipped", "reason": "no_matching_sources"}

    if failed_sources:
        logger.warning(
            "Failed sources: %d/%d (%s)",
            len(failed_sources),
            len(corpus["sources"]),
            ", ".join(failed_sources[:10]),
        )
    logger.info(
        "Finished source loop: %d/%d successful, %d skipped unchanged, total_chunks=%d",
        total_sources - len(failed_sources) - len(skipped_unchanged),
        total_sources,
        len(skipped_unchanged),
        len(all_chunks),
    )

    if not all_chunks:
        logger.warning("No chunks were produced for corpus %s; skipping embeddings/index upserts", corpus_id)
        if failed_sources and len(failed_sources) == matched_sources:
            raise RuntimeError(f"All sources failed for corpus {corpus_id}: {', '.join(failed_sources[:10])}")
        if not skipped_unchanged:
            raise RuntimeError(f"No chunks were produced for corpus {corpus_id}")
        return {
            "status": "completed",
            "sources_processed": total_sources - len(failed_sources) - len(skipped_unchanged),
            "sources_failed": len(failed_sources),
            "sources_skipped_unchanged": len(skipped_unchanged),
            "chunks_produced": 0,
            "sources": source_reports,
            "chunk_quality": _chunk_quality([]),
            "chunk_preview": [],
            "indexes": _index_stats(collection_name=_qdrant_collection_name(corpus)),
        }

    if check_cancelled and check_cancelled():
        logger.warning("Ingestion cancelled for corpus %s", corpus_id)
        raise InterruptedError("Job cancelled")

    try:
        logger.info("Embedding %d chunk texts", len(all_chunks))
        progress(
            stage="embedding",
            sources_total=total_sources,
            sources_processed=total_sources - len(failed_sources) - len(skipped_unchanged),
            sources_failed=len(failed_sources),
            sources_skipped_unchanged=len(skipped_unchanged),
            chunks_produced=len(all_chunks),
        )
        vectors = embed_texts([c.text for c in all_chunks])
        logger.info("Embedded %d vectors", len(vectors))

        collection_name = _qdrant_collection_name(corpus)
        logger.info("Ensuring/upserting Qdrant collection %s", collection_name)
        progress(stage="indexing_vectors", chunks_produced=len(all_chunks))
        qdrant_count = upsert_qdrant(corpus, all_chunks, vectors)
        logger.info(
            "Upserted %d points into Qdrant collection %s",
            qdrant_count,
            collection_name,
        )

        progress(stage="indexing_lexical", chunks_produced=len(all_chunks), qdrant_points=qdrant_count)
        lexical_count, lexical_path = upsert_lexical(corpus, all_chunks)
        logger.info("Wrote %d rows to SQLite lexical index %s", lexical_count, lexical_path)
    except Exception:
        logger.exception("Embedding/indexing stage failed for corpus %s", corpus_id)
        raise

    logger.info("Ingested %d chunks into %s", len(all_chunks), corpus_id)
    return {
        "status": "completed",
        "sources_processed": total_sources - len(failed_sources) - len(skipped_unchanged),
        "sources_failed": len(failed_sources),
        "sources_skipped_unchanged": len(skipped_unchanged),
        "chunks_produced": len(all_chunks),
        "qdrant_points": qdrant_count,
        "sqlite_rows": lexical_count,
        "sources": source_reports,
        "chunk_quality": _chunk_quality(all_chunks),
        "chunk_preview": _chunk_preview(all_chunks),
        "indexes": _index_stats(
            collection_name=collection_name,
            qdrant_count=qdrant_count,
            lexical_count=lexical_count,
            lexical_path=lexical_path,
        ),
    }


def dry_run_chunking(
    *,
    corpus_id: str,
    source_id: str | None = None,
    version_date: str | None = None,
    pipeline_id: str | None = None,
    chunking_model: str | None = None,
    processor_id: str | None = None,
    processor_config: dict[str, Any] | None = None,
    max_preview_chunks: int = 5,
):
    pipeline_id = (pipeline_id or DEFAULT_INGESTION_PIPELINE_ID or "").strip() or None
    chunking_model = (chunking_model or "").strip() or None
    max_preview_chunks = max(1, min(int(max_preview_chunks or 5), 20))

    corpus = load_corpus(corpus_id)
    corpus_id = corpus["corpus_id"]
    sources = corpus.get("sources", [])
    if not sources:
        raise RuntimeError(f"Corpus {corpus_id} has no sources")

    selected_source = None
    normalized_source_id = (source_id or "").strip()
    for src in sources:
        src_id = _source_doc_id(src)
        if not normalized_source_id or src_id == normalized_source_id:
            selected_source = src
            break
    if selected_source is None:
        raise RuntimeError(f"Source {normalized_source_id} not found in corpus {corpus_id}")

    src_id = _source_doc_id(selected_source)
    raw = fetch_source(selected_source, data_dir=DATA_DIR)
    content_hash = raw.get("content_hash")
    content = raw.get("content")
    content_len = len(content) if isinstance(content, (bytes, str)) else -1
    resolved_processor_id = resolve_processor_id(corpus, selected_source, processor_id)
    resolved_processor_config = resolve_processor_config(
        corpus,
        selected_source,
        processor_config,
        processor_id=resolved_processor_id,
    )
    result = run_processor(
        ProcessorContext(
            corpus=corpus,
            source=selected_source,
            raw=raw,
            version_date=version_date,
            pipeline_id=pipeline_id,
            chunking_model=chunking_model,
            processor_id=resolved_processor_id,
            processor_config=resolved_processor_config,
            processor_registry=corpus.get("processor_registry", {}),
        ),
        _default_processor,
    )
    chunks = result.chunks
    collection_name = _qdrant_collection_name(corpus)
    return {
        "status": "dry_run_completed",
        "corpus_id": corpus_id,
        "source_id": src_id,
        "parser": {
            "name": result.stats.get("parser_name") or resolved_processor_id,
            "version": result.stats.get("parser_version"),
        },
        "processor_id": resolved_processor_id,
        "processor_config_hash": processor_config_hash(resolved_processor_config),
        "chunking_model": chunking_model or "<policy-default>",
        "content_hash": content_hash,
        "source_fingerprint": source_processing_fingerprint(
            content_hash,
            resolved_processor_id,
            resolved_processor_config,
        ),
        "content_bytes": content_len,
        "blocks_parsed": result.blocks_parsed,
        "chunks_produced": len(chunks),
        "chunk_quality": _chunk_quality(chunks),
        "chunk_preview": _chunk_preview(chunks, limit=max_preview_chunks, max_chars=1600),
        "indexes": {
            "qdrant": {
                "status": "dry_run_not_written",
                "collection": collection_name,
                "points_upserted": 0,
            },
            "lexical": {
                "status": "dry_run_not_written",
                "rows_written": 0,
                "path": None,
            },
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--version-date", default=None)
    ap.add_argument("--source-id", action="append", default=None)
    ap.add_argument("--doc-id", action="append", default=None)
    args = ap.parse_args()
    run_ingest(args.corpus, args.version_date, source_ids=args.source_id, doc_ids=args.doc_id)


if __name__ == "__main__":
    main()
