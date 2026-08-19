import asyncio
import os
import re
from typing import Any, Dict, List, Tuple

from common.models import LookupRequest, QueryRequest, QueryResponse, RetrievedChunk
from common.retrieval_config import build_citation, invalid_filter_fields, merge_default_filters
from common.retrieval_graph import extract_query_aliases

from .embedder import TEIEmbedder
from .qdrant_client import qdrant_corpus_exists, qdrant_search
from .reranker import rerank_hits
from .sqlite_fts_client import (
    sqlite_corpus_exists,
    sqlite_exact_reference_search,
    sqlite_fetch_chunks_by_ids,
    sqlite_fts_search,
    sqlite_graph_expand,
    sqlite_lexical_lookup,
)
from .registry_client import get_corpus

EMBEDDER_URL = os.environ["EMBEDDER_URL"]
SEED_POOL_K = max(12, int(os.environ.get("RETRIEVAL_SEED_POOL_K", "30")))
GRAPH_POOL_K = max(12, int(os.environ.get("RETRIEVAL_GRAPH_POOL_K", "24")))
RERANK_POOL_K = max(12, int(os.environ.get("RETRIEVAL_RERANK_POOL_K", "40")))


class CorpusNotFoundError(Exception):
    def __init__(self, corpus_id: str):
        super().__init__(f"Unknown corpus_id: {corpus_id}")
        self.corpus_id = corpus_id


class InvalidFiltersError(Exception):
    def __init__(self, fields: List[str]):
        super().__init__(f"Unsupported filter fields: {', '.join(fields)}")
        self.fields = fields


class RetrievalConfigurationError(Exception):
    pass


def _is_missing_corpus_error(exc: Exception, corpus_id: str) -> bool:
    msg = str(exc).lower()
    return "not found" in msg and (f"corp_{corpus_id}".lower() in msg or corpus_id.lower() in msg)


def _normalize_scores(scores: Dict[str, float]) -> Dict[str, float]:
    if not scores:
        return {}
    values = [float(value) for value in scores.values()]
    hi = max(values)
    lo = min(values)
    if hi == lo:
        return {key: 1.0 for key in scores}
    return {key: (float(value) - lo) / (hi - lo) for key, value in scores.items()}


def _blend_channels(channels: List[Tuple[Dict[str, float], float]]) -> Dict[str, float]:
    keys = set()
    normalized: List[Tuple[Dict[str, float], float]] = []
    for scores, weight in channels:
        norm = _normalize_scores(scores)
        normalized.append((norm, weight))
        keys.update(norm.keys())
    out: Dict[str, float] = {}
    for key in keys:
        out[key] = sum(weight * scores.get(key, 0.0) for scores, weight in normalized)
    return out


def _prefers_exact_matching(query: str) -> bool:
    """
    Give lexical and exact-reference matches more influence for structured identifiers.

    This stays intentionally generic: it recognizes quoted text and tokens containing
    digits with common identifier separators, without knowing anything about a corpus
    domain, standard, regulation, or product family.
    """
    if not query:
        return False
    if any(char in query for char in ('"', "'", "`")):
        return True
    return bool(re.search(r"\b[\w.-]*\d[\w./:-]*\b", query))


def _collect_hits(payload_by_id: Dict[str, Dict[str, Any]], hits: List[Dict[str, Any]]) -> None:
    for hit in hits:
        payload_by_id[hit["chunk_id"]] = hit


def _ranked_ids(scores: Dict[str, float], limit: int) -> List[str]:
    return sorted(scores.keys(), key=lambda key: (-float(scores[key]), key))[:limit]


def _payload_to_chunk(chunk_id: str, payload: Dict[str, Any], score: float) -> RetrievedChunk:
    tags = payload.get("tags")
    if not tags and isinstance(payload.get("metadata"), dict):
        maybe_tags = payload["metadata"].get("tags")
        if isinstance(maybe_tags, list):
            tags = [str(tag) for tag in maybe_tags]
    if not isinstance(tags, list):
        tags = None
    else:
        tags = [str(tag) for tag in tags]
    return RetrievedChunk(
        chunk_id=chunk_id,
        score=float(score),
        text=payload.get("text", ""),
        doc_id=payload.get("doc_id") or "",
        doc_type=payload.get("doc_type") or "",
        source_url=payload.get("source_url") or "",
        title=payload.get("title") or None,
        section_id=payload.get("section_id"),
        tags=tags,
        version_date=payload.get("version_date"),
        metadata=payload.get("metadata") or None,
    )


def _response_from_ranked_payloads(
    *,
    corpus: Dict[str, Any],
    payload_by_id: Dict[str, Dict[str, Any]],
    scores: Dict[str, float],
    limit: int,
    no_results_message: str = "",
) -> QueryResponse:
    ranked_ids = _ranked_ids(scores, max(limit, 1))
    chunks = [
        _payload_to_chunk(chunk_id, payload_by_id[chunk_id], scores.get(chunk_id, 0.0))
        for chunk_id in ranked_ids[:limit]
        if chunk_id in payload_by_id
    ]
    citations = [build_citation(chunk, corpus) for chunk in chunks]

    answer_lines = []
    for index, chunk in enumerate(chunks, 1):
        title = chunk.title or chunk.doc_id or "Result"
        head = f"[{index}] {title}"
        if chunk.section_id:
            head += f" - {chunk.section_id}"
        answer_lines.append(head)
        answer_lines.append(chunk.text[:600].strip() + ("..." if len(chunk.text) > 600 else ""))
        answer_lines.append("")

    return QueryResponse(
        api_version="v1",
        answer="\n".join(answer_lines).strip() or no_results_message,
        citations=citations,
        chunks=chunks,
    )


def _load_lookup_corpus(req: LookupRequest) -> tuple[Dict[str, Any], Dict[str, Any]]:
    try:
        corpus = get_corpus(req.corpus_id)
    except ValueError as exc:
        raise RetrievalConfigurationError(str(exc)) from exc
    except Exception as exc:
        raise CorpusNotFoundError(req.corpus_id) from exc

    if not sqlite_corpus_exists(corpus):
        raise CorpusNotFoundError(req.corpus_id)

    filters = merge_default_filters(corpus, req.filters)
    invalid_fields = invalid_filter_fields(corpus, filters)
    if invalid_fields:
        raise InvalidFiltersError(invalid_fields)
    return corpus, filters


async def lexical_lookup_with_metrics(req: LookupRequest) -> Tuple[QueryResponse, Dict[str, int]]:
    corpus, filters = _load_lookup_corpus(req)
    terms = [str(term).strip() for term in req.terms if str(term).strip()]
    payload_by_id: Dict[str, Dict[str, Any]] = {}
    scores: Dict[str, float] = {}
    fts_hit_count = 0
    field_hit_count = 0
    exact_hit_count = 0

    for term_index, term in enumerate(terms):
        term_bonus = max(0.0, 1.0 - (term_index * 0.01))

        fts_hits = await sqlite_fts_search(corpus, term, top_k=req.top_k, filters=filters)
        fts_hit_count += len(fts_hits)
        for hit in fts_hits:
            chunk_id = str(hit["chunk_id"])
            payload_by_id[chunk_id] = hit
            scores[chunk_id] = max(scores.get(chunk_id, 0.0), float(hit.get("score") or 0.0) + 1.0 + term_bonus)

        field_hits = await sqlite_lexical_lookup(corpus, term, top_k=req.top_k, filters=filters)
        field_hit_count += len(field_hits)
        for hit in field_hits:
            chunk_id = str(hit["chunk_id"])
            payload_by_id[chunk_id] = hit
            scores[chunk_id] = max(scores.get(chunk_id, 0.0), float(hit.get("score") or 0.0) + 2.0 + term_bonus)

        exact_hits = await sqlite_exact_reference_search(
            corpus,
            extract_query_aliases(term),
            top_k=req.top_k,
            filters=filters,
        )
        exact_hit_count += len(exact_hits)
        for hit in exact_hits:
            chunk_id = str(hit["chunk_id"])
            payload_by_id[chunk_id] = hit
            scores[chunk_id] = max(scores.get(chunk_id, 0.0), float(hit.get("score") or 0.0) + 3.0 + term_bonus)

    response = _response_from_ranked_payloads(
        corpus=corpus,
        payload_by_id=payload_by_id,
        scores=scores,
        limit=req.max_results,
        no_results_message=f"No lookup results for {', '.join(terms)}",
    )
    metrics = {
        "returned_chunks": len(response.chunks),
        "terms": len(terms),
        "lexical_hits": fts_hit_count,
        "field_hits": field_hit_count,
        "exact_hits": exact_hit_count,
    }
    return response, metrics


async def hybrid_query_with_metrics(req: QueryRequest) -> Tuple[QueryResponse, Dict[str, int]]:
    try:
        corpus = get_corpus(req.corpus_id)
    except ValueError as exc:
        raise RetrievalConfigurationError(str(exc)) from exc
    except Exception as exc:
        raise CorpusNotFoundError(req.corpus_id) from exc

    if not qdrant_corpus_exists(corpus) and not sqlite_corpus_exists(corpus):
        raise CorpusNotFoundError(req.corpus_id)

    embedder = TEIEmbedder(EMBEDDER_URL)
    vec = (await embedder.embed([req.query]))[0]

    filters = merge_default_filters(corpus, req.filters)
    invalid_fields = invalid_filter_fields(corpus, filters)
    if invalid_fields:
        raise InvalidFiltersError(invalid_fields)
    retrieval_k = max(SEED_POOL_K, req.top_k * 4)

    try:
        sem_hits = await qdrant_search(corpus, vec, top_k=retrieval_k, filters=filters)
    except Exception as exc:
        if _is_missing_corpus_error(exc, req.corpus_id):
            raise CorpusNotFoundError(req.corpus_id) from exc
        raise

    lex_hits = await sqlite_fts_search(corpus, req.query, top_k=retrieval_k, filters=filters)
    query_aliases = extract_query_aliases(req.query)
    exact_hits = await sqlite_exact_reference_search(
        corpus,
        query_aliases,
        top_k=max(req.top_k * 3, 12),
        filters=filters,
    )

    sem_scores = {hit["chunk_id"]: hit["score"] for hit in sem_hits}
    lex_scores = {hit["chunk_id"]: hit["score"] for hit in lex_hits}
    exact_scores = {hit["chunk_id"]: hit["score"] for hit in exact_hits}

    if _prefers_exact_matching(req.query):
        blended = _blend_channels([(sem_scores, 0.25), (lex_scores, 0.45), (exact_scores, 0.30)])
    else:
        blended = _blend_channels([(sem_scores, 0.50), (lex_scores, 0.35), (exact_scores, 0.15)])

    payload_by_id: Dict[str, Dict[str, Any]] = {}
    _collect_hits(payload_by_id, lex_hits)
    _collect_hits(payload_by_id, sem_hits)
    _collect_hits(payload_by_id, exact_hits)

    seed_ids = _ranked_ids(blended, retrieval_k)
    graph_hits = await sqlite_graph_expand(
        corpus,
        seed_ids,
        top_k=max(GRAPH_POOL_K, req.top_k * 3),
        filters=filters,
    )
    _collect_hits(payload_by_id, graph_hits)
    graph_scores = {hit["chunk_id"]: hit["score"] for hit in graph_hits}

    pre_rerank_scores = _blend_channels([(blended, 0.84), (graph_scores, 0.16)])

    candidate_ids = _ranked_ids(pre_rerank_scores, max(RERANK_POOL_K, req.top_k * 5))
    missing_ids = [chunk_id for chunk_id in candidate_ids if chunk_id not in payload_by_id]
    if missing_ids:
        fetched_hits = await sqlite_fetch_chunks_by_ids(corpus, missing_ids, filters)
        _collect_hits(payload_by_id, fetched_hits)

    rerank_candidates = [
        {**payload_by_id[chunk_id], "score": float(pre_rerank_scores.get(chunk_id, 0.0))}
        for chunk_id in candidate_ids
        if chunk_id in payload_by_id
    ]
    reranked = await asyncio.to_thread(rerank_hits, req.query, rerank_candidates)
    final_scores = dict(pre_rerank_scores)
    rerank_scores = {str(hit["chunk_id"]): float(hit["rerank_score"]) for hit in reranked if "rerank_score" in hit}
    if rerank_scores:
        rerank_norm = _normalize_scores(rerank_scores)
        pre_norm = _normalize_scores(
            {chunk_id: pre_rerank_scores[chunk_id] for chunk_id in rerank_scores if chunk_id in pre_rerank_scores}
        )
        for chunk_id in set(pre_norm) | set(rerank_norm):
            final_scores[chunk_id] = 0.35 * pre_norm.get(chunk_id, 0.0) + 0.65 * rerank_norm.get(chunk_id, 0.0)

    response = _response_from_ranked_payloads(
        corpus=corpus,
        payload_by_id=payload_by_id,
        scores={
            chunk_id: final_scores.get(chunk_id, pre_rerank_scores.get(chunk_id, 0.0)) for chunk_id in payload_by_id
        },
        limit=req.top_k,
    )
    metrics = {
        "returned_chunks": len(response.chunks),
        "vector_hits": len(sem_hits),
        "lexical_hits": len(lex_hits),
        "exact_hits": len(exact_hits),
        "graph_hits": len(graph_hits),
        "reranked_candidates": len(rerank_scores),
    }
    return response, metrics


async def hybrid_query(req: QueryRequest) -> QueryResponse:
    response, _ = await hybrid_query_with_metrics(req)
    return response
