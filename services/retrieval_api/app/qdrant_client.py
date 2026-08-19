import os
import logging
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

QDRANT_URL = os.environ["QDRANT_URL"]
logger = logging.getLogger("retrieval-api.qdrant")
client = QdrantClient(url=QDRANT_URL)


from common.index_naming import qdrant_collection_name as _qdrant_collection_name


def _collection(corpus: dict) -> str:
    return _qdrant_collection_name(
        environment=corpus.get("environment") or "",
        tenant_id=corpus.get("tenant_id") or "",
        corpus_id=corpus["corpus_id"],
    )


TOP_LEVEL_FILTER_KEYS = {
    "chunk_id",
    "doc_id",
    "doc_type",
    "section_id",
    "version_date",
    "jurisdiction",
    "language",
    "source_url",
}


def _filter_key(key: str) -> str:
    if key == "tags":
        return "tags"
    if key in TOP_LEVEL_FILTER_KEYS:
        return key
    return f"metadata.{key}"


def _field_conditions_for_value(key: str, value: Any) -> List[qm.FieldCondition]:
    # Backward compatibility: older payloads may only have metadata.<key>.
    keys = [_filter_key(key)]
    if key in {"doc_type", "doc_id", "section_id", "source_url", "version_date"}:
        keys.append(f"metadata.{key}")
    return [qm.FieldCondition(key=k, match=qm.MatchValue(value=value)) for k in keys]


def _filters_to_qdrant(filters: Dict[str, Any]) -> Optional[qm.Filter]:
    must = []
    for k, v in (filters or {}).items():
        if v is None:
            continue
        if isinstance(v, (list, tuple, set)):
            should = []
            for vv in v:
                should.extend(_field_conditions_for_value(k, vv))
            if should:
                must.append(qm.Filter(should=should))
            continue
        must.append(qm.Filter(should=_field_conditions_for_value(k, v)))
    if not must:
        return None
    return qm.Filter(must=must)


def _execute_query(collection_name: str, vector: List[float], top_k: int, flt: Optional[qm.Filter]):
    if hasattr(client, "query_points"):
        try:
            result = client.query_points(
                collection_name=collection_name,
                query=vector,
                limit=top_k,
                with_payload=True,
                query_filter=flt,
            )
        except TypeError:
            result = client.query_points(
                collection_name=collection_name,
                query=vector,
                limit=top_k,
                with_payload=True,
                filter=flt,
            )
        return getattr(result, "points", result)

    if hasattr(client, "search"):
        return client.search(
            collection_name=collection_name,
            query_vector=vector,
            limit=top_k,
            with_payload=True,
            query_filter=flt,
        )

    if hasattr(client, "search_points"):
        try:
            result = client.search_points(
                collection_name=collection_name,
                query_vector=vector,
                limit=top_k,
                with_payload=True,
                query_filter=flt,
            )
        except TypeError:
            result = client.search_points(
                collection_name=collection_name,
                vector=vector,
                limit=top_k,
                with_payload=True,
                query_filter=flt,
            )
        return getattr(result, "result", result)

    raise RuntimeError("Unsupported qdrant-client version: no query/search method found.")


async def qdrant_search(corpus: dict, vector: List[float], top_k: int, filters: Dict[str, Any]):
    flt = _filters_to_qdrant(filters)
    res = _execute_query(
        collection_name=_collection(corpus),
        vector=vector,
        top_k=top_k,
        flt=flt,
    )
    out = []
    for p in res:
        payload = getattr(p, "payload", None) or {}
        point_id = getattr(p, "id", None)
        score = getattr(p, "score", 0.0)
        out.append(
            {
                "chunk_id": payload.get("chunk_id") or str(point_id),
                "score": float(score),
                "text": payload.get("text", ""),
                "doc_id": payload.get("doc_id") or str((payload.get("metadata") or {}).get("doc_id") or ""),
                "doc_type": payload.get("doc_type") or str((payload.get("metadata") or {}).get("doc_type") or ""),
                "title": payload.get("title", ""),
                "section_id": payload.get("section_id"),
                "source_url": payload.get("source_url") or "",
                "tags": payload.get("tags"),
                "version_date": payload.get("version_date"),
                "metadata": payload.get("metadata", {}) or {},
            }
        )
    return out


def qdrant_corpus_exists(corpus: dict) -> bool:
    collection_name = _collection(corpus)
    try:
        if hasattr(client, "collection_exists"):
            return bool(client.collection_exists(collection_name))
        if hasattr(client, "get_collection"):
            client.get_collection(collection_name)
            return True
    except Exception:
        return False
    return False
