import os
import httpx
from typing import List
import logging

EMBEDDER_URL = os.environ["EMBEDDER_URL"].rstrip("/")
EMBED_BATCH_SIZE = int(os.environ.get("EMBED_BATCH_SIZE", "6"))
EMBED_TIMEOUT_SECONDS = float(os.environ.get("EMBED_TIMEOUT_SECONDS", "600"))
EMBED_MAX_CHARS = int(os.environ.get("EMBED_MAX_CHARS", "8000"))
logger = logging.getLogger("ingestion-worker.embed")


def _sanitize_texts(texts: List[str]) -> List[str]:
    out: List[str] = []
    for t in texts:
        t = t if isinstance(t, str) else str(t)
        if EMBED_MAX_CHARS > 0 and len(t) > EMBED_MAX_CHARS:
            t = t[:EMBED_MAX_CHARS]
        out.append(t)
    return out


def _embed_batch(client: httpx.Client, batch: List[str], start_idx: int, total: int) -> List[List[float]]:
    end_idx = start_idx + len(batch)
    logger.info("Embedding batch %d-%d/%d (size=%d)", start_idx + 1, end_idx, total, len(batch))
    r = client.post(f"{EMBEDDER_URL}/embed", json={"inputs": batch})
    if r.status_code == 413 and len(batch) > 1:
        half = max(1, len(batch) // 2)
        logger.warning(
            "TEI returned 413 for batch %d-%d/%d (size=%d), retrying with smaller batches",
            start_idx + 1,
            end_idx,
            total,
            len(batch),
        )
        left = _embed_batch(client, batch[:half], start_idx, total)
        right = _embed_batch(client, batch[half:], start_idx + half, total)
        return left + right
    r.raise_for_status()
    batch_vecs = r.json()
    if not isinstance(batch_vecs, list) or len(batch_vecs) != len(batch):
        raise ValueError(
            f"Unexpected embedder response size for batch {start_idx + 1}-{end_idx}: "
            f"got {len(batch_vecs) if isinstance(batch_vecs, list) else type(batch_vecs)}"
        )
    return batch_vecs


def embed_texts(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []

    texts = _sanitize_texts(texts)
    vectors: List[List[float]] = []
    total = len(texts)
    with httpx.Client(timeout=EMBED_TIMEOUT_SECONDS) as client:
        for start in range(0, total, EMBED_BATCH_SIZE):
            end = min(start + EMBED_BATCH_SIZE, total)
            batch = texts[start:end]
            batch_vecs = _embed_batch(client, batch, start, total)
            vectors.extend(batch_vecs)
    return vectors
