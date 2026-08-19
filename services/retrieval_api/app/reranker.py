from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger("retrieval-api.reranker")

RERANKER_ENABLED = os.environ.get("RERANKER_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
RERANKER_MODEL = os.environ.get("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3").strip()
RERANKER_BATCH_SIZE = max(1, int(os.environ.get("RERANKER_BATCH_SIZE", "6")))
RERANKER_MAX_CANDIDATES = max(1, int(os.environ.get("RERANKER_MAX_CANDIDATES", "40")))
RERANKER_DEVICE = os.environ.get("RERANKER_DEVICE", "").strip()

_MODEL = None
_LOAD_FAILED = False


def _resolve_device() -> str:
    if RERANKER_DEVICE:
        return RERANKER_DEVICE
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _load_model():
    global _MODEL, _LOAD_FAILED
    if not RERANKER_ENABLED or _LOAD_FAILED:
        return None
    if _MODEL is not None:
        return _MODEL
    try:
        from sentence_transformers import CrossEncoder

        device = _resolve_device()
        logger.info("Loading reranker model=%s device=%s", RERANKER_MODEL, device)
        _MODEL = CrossEncoder(RERANKER_MODEL, device=device, trust_remote_code=True)
        return _MODEL
    except Exception as exc:
        _LOAD_FAILED = True
        logger.warning("Reranker unavailable: %s", exc)
        return None


def rerank_hits(query: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    model = _load_model()
    if model is None or len(hits) <= 1:
        return hits

    capped = hits[:RERANKER_MAX_CANDIDATES]
    pairs = [(query, str(hit.get("text") or "")) for hit in capped]
    try:
        scores = model.predict(pairs, batch_size=RERANKER_BATCH_SIZE, show_progress_bar=False)
    except Exception as exc:
        logger.warning("Reranker scoring failed: %s", exc)
        return hits

    reranked: List[Dict[str, Any]] = []
    for hit, score in zip(capped, scores):
        updated = dict(hit)
        updated["rerank_score"] = float(score)
        reranked.append(updated)
    reranked.sort(
        key=lambda item: (
            -float(item.get("rerank_score", 0.0)),
            -float(item.get("score", 0.0)),
            str(item.get("chunk_id") or ""),
        )
    )
    return reranked
