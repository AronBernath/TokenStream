"""
Shared text chunking utilities for ingestion and retrieval workflows.

Supports:
- LLM-based sentence-aware chunking - respects sentence boundaries and logical groupings

The LLM chunking is callable outside the ingestion pipeline for tools that need
the same chunking behavior without running a full ingestion job.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import threading
from collections import OrderedDict
from typing import Callable

logger = logging.getLogger(__name__)
CHUNKING_PROMPT_VERSION = "json-offsets-v3"


class ChunkingError(RuntimeError):
    """Raised when LLM-based chunking fails or returns invalid output."""


_CHUNKING_RESPONSE_FORMAT = {
    "type": "json_object",
}


def _parse_chunking_json(response: str) -> dict:
    text = (response or "").strip()
    if not text:
        raise ChunkingError("LLM returned empty response for chunking")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        preview = text[:300].replace("\n", " ")
        logger.error("LLM returned invalid JSON for chunking preview=%r", preview)
        raise ChunkingError("LLM returned invalid JSON for chunking") from exc

    if not isinstance(parsed, dict):
        parsed_type = type(parsed).__name__
        logger.error("LLM chunking returned non-object JSON parsed_type=%s", parsed_type)
        raise ChunkingError("LLM chunking returned non-object JSON")
    return parsed


def _is_offset_item(item: object) -> bool:
    return isinstance(item, dict) and isinstance(item.get("start"), int) and isinstance(item.get("end"), int)


# Default target size guidance for LLM (chars) - not enforced as hard cut
# Lower value yields more, smaller chunks; reduces word-splitting and uneven sizes.
DEFAULT_TARGET_CHARS = int(os.environ.get("CHUNK_TARGET_CHARS", "1200") or "1200")
LLM_CHUNKING_WINDOW_CHARS = int(os.environ.get("CHUNK_LLM_WINDOW_CHARS", "12000") or "12000")
LLM_CHUNKING_WINDOW_OVERLAP_CHARS = int(os.environ.get("CHUNK_LLM_WINDOW_OVERLAP_CHARS", "750") or "750")
LLM_CHUNKING_MAX_TOKENS = int(os.environ.get("CHUNK_LLM_MAX_TOKENS", "3000") or "3000")
LLM_CHUNKING_TIMEOUT_S = float(os.environ.get("CHUNK_LLM_TIMEOUT_S", "240") or "240")
# Cache size for chunking results
_CHUNK_CACHE_MAX_ENTRIES = max(1, int(os.environ.get("CHUNK_CACHE_SIZE", "256") or "256"))


def _snap_to_word_boundary(text: str, pos: int, snap_back: bool, max_move: int = 60) -> int:
    """
    Snap position to nearest word boundary.
    If no boundary within max_move chars, return original pos (avoids breaking text without spaces).
    """
    n = len(text)
    if pos <= 0 or pos >= n:
        return pos
    start_pos = pos
    if snap_back:
        # Move backward to start of current word
        while pos > 0 and start_pos - pos <= max_move and text[pos - 1 : pos] not in " \n\t\r.,;:!?)]}\"'":
            pos -= 1
        return pos if start_pos - pos <= max_move else start_pos
    else:
        # Move forward to end of current word
        while pos < n and pos - start_pos <= max_move and text[pos : pos + 1] not in " \n\t\r":
            pos += 1
        return pos if pos - start_pos <= max_move else start_pos


def _call_llm_for_chunking(
    text: str,
    target_chars: int,
    chat_fn: Callable[[str, str], str],
) -> list[str]:
    """
    Use LLM to split text into coherent, self-contained chunks.
    Each chunk respects sentence boundaries and logical paragraph groupings.
    """
    text_len = len(text)
    system_prompt = (
        "You are a JSON API for document chunk offsets. Return only one valid JSON object. "
        "The first character of your answer must be { and the last character must be }. "
        "Do not explain, summarize, reason aloud, use markdown, or include prose before or after the JSON. "
        'Schema: {"chunks":[{"start":0,"end":123}]}. '
        "Use 0-based character offsets into the exact input document; end is exclusive. "
        "Choose coherent chunks around the requested target size. Preserve sentence and paragraph boundaries when possible. "
        "Do not split words. Prefer numbered section boundaries when they are natural chunk boundaries."
    )
    user_prompt = (
        "Return chunk offsets for the document below.\n"
        f"Target chunk size: about {target_chars} characters.\n"
        f"Document length: {text_len} characters.\n"
        'Output exactly this JSON shape and nothing else: {"chunks":[{"start":0,"end":123}]}\n'
        "Do not include text excerpts in the JSON. Offsets only.\n\n"
        "<document>\n"
        f"{text}\n"
        "</document>"
    )
    try:
        response = chat_fn(system_prompt, user_prompt)
    except ChunkingError:
        raise
    except Exception as exc:
        logger.error("LLM chunking failed: %s", exc)
        raise ChunkingError(f"LLM chunking failed: {exc}") from exc

    parsed = _parse_chunking_json(response)
    chunk_items = parsed.get("chunks")
    if not isinstance(chunk_items, list):
        logger.error("LLM chunking returned JSON without top-level chunks list")
        raise ChunkingError("LLM chunking returned missing chunks list")

    # Preferred: list of {start,end} offsets.
    offsets: list[tuple[int, int]] = []
    for item in chunk_items:
        if not _is_offset_item(item):
            logger.error("LLM chunking returned invalid chunk offset item")
            raise ChunkingError("LLM chunking returned invalid chunk offset item")
        start = item.get("start")
        end = item.get("end")
        offsets.append((start, end))

    if not offsets:
        logger.error("LLM chunking returned no valid offsets")
        raise ChunkingError("LLM chunking returned no valid offsets")

    # Snap offsets to word boundaries to avoid cutting words in half
    snapped_offsets: list[tuple[int, int]] = []
    for start, end in offsets:
        if start < 0 or end < 0 or start >= end:
            continue
        if start > text_len:
            continue
        end = min(end, text_len)
        # Snap start backward to word start; snap end forward to word end
        start = max(0, _snap_to_word_boundary(text, start, snap_back=True))
        end = _snap_to_word_boundary(text, end, snap_back=False)
        if end <= start:
            continue
        snapped_offsets.append((start, end))

    coverage_offsets: list[tuple[int, int]] = []
    cursor = 0
    for start, end in sorted(snapped_offsets):
        if end <= cursor:
            continue
        if start > cursor:
            coverage_offsets.append((cursor, start))
        start = max(start, cursor)
        coverage_offsets.append((start, end))
        cursor = end
    if cursor < text_len:
        coverage_offsets.append((cursor, text_len))

    chunks: list[str] = []
    for start, end in coverage_offsets:
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

    if not chunks:
        logger.error("LLM chunking returned empty chunk list")
        raise ChunkingError("LLM chunking returned empty chunk list")
    return chunks


def _llm_windows(text: str, window_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= window_chars:
        return [text]
    windows: list[str] = []
    start = 0
    n = len(text)
    step = max(1, window_chars - max(0, overlap_chars))
    while start < n:
        end = min(start + window_chars, n)
        windows.append(text[start:end])
        if end >= n:
            break
        start += step
    return windows


def _call_llm_for_chunking_windowed(
    text: str,
    target_chars: int,
    chat_fn: Callable[[str, str], str],
    *,
    window_chars: int = LLM_CHUNKING_WINDOW_CHARS,
    overlap_chars: int = LLM_CHUNKING_WINDOW_OVERLAP_CHARS,
) -> list[str]:
    windows = _llm_windows(text, max(window_chars, target_chars * 2), max(0, overlap_chars))
    if len(windows) == 1:
        return _call_llm_for_chunking(text, target_chars, chat_fn)

    chunks: list[str] = []
    seen_hashes: set[str] = set()
    for idx, window in enumerate(windows, start=1):
        logger.info(
            "LLM chunking window %d/%d chars=%d target=%d",
            idx,
            len(windows),
            len(window),
            target_chars,
        )
        for chunk in _call_llm_for_chunking(window, target_chars, chat_fn):
            normalized = re.sub(r"\s+", " ", chunk.strip())
            if not normalized:
                continue
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            chunks.append(chunk)
    if not chunks:
        raise ChunkingError("LLM chunking returned empty chunk list")
    return chunks


class _ChunkLRUCache:
    def __init__(self, max_entries: int):
        self._max = max_entries
        self._lock = threading.Lock()
        self._data: OrderedDict[str, list[str]] = OrderedDict()

    def get(self, key: str) -> list[str] | None:
        with self._lock:
            if key not in self._data:
                return None
            val = self._data.pop(key)
            self._data[key] = val
            return val

    def set(self, key: str, val: list[str]) -> None:
        with self._lock:
            if key in self._data:
                self._data.pop(key)
            self._data[key] = val
            while len(self._data) > self._max:
                self._data.popitem(last=False)


_chunk_lru_cache = _ChunkLRUCache(_CHUNK_CACHE_MAX_ENTRIES)


def make_chat_fn_from_orchestrator(
    *,
    orchestrator_api_url: str,
    api_key: str | None = None,
    model: str = "openai:gpt-5.1",
    pipeline_id: str | None = None,
    task: str | None = None,
    timeout_s: float = LLM_CHUNKING_TIMEOUT_S,
) -> Callable[[str, str], str] | None:
    """
    Create a chat_fn for LLM chunking from orchestrator config.
    Returns None if orchestrator_api_url is empty.
    """
    url = (orchestrator_api_url or "").strip().rstrip("/")
    if not url:
        return None

    def _chat(system_prompt: str, user_prompt: str) -> str:
        import httpx

        endpoint = f"{url}/v1/chat/completions"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "pipeline_id": pipeline_id,
            "task": task,
            "temperature": 0.0,
            "max_tokens": LLM_CHUNKING_MAX_TOKENS,
            "tool_choice": "none",
            "response_format": _CHUNKING_RESPONSE_FORMAT,
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                logger.info(
                    "Chunking LLM request prompt_version=%s attempt=%d response_format=json_object max_tokens=%d timeout_s=%s",
                    CHUNKING_PROMPT_VERSION,
                    attempt + 1,
                    LLM_CHUNKING_MAX_TOKENS,
                    timeout_s,
                )
                with httpx.Client(timeout=timeout_s) as client:
                    resp = client.post(endpoint, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.error("Chunking LLM call failed: %s", exc)
                if attempt < 2:
                    time.sleep(0.5)
                    continue
                raise ChunkingError("Chunking LLM call failed") from exc

            preview = (resp.text or "")[:300].replace("\n", " ")
            if resp.status_code >= 500 and attempt < 2:
                logger.error("Chunking LLM transient error %s: %s", resp.status_code, preview)
                time.sleep(0.5)
                continue

            if resp.status_code >= 400:
                logger.error("Chunking LLM error %s: %s", resp.status_code, preview)
                raise ChunkingError(f"Chunking LLM error {resp.status_code}")

            try:
                body = resp.json()
            except Exception as exc:
                last_exc = exc
                logger.error("Chunking LLM returned non-JSON response: %s", exc)
                if attempt < 2:
                    time.sleep(0.5)
                    continue
                raise ChunkingError("Chunking LLM returned non-JSON response") from exc

            choices = body.get("choices") or []
            if not choices:
                logger.error("Chunking LLM response missing choices")
                raise ChunkingError("Chunking LLM response missing choices")
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if content is None or not str(content).strip():
                logger.error("Chunking LLM response missing content")
                if attempt < 2:
                    time.sleep(0.5)
                    continue
                raise ChunkingError("Chunking LLM response missing content")

            return str(content).strip()

        if last_exc is not None:
            raise ChunkingError(f"Chunking LLM call failed: {last_exc}") from last_exc
        raise ChunkingError("Chunking LLM call failed")

    return _chat


def chunk_text(
    text: str,
    *,
    target_chars: int = DEFAULT_TARGET_CHARS,
    overlap_chars: int = 250,
    strategy: str = "llm",
    chat_fn: Callable[[str, str], str] | None = None,
    use_cache: bool = True,
) -> list[str]:
    """
    Chunk text using the specified strategy.

    Args:
        text: Document text to chunk.
        target_chars: Target size guidance for the LLM.
        overlap_chars: Reserved for compatibility; LLM chunking uses configured transport windows.
        strategy: Only "llm" is supported by the public chunking wrapper.
        chat_fn: For strategy="llm", a callable (system_prompt, user_prompt) -> response.
        use_cache: Whether to use LRU cache for results (keyed by text hash + params).

    Returns:
        List of chunk text strings.
    """
    if not text or not text.strip():
        return []

    text = text.strip()

    cache_key: str | None = None
    if use_cache:
        cache_key = hashlib.sha256(f"{text}|{target_chars}|{overlap_chars}|{strategy}".encode("utf-8")).hexdigest()
        cached = _chunk_lru_cache.get(cache_key)
        if cached is not None:
            return cached

    if strategy != "llm":
        raise ValueError(f"Unsupported chunking strategy '{strategy}'; only 'llm' is allowed")
    if chat_fn is None:
        raise ValueError("chat_fn is required for strategy='llm'")
    chunks = _call_llm_for_chunking_windowed(text, target_chars, chat_fn)

    if use_cache and cache_key is not None:
        _chunk_lru_cache.set(cache_key, chunks)

    return chunks
