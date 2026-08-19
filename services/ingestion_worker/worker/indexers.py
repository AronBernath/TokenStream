import json
import os
import sqlite3
from typing import Any, Dict, List, Tuple

from common.models import Chunk
from common.retrieval_graph import normalize_graph_text
from common.index_naming import qdrant_collection_name, lexical_index_path
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

QDRANT_URL = os.environ["QDRANT_URL"]
LEXICAL_INDEX_DIR = os.environ.get("LEXICAL_INDEX_DIR") or os.environ.get("LEX_DB_DIR", "/data/lex")
QDRANT_UPSERT_BATCH_SIZE = int(os.environ.get("QDRANT_UPSERT_BATCH_SIZE", "256"))
qdrant = QdrantClient(url=QDRANT_URL)


def _sqlite_path(corpus: dict) -> str:
    return lexical_index_path(
        environment=corpus.get("environment") or "",
        tenant_id=corpus.get("tenant_id") or "",
        corpus_id=corpus["corpus_id"],
        data_dir=LEXICAL_INDEX_DIR,
    )


def _point_id_from_chunk_id(chunk_id: str) -> int:
    # Qdrant accepts uint64/UUID IDs; keep chunk_id in payload, use stable uint64 as point ID.
    return int(chunk_id[:16], 16)


def _qdrant_collection_name(corpus: dict) -> str:
    return qdrant_collection_name(
        environment=corpus.get("environment") or "",
        tenant_id=corpus.get("tenant_id") or "",
        corpus_id=corpus["corpus_id"],
    )


def _qdrant_collections_for_corpus(corpus: dict) -> List[str]:
    configured = str((corpus.get("index") or {}).get("qdrant_collection") or "").strip()
    canonical = _qdrant_collection_name(corpus)
    if configured and configured != canonical:
        return [canonical, configured]
    return [canonical]


def _flatten_metadata_for_fts(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (str, int, float, bool)):
        text = str(value).strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        values: List[str] = []
        for item in value:
            values.extend(_flatten_metadata_for_fts(item))
        return values
    if isinstance(value, dict):
        values: List[str] = []
        for key, item in value.items():
            if key in {"graph_document_node", "graph_primary_node", "graph_edges"}:
                continue
            values.append(str(key))
            values.extend(_flatten_metadata_for_fts(item))
        return values
    return [str(value)]


def ensure_indexes(corpus: dict):
    os.makedirs(LEXICAL_INDEX_DIR, exist_ok=True)
    db_path = _sqlite_path(corpus)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT,
                doc_type TEXT,
                tags_json TEXT NOT NULL,
                text TEXT NOT NULL,
                title TEXT,
                section_id TEXT,
                version_date TEXT,
                jurisdiction TEXT,
                language TEXT,
                source_url TEXT,
                metadata_json TEXT NOT NULL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_version_date ON chunks(version_date);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_language ON chunks(language);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_jurisdiction ON chunks(jurisdiction);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc_type ON chunks(doc_type);")
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED,
                text,
                title,
                section_id,
                doc_type,
                tags,
                tokenize = "unicode61"
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS canonical_nodes (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                label TEXT NOT NULL,
                doc_id TEXT,
                section_id TEXT,
                source_url TEXT,
                metadata_json TEXT NOT NULL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_canonical_nodes_doc_id ON canonical_nodes(doc_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_canonical_nodes_section_id ON canonical_nodes(section_id);")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS node_aliases (
                node_id TEXT NOT NULL,
                alias TEXT NOT NULL,
                alias_norm TEXT NOT NULL,
                PRIMARY KEY (node_id, alias_norm)
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_node_aliases_norm ON node_aliases(alias_norm);")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunk_node_links (
                chunk_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                link_role TEXT NOT NULL,
                PRIMARY KEY (chunk_id, node_id, link_role)
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunk_node_links_node_id ON chunk_node_links(node_id);")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS node_edges (
                edge_key TEXT PRIMARY KEY,
                src_node_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                dst_node_id TEXT,
                dst_alias TEXT,
                weight REAL NOT NULL DEFAULT 1.0,
                metadata_json TEXT NOT NULL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_node_edges_src ON node_edges(src_node_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_node_edges_dst ON node_edges(dst_node_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_node_edges_alias ON node_edges(dst_alias);")


def _as_graph_node(node: Any) -> Dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    node_id = str(node.get("node_id") or "").strip()
    if not node_id:
        return None
    aliases = []
    for alias in node.get("aliases") or []:
        alias_text = str(alias or "").strip()
        if alias_text:
            aliases.append(alias_text)
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return {
        "node_id": node_id,
        "node_type": str(node.get("node_type") or "node"),
        "label": str(node.get("label") or node_id),
        "aliases": aliases,
        "doc_id": metadata.get("doc_id"),
        "section_id": metadata.get("section_id"),
        "source_url": metadata.get("source_url"),
        "metadata": metadata,
    }


def _graph_edge_key(edge: Dict[str, Any]) -> str:
    return "|".join(
        [
            str(edge.get("src_node_id") or ""),
            str(edge.get("edge_type") or ""),
            str(edge.get("dst_node_id") or ""),
            str(edge.get("dst_alias") or ""),
        ]
    )


def _upsert_graph_material(conn: sqlite3.Connection, chunks: List[Chunk]) -> None:
    node_rows: Dict[str, Dict[str, Any]] = {}
    alias_rows: set[tuple[str, str, str]] = set()
    link_rows: set[tuple[str, str, str]] = set()
    edge_rows: Dict[str, Dict[str, Any]] = {}

    for chunk in chunks:
        metadata = chunk.metadata or {}
        primary = _as_graph_node(metadata.get("graph_primary_node"))
        document = _as_graph_node(metadata.get("graph_document_node"))
        for node in [document, primary]:
            if node is None:
                continue
            node_rows[node["node_id"]] = node
            for alias in node.get("aliases") or []:
                alias_norm = normalize_graph_text(alias)
                if alias_norm:
                    alias_rows.add((node["node_id"], str(alias), alias_norm))
        if primary is not None:
            link_rows.add((chunk.chunk_id, primary["node_id"], "primary"))
        if document is not None and (primary is None or document["node_id"] != primary["node_id"]):
            link_rows.add((chunk.chunk_id, document["node_id"], "document"))

        for edge in metadata.get("graph_edges") or []:
            if not isinstance(edge, dict):
                continue
            src_node_id = str(edge.get("src_node_id") or "").strip()
            edge_type = str(edge.get("edge_type") or "").strip()
            dst_node_id = str(edge.get("dst_node_id") or "").strip() or None
            dst_alias = normalize_graph_text(edge.get("dst_alias") or "") or None
            if not src_node_id or not edge_type or (dst_node_id is None and dst_alias is None):
                continue
            record = {
                "edge_key": _graph_edge_key(
                    {
                        "src_node_id": src_node_id,
                        "edge_type": edge_type,
                        "dst_node_id": dst_node_id or "",
                        "dst_alias": dst_alias or "",
                    }
                ),
                "src_node_id": src_node_id,
                "edge_type": edge_type,
                "dst_node_id": dst_node_id,
                "dst_alias": dst_alias,
                "weight": float(edge.get("weight", 1.0) or 1.0),
                "metadata": edge.get("metadata") if isinstance(edge.get("metadata"), dict) else {},
            }
            edge_rows[record["edge_key"]] = record

    if not node_rows and not link_rows and not edge_rows:
        return

    chunk_ids = sorted({chunk.chunk_id for chunk in chunks})
    node_ids = sorted(node_rows.keys())
    if chunk_ids:
        conn.executemany("DELETE FROM chunk_node_links WHERE chunk_id = ?;", [(chunk_id,) for chunk_id in chunk_ids])
    if node_ids:
        placeholders = ",".join("?" for _ in node_ids)
        conn.execute(f"DELETE FROM node_aliases WHERE node_id IN ({placeholders});", node_ids)
        conn.execute(
            f"DELETE FROM node_edges WHERE src_node_id IN ({placeholders}) OR dst_node_id IN ({placeholders});",
            [*node_ids, *node_ids],
        )
        conn.execute(f"DELETE FROM canonical_nodes WHERE node_id IN ({placeholders});", node_ids)

    conn.executemany(
        """
        INSERT OR REPLACE INTO canonical_nodes (
            node_id, node_type, label, doc_id, section_id, source_url, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        [
            (
                node["node_id"],
                node["node_type"],
                node["label"],
                node.get("doc_id"),
                node.get("section_id"),
                node.get("source_url"),
                json.dumps(node.get("metadata") or {}, ensure_ascii=False),
            )
            for node in node_rows.values()
        ],
    )
    if alias_rows:
        conn.executemany(
            "INSERT OR REPLACE INTO node_aliases (node_id, alias, alias_norm) VALUES (?, ?, ?);",
            sorted(alias_rows),
        )
    if link_rows:
        conn.executemany(
            "INSERT OR REPLACE INTO chunk_node_links (chunk_id, node_id, link_role) VALUES (?, ?, ?);",
            sorted(link_rows),
        )
    if edge_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO node_edges (
                edge_key, src_node_id, edge_type, dst_node_id, dst_alias, weight, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            [
                (
                    edge["edge_key"],
                    edge["src_node_id"],
                    edge["edge_type"],
                    edge.get("dst_node_id"),
                    edge.get("dst_alias"),
                    edge["weight"],
                    json.dumps(edge.get("metadata") or {}, ensure_ascii=False),
                )
                for edge in edge_rows.values()
            ],
        )


def upsert_qdrant(corpus: dict, chunks: List[Chunk], vectors: List[List[float]]):
    col = _qdrant_collection_name(corpus)
    dim = len(vectors[0]) if vectors else 0
    if not vectors:
        raise ValueError("No vectors provided for Qdrant upsert.")
    if len(chunks) != len(vectors):
        raise ValueError(f"chunks/vectors length mismatch: {len(chunks)} != {len(vectors)}")

    # Create collection if missing
    existing = [c.name for c in qdrant.get_collections().collections]
    if col not in existing:
        qdrant.create_collection(
            collection_name=col,
            vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
        )

    points = []
    for c, v in zip(chunks, vectors):
        payload = {
            "chunk_id": c.chunk_id,
            "doc_id": c.doc_id,
            "doc_type": c.metadata.get("doc_type"),
            "tags": c.metadata.get("tags", []),
            "title": c.title,
            "section_id": c.section_id,
            "version_date": c.version_date,
            "jurisdiction": c.jurisdiction,
            "language": c.language,
            "source_url": c.source_url,
            "text": c.text,
            "metadata": c.metadata,
            "environment": corpus.get("environment") or "",
            "tenant_id": corpus.get("tenant_id") or "",
            "corpus_id": corpus.get("corpus_id") or "",
            "source_id": c.metadata.get("registry_source_id") or c.doc_id,
        }
        points.append(qm.PointStruct(id=_point_id_from_chunk_id(c.chunk_id), vector=v, payload=payload))

    for start in range(0, len(points), QDRANT_UPSERT_BATCH_SIZE):
        qdrant.upsert(
            collection_name=col,
            points=points[start : start + QDRANT_UPSERT_BATCH_SIZE],
        )
    return len(points)


def upsert_lexical(corpus: dict, chunks: List[Chunk]):
    db_path = _sqlite_path(corpus)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        for c in chunks:
            metadata = dict(c.metadata or {})
            tags = metadata.get("tags", [])
            metadata.update(
                {
                    "environment": corpus.get("environment") or "",
                    "tenant_id": corpus.get("tenant_id") or "",
                    "corpus_id": corpus.get("corpus_id") or "",
                    "source_id": metadata.get("registry_source_id") or c.doc_id,
                }
            )
            metadata_values = _flatten_metadata_for_fts(metadata)
            tags_text = " ".join(str(t) for t in [*(tags or []), *metadata_values])
            metadata_json = json.dumps(metadata, ensure_ascii=False)
            tags_json = json.dumps(tags or [], ensure_ascii=False)

            conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?;", (c.chunk_id,))
            conn.execute("DELETE FROM chunks WHERE chunk_id = ?;", (c.chunk_id,))

            conn.execute(
                """
                INSERT INTO chunks (
                    chunk_id, doc_id, doc_type, tags_json, text, title, section_id,
                    version_date, jurisdiction, language, source_url, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    c.chunk_id,
                    c.doc_id,
                    c.metadata.get("doc_type"),
                    tags_json,
                    c.text,
                    c.title,
                    c.section_id,
                    c.version_date,
                    c.jurisdiction,
                    c.language,
                    c.source_url,
                    metadata_json,
                ),
            )
            conn.execute(
                """
                INSERT INTO chunks_fts (chunk_id, text, title, section_id, doc_type, tags)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    c.chunk_id,
                    c.text,
                    c.title or "",
                    c.section_id or "",
                    c.metadata.get("doc_type") or "",
                    tags_text,
                ),
            )

        _upsert_graph_material(conn, chunks)
        conn.commit()
    return len(chunks), db_path


def _delete_qdrant_points(collection_name: str, chunk_ids: List[str]) -> int:
    point_ids = sorted({_point_id_from_chunk_id(cid) for cid in chunk_ids if cid})
    if not point_ids:
        return 0
    try:
        point_selector = getattr(qm, "PointIdsList", None)
        if point_selector is None:
            raise RuntimeError("PointIdsList model not available")
        qdrant.delete(collection_name=collection_name, points_selector=point_selector(points=point_ids))
        return len(point_ids)
    except Exception:
        flt = qm.Filter(
            should=[qm.FieldCondition(key="chunk_id", match=qm.MatchValue(value=chunk_id)) for chunk_id in chunk_ids]
        )
        try:
            qdrant.delete(collection_name=collection_name, points_selector=qm.FilterSelector(filter=flt))
            return len(point_ids)
        except Exception:
            return 0


def get_corpus_source_hashes(corpus: dict) -> Dict[str, str]:
    """
    Returns a mapping of doc_id to the indexed source fingerprint of the first chunk found for that doc_id.
    """
    corpus_id = (corpus.get("corpus_id") or "").strip()
    if not corpus_id:
        return {}

    db_path = _sqlite_path(corpus)
    if not os.path.exists(db_path):
        return {}

    out = {}
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT doc_id, metadata_json FROM chunks;")
            for row in cur.fetchall():
                doc_id = row[0]
                try:
                    meta = json.loads(row[1])
                    source_id = str(meta.get("registry_source_id") or doc_id)
                    if "source_fingerprint" in meta:
                        out[source_id] = meta["source_fingerprint"]
                    elif "source_content_hash" in meta:
                        out[source_id] = meta["source_content_hash"]
                except Exception:
                    pass
        except sqlite3.OperationalError:
            pass
    return out


def _doc_ids_for_source(corpus: dict, source_id: str) -> List[str]:
    source_id = (source_id or "").strip()
    if not source_id:
        return []
    db_path = _sqlite_path(corpus)
    if not os.path.exists(db_path):
        return []
    with sqlite3.connect(db_path) as conn:
        try:
            cur = conn.execute(
                """
                SELECT DISTINCT doc_id
                FROM chunks
                WHERE doc_id = ?
                   OR json_extract(metadata_json, '$.registry_source_id') = ?
                """,
                (source_id, source_id),
            )
        except sqlite3.OperationalError:
            return []
        return [str(row[0]) for row in cur.fetchall() if row and row[0]]


def delete_corpus_source_artifacts(corpus: dict, source_id: str) -> Tuple[int, int]:
    deleted_chunks = 0
    deleted_qdrant = 0
    for doc_id in _doc_ids_for_source(corpus, source_id):
        chunk_count, qdrant_count = delete_corpus_document(corpus, doc_id)
        deleted_chunks += chunk_count
        deleted_qdrant += qdrant_count
    return deleted_chunks, deleted_qdrant


def delete_corpus_document(corpus: dict, doc_id: str) -> Tuple[int, int]:
    """
    Delete one document's indexed artifacts from SQLite and Qdrant.

    Returns:
        tuple[deleted_chunks, deleted_qdrant_points]
    """
    corpus_id = (corpus.get("corpus_id") or "").strip()
    doc_id = (doc_id or "").strip()
    if not corpus_id or not doc_id:
        return 0, 0

    db_path = _sqlite_path(corpus)
    chunk_ids: List[str] = []
    deleted_chunks = 0
    stale_node_ids: List[str] = []
    stale_alias_norms: List[str] = []

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        cur = conn.cursor()

        cur.execute("SELECT chunk_id FROM chunks WHERE doc_id = ?;", (doc_id,))
        chunk_ids = [str(r[0]) for r in cur.fetchall() if r and r[0]]

        candidate_node_ids: List[str] = []
        if chunk_ids:
            chunk_placeholders = ",".join("?" for _ in chunk_ids)
            cur.execute(
                f"SELECT DISTINCT node_id FROM chunk_node_links WHERE chunk_id IN ({chunk_placeholders});",
                chunk_ids,
            )
            candidate_node_ids.extend(str(r[0]) for r in cur.fetchall() if r and r[0])

        cur.execute(
            "SELECT node_id FROM canonical_nodes WHERE doc_id = ? AND node_id IS NOT NULL AND TRIM(node_id) != '';",
            (doc_id,),
        )
        candidate_node_ids.extend(str(r[0]) for r in cur.fetchall() if r and r[0])

        candidate_node_ids = sorted(set(candidate_node_ids))
        if candidate_node_ids:
            if chunk_ids:
                chunk_placeholders = ",".join("?" for _ in chunk_ids)
                node_placeholders = ",".join("?" for _ in candidate_node_ids)
                cur.execute(
                    f"SELECT DISTINCT node_id FROM chunk_node_links "
                    f"WHERE node_id IN ({node_placeholders}) AND chunk_id NOT IN ({chunk_placeholders});",
                    [*candidate_node_ids, *chunk_ids],
                )
            else:
                node_placeholders = ",".join("?" for _ in candidate_node_ids)
                cur.execute(
                    f"SELECT DISTINCT node_id FROM chunk_node_links WHERE node_id IN ({node_placeholders});",
                    candidate_node_ids,
                )
            active_node_ids = {str(r[0]) for r in cur.fetchall() if r and r[0]}
            stale_node_ids = [nid for nid in candidate_node_ids if nid not in active_node_ids]

        if chunk_ids:
            chunk_placeholders = ",".join("?" for _ in chunk_ids)
            cur.execute(f"DELETE FROM chunks_fts WHERE chunk_id IN ({chunk_placeholders});", chunk_ids)
            cur.execute("DELETE FROM chunks WHERE doc_id = ?;", (doc_id,))
            deleted_chunks = cur.rowcount or 0
            cur.execute(f"DELETE FROM chunk_node_links WHERE chunk_id IN ({chunk_placeholders});", chunk_ids)

        if stale_node_ids:
            stale_placeholders = ",".join("?" for _ in stale_node_ids)
            cur.execute(
                f"SELECT alias_norm FROM node_aliases WHERE node_id IN ({stale_placeholders});",
                stale_node_ids,
            )
            stale_alias_norms = [str(r[0]) for r in cur.fetchall() if r and r[0]]
            cur.execute(f"DELETE FROM node_aliases WHERE node_id IN ({stale_placeholders});", stale_node_ids)
            cur.execute(
                f"DELETE FROM node_edges WHERE src_node_id IN ({stale_placeholders}) OR dst_node_id IN ({stale_placeholders});",
                [*stale_node_ids, *stale_node_ids],
            )
            cur.execute(f"DELETE FROM canonical_nodes WHERE node_id IN ({stale_placeholders});", stale_node_ids)
            if stale_alias_norms:
                stale_alias_placeholders = ",".join("?" for _ in stale_alias_norms)
                cur.execute(
                    f"DELETE FROM node_edges WHERE dst_alias IN ({stale_alias_placeholders});",
                    stale_alias_norms,
                )

        conn.commit()

    deleted_qdrant = 0
    for collection_name in _qdrant_collections_for_corpus(corpus):
        deleted_qdrant += _delete_qdrant_points(collection_name=collection_name, chunk_ids=chunk_ids)

    return deleted_chunks, deleted_qdrant
