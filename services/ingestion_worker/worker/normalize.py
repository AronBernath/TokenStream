import hashlib
import os
import logging
from datetime import UTC, datetime

from common.chunking import chunk_text, make_chat_fn_from_orchestrator
from common.models import Chunk

# Env vars for LLM chunking (when strategy is "llm")
_ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_API_URL", "").strip()
_ORCHESTRATOR_API_KEY = os.environ.get("ORCHESTRATOR_API_KEY") or os.environ.get("API_KEY")
_CHUNKING_LLM_MODEL = os.environ.get("CHUNKING_LLM_MODEL", "openai:gpt-5.1")
logger = logging.getLogger("ingestion-worker.normalize")


def _as_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _block_doc_id(block: dict) -> str:
    return str(block.get("doc_id") or block.get("source_url") or "doc")


def _merge_unique(values):
    out = []
    seen = set()
    for value in values:
        if isinstance(value, (list, tuple, set)):
            candidates = value
        else:
            candidates = [value]
        for candidate in candidates:
            text = str(candidate or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
    return out


def _chunk_section_id(blocks: list[dict], part_idx: int | None = None) -> str | None:
    section_ids = _merge_unique(block.get("section_id") for block in blocks)
    if not section_ids:
        return None
    if len(section_ids) == 1:
        base = section_ids[0]
    else:
        base = f"{section_ids[0]}--{section_ids[-1]}"
    return f"{base}:part_{part_idx}" if part_idx is not None else base


def _combined_metadata(blocks: list[dict], *, piece: str, indexed_at: str, byte_range: dict | None = None) -> dict:
    first_meta = dict((blocks[0].get("metadata") or {}) if blocks else {})
    section_ids = _merge_unique(block.get("section_id") for block in blocks)
    source_urls = _merge_unique(block.get("source_url") for block in blocks)
    parser_versions = _merge_unique((block.get("metadata") or {}).get("parser_version") for block in blocks)
    source_fingerprints = _merge_unique((block.get("metadata") or {}).get("source_fingerprint") for block in blocks)
    source_hashes = _merge_unique((block.get("metadata") or {}).get("source_content_hash") for block in blocks)
    tags = _merge_unique(block.get("tags") or [] for block in blocks)

    metadata = {
        **first_meta,
        "source_block_count": len(blocks),
        "source_section_ids": section_ids,
        "source_urls": source_urls,
        "chunk_hash": hashlib.sha256(piece.encode("utf-8")).hexdigest(),
        "indexed_at": indexed_at,
    }
    if parser_versions:
        metadata["parser_version"] = parser_versions[0] if len(parser_versions) == 1 else parser_versions
    if source_fingerprints:
        metadata["source_fingerprint"] = (
            source_fingerprints[0] if len(source_fingerprints) == 1 else source_fingerprints
        )
    if source_hashes:
        metadata["source_content_hash"] = source_hashes[0] if len(source_hashes) == 1 else source_hashes
    if tags:
        metadata["tags"] = tags
    if byte_range is not None:
        metadata["byte_range"] = byte_range
    return metadata


def _make_chunk(
    *,
    corpus: dict,
    blocks: list[dict],
    piece: str,
    idx: int,
    version_date: str | None,
    byte_range: dict | None = None,
    part_idx: int | None = None,
) -> Chunk:
    corpus_id = corpus["corpus_id"]
    first = blocks[0]
    first_meta = first.get("metadata") or {}
    section_id = _chunk_section_id(blocks, part_idx=part_idx)
    doc_id = _block_doc_id(first)
    indexed_at = datetime.now(UTC).isoformat()
    base = f"{corpus_id}|{doc_id}|{section_id}|{idx}|{piece[:80]}"
    metadata = _combined_metadata(blocks, piece=piece, indexed_at=indexed_at, byte_range=byte_range)
    return Chunk(
        chunk_id=hashlib.sha1(base.encode("utf-8")).hexdigest(),
        corpus_id=corpus_id,
        doc_id=doc_id,
        title=first.get("title", corpus.get("title", "")),
        section_id=section_id,
        version_date=first_meta.get("source_version_date") or first.get("version_date") or version_date,
        language=first.get("language"),
        jurisdiction=corpus.get("jurisdiction"),
        source_url=first.get("source_url"),
        text=piece.strip(),
        metadata={
            "doc_type": first.get("doc_type"),
            "tags": _merge_unique(block.get("tags") or [] for block in blocks),
            **metadata,
        },
    )


def _pack_structural_blocks(blocks: list[dict], target_chars: int) -> list[list[dict]]:
    packed: list[list[dict]] = []
    current: list[dict] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            packed.append(current)
        current = []
        current_len = 0

    for block in blocks:
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        if len(text) > target_chars:
            flush()
            packed.append([block])
            continue
        separator_len = 2 if current else 0
        same_doc = not current or _block_doc_id(current[-1]) == _block_doc_id(block)
        if current and (not same_doc or current_len + separator_len + len(text) > target_chars):
            flush()
        current.append(block)
        current_len += (2 if len(current) > 1 else 0) + len(text)

    flush()
    return packed


def blocks_to_chunks(
    blocks: list,
    corpus: dict,
    version_date: str | None,
    *,
    chat_fn=None,
    pipeline_id: str | None = None,
    chunking_model: str | None = None,
):
    corpus_id = corpus["corpus_id"]
    chunking_config = corpus.get("chunking") or {}
    target = _as_int(chunking_config.get("target_chars", 2200), 2200)
    overlap = _as_int(chunking_config.get("overlap_chars", 250), 250)
    strategy = chunking_config.get("strategy", "llm")
    if strategy != "llm":
        raise ValueError(f"Unsupported ingestion chunking strategy '{strategy}'; only 'llm' is allowed")
    model = str(chunking_model or chunking_config.get("model") or "").strip()
    if not model and not pipeline_id:
        model = _CHUNKING_LLM_MODEL

    # Resolve chat_fn for LLM strategy
    if strategy == "llm" and chat_fn is None:
        chat_fn = make_chat_fn_from_orchestrator(
            orchestrator_api_url=_ORCHESTRATOR_URL,
            api_key=_ORCHESTRATOR_API_KEY,
            model=model,
            pipeline_id=pipeline_id,
            task="chunking",
        )
    if strategy == "llm" and chat_fn is None:
        raise RuntimeError(f"LLM chunking requested for corpus={corpus_id} but orchestrator chunking is unavailable")

    out = []
    for group in _pack_structural_blocks(blocks, target):
        group_text = "\n\n".join(
            str(block.get("text") or "").strip() for block in group if str(block.get("text") or "").strip()
        )
        if not group_text:
            continue
        if len(group) > 1 or len(group_text) <= target:
            byte_range = None
            if len(group) == 1:
                byte_range = {"start": 0, "end": len(group_text)}
            out.append(
                _make_chunk(
                    corpus=corpus,
                    blocks=group,
                    piece=group_text,
                    idx=len(out),
                    version_date=version_date,
                    byte_range=byte_range,
                )
            )
            continue

        block = group[0]
        pieces = chunk_text(
            group_text,
            target_chars=target,
            overlap_chars=overlap,
            strategy=strategy,
            chat_fn=chat_fn,
            use_cache=True,
        )
        cursor = 0
        for part_idx, piece in enumerate(pieces):
            start = -1
            end = -1
            if isinstance(piece, str) and piece:
                start = group_text.find(piece, cursor)
                if start == -1:
                    start = group_text.find(piece)
                if start != -1:
                    end = start + len(piece)
                    cursor = end
            out.append(
                _make_chunk(
                    corpus=corpus,
                    blocks=[block],
                    piece=piece,
                    idx=len(out),
                    version_date=version_date,
                    byte_range={"start": int(start), "end": int(end)} if start != -1 and end != -1 else None,
                    part_idx=part_idx,
                )
            )
    return out
