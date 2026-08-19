from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import httpx
from pydantic import ValidationError

from common.llm.types import ChatMessage, ToolCall, ToolDefinition
from common.models import LookupRequest, QueryRequest, QueryResponse

from .config import Settings
from .pipeline import PipelineResolution, is_tool_allowed

logger = logging.getLogger("orchestrator-api.rag")


def _json_or_none(resp: httpx.Response) -> Dict[str, Any] | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


async def _call_retrieval_api(
    *,
    base_url: str,
    req: QueryRequest,
    timeout_s: float = 20.0,
    headers: Optional[Dict[str, str]] = None,
) -> QueryResponse:
    url = f"{base_url.rstrip('/')}/v1/query"
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        try:
            resp = await client.post(url, json=req.model_dump(), headers=headers)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Retrieval API is unreachable ({base_url})") from exc

    if resp.status_code == 404:
        body = _json_or_none(resp) or {}
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        code = err.get("code", "corpus_not_found")
        msg = err.get("message", "Unknown corpus_id")
        details = err.get("details") if isinstance(err.get("details"), dict) else None
        raise RuntimeError(f"{msg} (code={code}, details={details})")

    if resp.status_code >= 500:
        raise RuntimeError(f"Retrieval API returned an upstream error (status={resp.status_code})")
    if resp.status_code >= 400:
        raise RuntimeError(f"Retrieval API request failed (status={resp.status_code})")

    body = _json_or_none(resp)
    if body is None:
        raise RuntimeError("Retrieval API returned a non-JSON response")
    try:
        return QueryResponse.model_validate(body)
    except ValidationError as exc:
        raise RuntimeError("Retrieval API returned an invalid response payload") from exc


async def _call_retrieval_lookup_api(
    *,
    base_url: str,
    req: LookupRequest,
    timeout_s: float = 20.0,
    headers: Optional[Dict[str, str]] = None,
) -> QueryResponse:
    url = f"{base_url.rstrip('/')}/v1/lookup"
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        try:
            resp = await client.post(url, json=req.model_dump(), headers=headers)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Retrieval API lookup is unreachable ({base_url})") from exc

    if resp.status_code == 404:
        body = _json_or_none(resp) or {}
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        code = err.get("code", "corpus_not_found")
        msg = err.get("message", "Unknown corpus_id or missing lexical index")
        details = err.get("details") if isinstance(err.get("details"), dict) else None
        raise RuntimeError(f"{msg} (code={code}, details={details})")

    if resp.status_code >= 500:
        raise RuntimeError(f"Retrieval API lookup returned an upstream error (status={resp.status_code})")
    if resp.status_code >= 400:
        raise RuntimeError(f"Retrieval API lookup request failed (status={resp.status_code})")

    body = _json_or_none(resp)
    if body is None:
        raise RuntimeError("Retrieval API lookup returned a non-JSON response")
    try:
        return QueryResponse.model_validate(body)
    except ValidationError as exc:
        raise RuntimeError("Retrieval API lookup returned an invalid response payload") from exc


def _format_retrieval_tool_text(
    *,
    query: str,
    corpus_id: str,
    top_k: int,
    filters: Dict[str, Any],
    resp: QueryResponse,
) -> str:
    chunks = resp.chunks or []
    lines: List[str] = []
    lines.append("RAG_RETRIEVAL_RESULT")
    lines.append(f"query={query}")
    lines.append(
        f"corpus_id={corpus_id} top_k={top_k} returned_chunks={len(chunks)} filter_keys={sorted(filters.keys())}"
    )
    lines.append("")

    lines.append("CITATIONS:")
    if not resp.citations:
        lines.append("- (none)")
    else:
        for i, c in enumerate(resp.citations, 1):
            if not isinstance(c, dict):
                continue
            title = str(c.get("title") or "").strip() or "-"
            section_id = str(c.get("section_id") or "").strip() or "-"
            version_date = str(c.get("version_date") or "").strip() or "-"
            source_url = str(c.get("source_url") or "").strip() or "-"
            lines.append(
                f"[{i}] title={title} | section_id={section_id} | version_date={version_date} | source_url={source_url}"
            )
    lines.append("")

    lines.append("CHUNKS:")
    if not chunks:
        lines.append("- (none)")
    else:
        for i, ch in enumerate(chunks, 1):
            head_parts = [
                f"[{i}]",
                f"chunk_id={ch.chunk_id}",
                f"score={ch.score:.4f}",
                f"doc_id={ch.doc_id}",
                f"doc_type={ch.doc_type}",
            ]
            if ch.title:
                head_parts.append(f"title={ch.title}")
            if ch.section_id:
                head_parts.append(f"section_id={ch.section_id}")
            if ch.version_date:
                head_parts.append(f"version_date={ch.version_date}")
            head_parts.append(f"source_url={ch.source_url}")
            lines.append(" | ".join(head_parts))
            lines.append((ch.text or "").strip())
            lines.append("")

    # Keep a compact machine-readable tail for models that want to parse.
    compact = {
        "api_version": resp.api_version,
        "chunks": [c.model_dump() for c in chunks],
        "citations": resp.citations,
    }
    lines.append("RAW_JSON:")
    lines.append(json.dumps(compact, ensure_ascii=False))
    return "\n".join(lines).strip()


@dataclass(frozen=True)
class RagTooling:
    settings: Settings

    @property
    def retrieval_tool_name(self) -> str:
        # Use OpenAI-safe tool name (no dots).
        return "rag__query"

    def tool_definitions(self, allowed_tools: Sequence[str] | None = None) -> List[ToolDefinition]:
        if allowed_tools is not None and not is_tool_allowed(self.retrieval_tool_name, allowed_tools):
            return []
        tools: List[ToolDefinition] = [
            ToolDefinition(
                name=self.retrieval_tool_name,
                description=(
                    "Retrieve relevant chunks from the RAG corpus via retrieval-api (/v1/query). "
                    "Returns chunk text plus citation metadata, with stable [1..N] numbering."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The user question to retrieve for."},
                        "corpus_id": {
                            "type": "string",
                            "description": "Corpus to retrieve from. Omit or leave empty to use the configured default.",
                        },
                        "top_k": {"type": "integer", "minimum": 1, "description": "Number of chunks to return."},
                        "filters": {"type": "object", "description": "Metadata filters object (JSON)."},
                    },
                    "required": ["query"],
                },
            )
        ]

        return tools

    def can_handle(self, tool_name: str, allowed_tools: Sequence[str] | None = None) -> bool:
        # Backward compatibility: older deployments/models may still emit dotted names.
        if not is_tool_allowed(tool_name, allowed_tools):
            return False
        if tool_name in {self.retrieval_tool_name, "rag.query"}:
            return True
        return False

    async def execute(
        self,
        tc: ToolCall,
        *,
        headers: Optional[Dict[str, str]] = None,
        pipeline: PipelineResolution | None = None,
    ) -> ChatMessage:
        try:
            if self.can_handle(tc.name, allowed_tools=None if pipeline is None else pipeline.allowed_tools):
                return await self._execute_retrieval(tc, headers=headers, pipeline=pipeline)
            raise RuntimeError(f"Unknown tool: {tc.name}")
        except Exception as exc:
            logger.warning("rag_tool_failed tool=%s error=%s", tc.name, str(exc), exc_info=True)
            return ChatMessage(role="tool", content=f"ERROR: {exc}", tool_call_id=tc.id)

    async def _execute_retrieval(
        self,
        tc: ToolCall,
        *,
        headers: Optional[Dict[str, str]] = None,
        pipeline: PipelineResolution | None = None,
    ) -> ChatMessage:
        args = tc.arguments or {}
        query = str(args.get("query") or "").strip()
        if not query:
            raise RuntimeError("Missing required argument: query")

        raw_corpus = str(args.get("corpus_id") or "").strip()
        pipeline_filters = pipeline.effective_filters if pipeline else {}

        if raw_corpus and raw_corpus.upper() == "DEFAULT_CORPUS_ID":
            raw_corpus = ""
        if pipeline:
            corpus_id = pipeline.enforce_corpus(raw_corpus or None)
        else:
            # LLM may pass literal "DEFAULT_CORPUS_ID" from tool description; resolve to actual default
            corpus_id = self.settings.default_corpus_id if not raw_corpus else raw_corpus
        top_k_raw = args.get("top_k", self.settings.default_top_k)
        try:
            top_k = int(top_k_raw)
        except Exception:
            top_k = self.settings.default_top_k
        top_k = max(top_k, 1)

        if pipeline and pipeline.max_top_k is not None:
            top_k = min(top_k, pipeline.max_top_k)

        requested_filters = args.get("filters") if isinstance(args.get("filters"), dict) else {}
        filters = dict(pipeline_filters)
        if isinstance(requested_filters, dict):
            filters.update(requested_filters)
        req = QueryRequest(query=query, corpus_id=corpus_id, filters=filters, top_k=top_k)
        resp = await _call_retrieval_api(
            base_url=self.settings.retrieval_api_url,
            req=req,
            timeout_s=20.0,
            headers=headers,
        )
        content = _format_retrieval_tool_text(
            query=query,
            corpus_id=corpus_id,
            top_k=top_k,
            filters=filters,
            resp=resp,
        )
        return ChatMessage(role="tool", content=content, tool_call_id=tc.id)
