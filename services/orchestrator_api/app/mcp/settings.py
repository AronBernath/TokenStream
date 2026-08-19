from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .errors import McpError


@dataclass(frozen=True)
class McpServerSettings:
    """
    Remote MCP server configuration.

    For Streamable HTTP:
      - url: single JSON-RPC endpoint (often `/mcp`)

    For legacy SSE:
      - sse_url: SSE endpoint (often `/sse`)
      - messages_url: POST endpoint for client->server messages (often `/messages`)
    """

    name: str
    transport: str  # "streamable_http" | "sse"

    # Streamable HTTP
    url: Optional[str] = None

    # SSE (legacy)
    sse_url: Optional[str] = None
    messages_url: Optional[str] = None

    # Tool namespacing
    namespace: Optional[str] = None

    # Extra headers for auth etc.
    headers: Dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self):
        object.__setattr__(self, "headers", self.headers or {})

    @property
    def tool_namespace(self) -> str:
        return (self.namespace or self.name).strip()


def parse_mcp_servers(raw_json: str) -> List[McpServerSettings]:
    if not raw_json:
        return []
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise McpError("Invalid MCP_SERVERS JSON", details={"error": str(exc)}) from exc

    if not isinstance(data, list):
        raise McpError("MCP_SERVERS must be a JSON array")

    out: List[McpServerSettings] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise McpError("MCP_SERVERS entries must be objects", details={"index": idx})

        name = str(item.get("name") or "").strip()
        if not name:
            raise McpError("MCP server entry missing name", details={"index": idx})

        transport = str(item.get("transport") or "streamable_http").strip().lower()
        headers = item.get("headers") if isinstance(item.get("headers"), dict) else {}

        if transport == "streamable_http":
            url = str(item.get("url") or "").strip()
            if not url:
                raise McpError("Streamable HTTP MCP server missing url", details={"name": name})
            out.append(
                McpServerSettings(
                    name=name,
                    transport=transport,
                    url=url,
                    namespace=str(item.get("namespace") or "").strip() or None,
                    headers={str(k): str(v) for k, v in headers.items()},
                )
            )
            continue

        if transport == "sse":
            sse_url = str(item.get("sse_url") or item.get("sseUrl") or "").strip()
            messages_url = str(item.get("messages_url") or item.get("messagesUrl") or "").strip()
            if not sse_url and isinstance(item.get("url"), str):
                # Convenience: allow url to point to /sse for legacy configs.
                sse_url = str(item.get("url")).strip()
            if not messages_url and sse_url.endswith("/sse"):
                messages_url = sse_url[: -len("/sse")] + "/messages"
            if not sse_url or not messages_url:
                raise McpError(
                    "SSE MCP server requires sse_url and messages_url",
                    details={"name": name, "sse_url": sse_url, "messages_url": messages_url},
                )
            out.append(
                McpServerSettings(
                    name=name,
                    transport=transport,
                    sse_url=sse_url,
                    messages_url=messages_url,
                    namespace=str(item.get("namespace") or "").strip() or None,
                    headers={str(k): str(v) for k, v in headers.items()},
                )
            )
            continue

        raise McpError("Unsupported MCP transport", details={"name": name, "transport": transport})

    # Ensure unique namespaces (to avoid tool collisions across servers).
    seen: set[str] = set()
    for s in out:
        ns = s.tool_namespace
        if ns in seen:
            raise McpError("Duplicate MCP server namespace/name", details={"namespace": ns})
        seen.add(ns)

    return out


def summarize_mcp_servers(servers: Sequence[McpServerSettings]) -> List[Dict[str, Any]]:
    return [
        {
            "name": s.name,
            "namespace": s.tool_namespace,
            "transport": s.transport,
            "url": s.url,
            "sse_url": s.sse_url,
            "messages_url": s.messages_url,
            "headers": sorted(s.headers.keys()),
        }
        for s in servers
    ]
