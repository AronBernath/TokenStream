import json
import os
import re
import sqlite3
import logging

logger = logging.getLogger("retrieval-api.sqlite")
from typing import Any, Dict, List, Tuple

from common.index_naming import lexical_index_path

LEXICAL_INDEX_DIR = os.environ.get("LEXICAL_INDEX_DIR") or os.environ.get("LEX_DB_DIR", "/data/lex")


def _sqlite_path(corpus: dict) -> str:
    return lexical_index_path(
        environment=corpus.get("environment") or "",
        tenant_id=corpus.get("tenant_id") or "",
        corpus_id=corpus["corpus_id"],
        data_dir=LEXICAL_INDEX_DIR,
    )


def _build_match_query(query: str) -> str:
    tokens = re.findall(r"\w+", query, flags=re.UNICODE)
    if not tokens:
        return '""'
    return " AND ".join(f'"{t}"' for t in tokens)


def _build_filter_sql(filters: Dict[str, Any]) -> Tuple[List[str], List[Any]]:
    where: List[str] = []
    params: List[Any] = []

    for key, value in (filters or {}).items():
        if value is None:
            continue
        if key == "tags":
            vals = value if isinstance(value, list) else [value]
            tag_clauses = []
            for v in vals:
                tag_clauses.append("EXISTS (SELECT 1 FROM json_each(c.tags_json) WHERE json_each.value = ?)")
                params.append(str(v))
            where.append("(" + " OR ".join(tag_clauses) + ")")
            continue

        if key in {
            "chunk_id",
            "doc_id",
            "doc_type",
            "section_id",
            "version_date",
            "jurisdiction",
            "language",
            "source_url",
        }:
            col = f"c.{key}"
            if key == "doc_type":
                col = "COALESCE(c.doc_type, json_extract(c.metadata_json, '$.doc_type'))"
            if isinstance(value, (list, tuple, set)):
                vals = list(value)
                if not vals:
                    continue
                where.append(f"{col} IN ({','.join('?' for _ in vals)})")
                params.extend(vals)
            else:
                where.append(f"{col} = ?")
                params.append(value)
            continue

        if isinstance(value, (list, tuple, set)):
            vals = list(value)
            if not vals:
                continue
            where.append(f"json_extract(c.metadata_json, ?) IN ({','.join('?' for _ in vals)})")
            params.append(f"$.{key}")
            params.extend(vals)
        else:
            where.append("json_extract(c.metadata_json, ?) = ?")
            params.append(f"$.{key}")
            params.append(value)

    return where, params


def _rows_to_chunks(rows: List[tuple]) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        tags = None
        if row[9]:
            try:
                parsed_tags = json.loads(row[9])
                if isinstance(parsed_tags, list):
                    tags = [str(t) for t in parsed_tags]
            except json.JSONDecodeError:
                tags = None
        metadata = {}
        if row[10]:
            try:
                metadata = json.loads(row[10])
            except json.JSONDecodeError:
                metadata = {}
        out.append(
            {
                "chunk_id": row[0],
                "score": float(row[1]),
                "doc_id": row[2] or "",
                "doc_type": row[3] or "",
                "text": row[4],
                "title": row[5],
                "section_id": row[6],
                "source_url": row[7] or "",
                "version_date": row[8],
                "tags": tags,
                "metadata": metadata,
            }
        )
    return out


def _chunk_projection(score_expr: str) -> str:
    return f"""
        SELECT
            c.chunk_id,
            {score_expr} AS score,
            c.doc_id,
            COALESCE(c.doc_type, '') AS doc_type,
            c.text,
            COALESCE(c.title, '') AS title,
            c.section_id,
            COALESCE(c.source_url, '') AS source_url,
            c.version_date,
            c.tags_json,
            c.metadata_json
    """


async def sqlite_fts_search(corpus: dict, query: str, top_k: int, filters: Dict[str, Any]):
    db_path = _sqlite_path(corpus)
    if not os.path.isfile(db_path):
        return []

    match_query = _build_match_query(query)
    filter_sql, filter_params = _build_filter_sql(filters or {})
    where_sql = ""
    if filter_sql:
        where_sql = " AND " + " AND ".join(filter_sql)

    sql = f"""
        {_chunk_projection("-bm25(chunks_fts)")}
        FROM chunks_fts
        JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
        WHERE chunks_fts MATCH ? {where_sql}
        ORDER BY score DESC
        LIMIT ?;
    """

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        try:
            cur.execute(sql, [match_query, *filter_params, top_k])
        except sqlite3.OperationalError:
            # Fallback to quoted raw query if tokenized expression is invalid.
            cur.execute(sql, [f'"{query.replace(chr(34), " ")}"', *filter_params, top_k])
        rows = cur.fetchall()

    return _rows_to_chunks(rows)


async def sqlite_lexical_lookup(corpus: dict, term: str, top_k: int, filters: Dict[str, Any]):
    db_path = _sqlite_path(corpus)
    term = str(term or "").strip()
    if not os.path.isfile(db_path) or not term:
        return []

    normalized = term.lower()
    like = f"%{normalized}%"
    filter_sql, filter_params = _build_filter_sql(filters or {})
    where_sql = ""
    if filter_sql:
        where_sql = " AND " + " AND ".join(filter_sql)

    exact_fields = (
        "lower(c.doc_id) = ? OR lower(COALESCE(c.title, '')) = ? OR "
        "lower(COALESCE(c.section_id, '')) = ? OR lower(COALESCE(c.source_url, '')) = ?"
    )
    contains_fields = (
        "lower(c.doc_id) LIKE ? OR lower(COALESCE(c.title, '')) LIKE ? OR "
        "lower(COALESCE(c.section_id, '')) LIKE ? OR lower(COALESCE(c.source_url, '')) LIKE ? OR "
        "lower(c.metadata_json) LIKE ? OR lower(c.text) LIKE ?"
    )
    score_expr = f"""
        CASE
            WHEN {exact_fields} THEN 4.0
            WHEN lower(c.doc_id) LIKE ? OR lower(COALESCE(c.title, '')) LIKE ?
              OR lower(COALESCE(c.section_id, '')) LIKE ?
              OR lower(COALESCE(c.source_url, '')) LIKE ?
              OR lower(c.metadata_json) LIKE ? THEN 3.0
            ELSE 1.0
        END
    """
    sql = f"""
        {_chunk_projection(score_expr)}
        FROM chunks c
        WHERE ({exact_fields} OR {contains_fields}) {where_sql}
        ORDER BY score DESC, c.chunk_id ASC
        LIMIT ?;
    """
    params = [
        normalized,
        normalized,
        normalized,
        normalized,
        like,
        like,
        like,
        like,
        like,
        normalized,
        normalized,
        normalized,
        normalized,
        like,
        like,
        like,
        like,
        like,
        like,
        *filter_params,
        top_k,
    ]

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()

    return _rows_to_chunks(rows)


async def sqlite_fetch_chunks_by_ids(corpus: dict, chunk_ids: List[str], filters: Dict[str, Any]):
    if not chunk_ids:
        return []
    db_path = _sqlite_path(corpus)
    if not os.path.isfile(db_path):
        return []

    placeholders = ",".join("?" for _ in chunk_ids)
    filter_sql, filter_params = _build_filter_sql(filters or {})
    where_sql = f"c.chunk_id IN ({placeholders})"
    if filter_sql:
        where_sql += " AND " + " AND ".join(filter_sql)
    sql = f"""
        {_chunk_projection("0.0")}
        FROM chunks c
        WHERE {where_sql}
        ORDER BY c.chunk_id ASC;
    """
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(sql, [*chunk_ids, *filter_params])
        rows = cur.fetchall()
    return _rows_to_chunks(rows)


async def sqlite_exact_reference_search(
    corpus: dict,
    aliases: List[str],
    top_k: int,
    filters: Dict[str, Any],
):
    if not aliases:
        return []
    db_path = _sqlite_path(corpus)
    if not os.path.isfile(db_path):
        return []

    alias_norms = [str(alias).strip() for alias in aliases if str(alias).strip()]
    if not alias_norms:
        return []
    alias_placeholders = ",".join("?" for _ in alias_norms)
    filter_sql, filter_params = _build_filter_sql(filters or {})
    where_sql = f"na.alias_norm IN ({alias_placeholders})"
    if filter_sql:
        where_sql += " AND " + " AND ".join(filter_sql)
    sql = f"""
        {_chunk_projection("MAX(CASE WHEN l.link_role = 'primary' THEN 1.25 ELSE 0.75 END)")}
        FROM node_aliases na
        JOIN chunk_node_links l ON l.node_id = na.node_id
        JOIN chunks c ON c.chunk_id = l.chunk_id
        WHERE {where_sql}
        GROUP BY c.chunk_id
        ORDER BY score DESC, c.chunk_id ASC
        LIMIT ?;
    """
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(sql, [*alias_norms, *filter_params, top_k])
        rows = cur.fetchall()
    return _rows_to_chunks(rows)


async def sqlite_graph_expand(corpus: dict, seed_chunk_ids: List[str], top_k: int, filters: Dict[str, Any]):
    if not seed_chunk_ids:
        return []
    db_path = _sqlite_path(corpus)
    if not os.path.isfile(db_path):
        return []

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        chunk_placeholders = ",".join("?" for _ in seed_chunk_ids)
        cur.execute(
            f"""
            SELECT DISTINCT node_id
            FROM chunk_node_links
            WHERE chunk_id IN ({chunk_placeholders}) AND link_role = 'primary';
            """,
            seed_chunk_ids,
        )
        seed_nodes = [str(row[0]) for row in cur.fetchall() if row and row[0]]
        if not seed_nodes:
            return []

        node_placeholders = ",".join("?" for _ in seed_nodes)
        cur.execute(
            f"""
            SELECT edge_key, src_node_id, edge_type, dst_node_id, dst_alias, weight
            FROM node_edges
            WHERE src_node_id IN ({node_placeholders})
            ORDER BY weight DESC, edge_key ASC
            LIMIT ?;
            """,
            [*seed_nodes, max(top_k * 8, 48)],
        )
        edge_rows = cur.fetchall()

        target_node_ids: set[str] = set()
        unresolved_aliases: set[str] = set()
        for _, _, _, dst_node_id, dst_alias, _ in edge_rows:
            if dst_node_id:
                target_node_ids.add(str(dst_node_id))
            elif dst_alias:
                unresolved_aliases.add(str(dst_alias))

        if unresolved_aliases:
            alias_placeholders = ",".join("?" for _ in unresolved_aliases)
            cur.execute(
                f"SELECT DISTINCT node_id FROM node_aliases WHERE alias_norm IN ({alias_placeholders});",
                list(unresolved_aliases),
            )
            target_node_ids.update(str(row[0]) for row in cur.fetchall() if row and row[0])

        if not target_node_ids:
            return []

        target_placeholders = ",".join("?" for _ in target_node_ids)
        filter_sql, filter_params = _build_filter_sql(filters or {})
        where_sql = f"l.node_id IN ({target_placeholders})"
        if filter_sql:
            where_sql += " AND " + " AND ".join(filter_sql)
        sql = f"""
            {_chunk_projection("MAX(ne.weight * CASE WHEN l.link_role = 'primary' THEN 1.0 ELSE 0.8 END)")}
            FROM chunk_node_links l
            JOIN node_edges ne
              ON ne.dst_node_id = l.node_id
              OR (ne.dst_node_id IS NULL AND ne.dst_alias IN (
                  SELECT alias_norm FROM node_aliases na WHERE na.node_id = l.node_id
              ))
            JOIN chunks c ON c.chunk_id = l.chunk_id
            WHERE {where_sql}
              AND ne.src_node_id IN ({node_placeholders})
            GROUP BY c.chunk_id
            ORDER BY score DESC, c.chunk_id ASC
            LIMIT ?;
        """
        cur.execute(sql, [*target_node_ids, *filter_params, *seed_nodes, top_k])
        rows = cur.fetchall()
    return _rows_to_chunks(rows)


def sqlite_corpus_exists(corpus: dict) -> bool:
    return os.path.isfile(_sqlite_path(corpus))
