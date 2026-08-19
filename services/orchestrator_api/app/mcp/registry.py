from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from common.llm.types import ChatMessage, ToolCall, ToolDefinition

from .errors import McpError, McpProtocolError, McpTransportError
from .http_client import BaseMcpClient, SseMcpClient, StreamableHttpMcpClient
from .settings import McpServerSettings
from ..logging_utils import bounded_log_payload
from ..pipeline import is_tool_allowed

logger = logging.getLogger("orchestrator-api.mcp")


@dataclass(frozen=True)
class NamespacedTool:
    full_name: str
    server_namespace: str
    server_name: str
    mcp_tool_name: str
    description: Optional[str]
    input_schema: Dict[str, Any]


def _ensure_json_schema_object(schema: Any) -> Dict[str, Any]:
    if isinstance(schema, dict) and schema.get("type") == "object":
        return schema
    if isinstance(schema, dict):
        # Some servers omit type; treat as object schema.
        return {"type": "object", **schema}
    return {"type": "object", "properties": {}}


def _tool_result_to_text(result: Any) -> str:
    """
    Convert an MCP tool/call result to a tool-message string.

    We prefer a readable text summary when possible, and fall back to compact JSON.
    """
    if not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    is_error = bool(result.get("isError"))
    content = result.get("content")
    if isinstance(content, list):
        text_parts: List[str] = []
        non_text_items: List[Dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            t = item.get("type")
            if t == "text" and isinstance(item.get("text"), str):
                text_parts.append(item["text"])
            else:
                non_text_items.append(item)
        if text_parts and not non_text_items:
            txt = "\n".join(text_parts).strip()
            if txt:
                return f"ERROR: {txt}" if is_error else txt
            if is_error:
                return "ERROR"
            return "(empty tool result)"

    # Fallback: include raw JSON (trimmed to a reasonable size).
    raw = json.dumps(result, ensure_ascii=False)
    if len(raw) > 40_000:
        raw = raw[:40_000] + "…"
    return f"ERROR: {raw}" if is_error else raw


def _tool_result_observability(result: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"result_type": type(result).__name__}
    if not isinstance(result, dict):
        return out
    out["is_error"] = bool(result.get("isError"))
    content = result.get("content")
    if isinstance(content, list):
        out["content_items"] = len(content)
        text_chars = 0
        non_text_items = 0
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                text_chars += len(item["text"])
            else:
                non_text_items += 1
        out["text_chars"] = text_chars
        out["non_text_items"] = non_text_items
    return out


class McpToolRegistry:
    def __init__(
        self,
        *,
        servers: Sequence[McpServerSettings],
        protocol_version: str,
        timeout_s: float,
        strict: bool = False,
    ):
        self._servers = list(servers)
        self._protocol_version = protocol_version
        self._timeout_s = timeout_s
        self._strict = strict

        self._clients: Dict[str, BaseMcpClient] = {}
        self._tools: Dict[str, NamespacedTool] = {}
        # Backward-compatible alias mapping (e.g. "viz.tool" -> "viz__tool")
        self._aliases: Dict[str, str] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._servers)

    def tool_definitions(self, allowed_tools: Sequence[str] | None = None) -> List[ToolDefinition]:
        out: List[ToolDefinition] = []
        for t in self._tools.values():
            if not is_tool_allowed(t.full_name, allowed_tools):
                dotted_name = f"{t.server_namespace}.{t.mcp_tool_name}"
                if not is_tool_allowed(dotted_name, allowed_tools):
                    continue
            out.append(
                ToolDefinition(
                    name=t.full_name,
                    description=(
                        f"[{t.server_namespace}] {t.description}".strip()
                        if t.description
                        else f"[{t.server_namespace}]"
                    ),
                    parameters=_ensure_json_schema_object(t.input_schema),
                )
            )
        return out

    def list_tool_names(self) -> List[str]:
        return sorted(self._tools.keys())

    async def start(self) -> None:
        if not self._servers:
            return

        errors: List[Dict[str, Any]] = []
        for s in self._servers:
            try:
                client = self._build_client(s)
                await client.connect()
                tools = await client.list_tools()
                self._clients[s.tool_namespace] = client
                self._register_server_tools(server=s, tools=tools)
                logger.info(
                    "mcp_server_ready name=%s namespace=%s transport=%s tools=%d",
                    s.name,
                    s.tool_namespace,
                    s.transport,
                    len(tools),
                )
            except Exception as exc:
                if self._strict:
                    logger.warning(
                        "mcp_server_failed name=%s namespace=%s transport=%s error=%s",
                        s.name,
                        s.tool_namespace,
                        s.transport,
                        str(exc),
                        exc_info=True,
                    )
                else:
                    logger.info(
                        "mcp_server_optional_unavailable name=%s namespace=%s transport=%s error=%s",
                        s.name,
                        s.tool_namespace,
                        s.transport,
                        str(exc),
                    )
                errors.append(
                    {"name": s.name, "namespace": s.tool_namespace, "transport": s.transport, "error": str(exc)}
                )

        if self._strict and errors:
            raise McpError("One or more MCP servers failed to start", details={"errors": errors})

    async def close(self) -> None:
        for c in list(self._clients.values()):
            try:
                await c.close()
            except Exception:
                logger.debug("mcp_client_close_failed", exc_info=True)
        self._clients.clear()
        self._tools.clear()
        self._aliases.clear()

    def _build_client(self, s: McpServerSettings) -> BaseMcpClient:
        if s.transport == "streamable_http":
            return StreamableHttpMcpClient(
                url=s.url or "",
                protocol_version=self._protocol_version,
                headers=s.headers,
                timeout_s=self._timeout_s,
            )
        if s.transport == "sse":
            return SseMcpClient(
                sse_url=s.sse_url or "",
                messages_url=s.messages_url or "",
                protocol_version=self._protocol_version,
                headers=s.headers,
                timeout_s=self._timeout_s,
            )
        raise McpError("Unsupported MCP transport", details={"name": s.name, "transport": s.transport})

    def _register_server_tools(self, *, server: McpServerSettings, tools: Sequence[Dict[str, Any]]) -> None:
        for t in tools:
            name = t.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            desc = t.get("description")
            description = desc if isinstance(desc, str) else None
            input_schema = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {}

            # OpenAI-safe canonical tool name (no dots).
            full_name = f"{server.tool_namespace}__{name}"
            if full_name in self._tools:
                # Should not happen if namespaces are unique, but protect against it.
                suffix = 2
                candidate = f"{full_name}_{suffix}"
                while candidate in self._tools:
                    suffix += 1
                    candidate = f"{full_name}_{suffix}"
                full_name = candidate

            self._tools[full_name] = NamespacedTool(
                full_name=full_name,
                server_namespace=server.tool_namespace,
                server_name=server.name,
                mcp_tool_name=name,
                description=description,
                input_schema=input_schema or {},
            )

            # Backward compatibility: accept dotted tool calls even though we do not
            # advertise them to providers.
            dotted = f"{server.tool_namespace}.{name}"
            if dotted != full_name and dotted not in self._tools:
                self._aliases[dotted] = full_name

    def _resolve_tool(self, full_name: str) -> tuple[BaseMcpClient, NamespacedTool]:
        canonical = self._aliases.get(full_name, full_name)
        t = self._tools.get(canonical)
        if t is None:
            raise McpError("Unknown tool", details={"tool": full_name})
        client = self._clients.get(t.server_namespace)
        if client is None:
            raise McpError("MCP server not available", details={"tool": full_name, "namespace": t.server_namespace})
        return client, t

    async def execute_tool_calls(
        self,
        tool_calls: Sequence[ToolCall],
        *,
        trace_id: Optional[str] = None,
        round_idx: Optional[int] = None,
        allowed_tools: Sequence[str] | None = None,
    ) -> List[ChatMessage]:
        """
        Execute tool calls and return `tool` role messages to append to the chat.
        """
        out: List[ChatMessage] = []
        for tc in tool_calls:
            started = time.perf_counter()
            logger.info(
                "mcp_tool_call_start %s",
                bounded_log_payload(
                    trace_id=trace_id,
                    round=round_idx,
                    tool=tc.name,
                    tool_call_id=tc.id,
                    args_is_object=isinstance(tc.arguments, dict),
                    args_keys=(sorted(list(tc.arguments.keys())) if isinstance(tc.arguments, dict) else []),
                    args_raw_present=bool(tc.arguments_raw and tc.arguments_raw.strip()),
                    max_chars=600,
                ),
            )
            try:
                if tc.arguments is None and isinstance(tc.arguments_raw, str) and tc.arguments_raw.strip():
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    logger.warning(
                        "mcp_tool_call_invalid_args %s",
                        bounded_log_payload(
                            trace_id=trace_id,
                            round=round_idx,
                            tool=tc.name,
                            tool_call_id=tc.id,
                            latency_ms=elapsed_ms,
                            args_raw=tc.arguments_raw,
                            max_chars=600,
                        ),
                    )
                    out.append(
                        ChatMessage(
                            role="tool",
                            content=_tool_result_to_text(
                                {
                                    "isError": True,
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": (
                                                "Invalid tool arguments: expected a JSON object "
                                                f"but received {tc.arguments_raw!r}"
                                            ),
                                        }
                                    ],
                                }
                            ),
                            tool_call_id=tc.id,
                        )
                    )
                    continue
                if not is_tool_allowed(tc.name, allowed_tools):
                    raise McpError("Tool not allowed", details={"tool": tc.name, "pipeline": True})
                client, tool = self._resolve_tool(tc.name)
                result = await client.call_tool(name=tool.mcp_tool_name, arguments=tc.arguments)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                logger.info(
                    "mcp_tool_call_success %s",
                    bounded_log_payload(
                        trace_id=trace_id,
                        round=round_idx,
                        tool=tc.name,
                        tool_call_id=tc.id,
                        latency_ms=elapsed_ms,
                        server_namespace=tool.server_namespace,
                        server_name=tool.server_name,
                        **_tool_result_observability(result),
                        max_chars=600,
                    ),
                )
                out.append(ChatMessage(role="tool", content=_tool_result_to_text(result), tool_call_id=tc.id))
            except (McpTransportError, McpProtocolError, McpError) as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                logger.warning(
                    "mcp_tool_call_failed %s",
                    bounded_log_payload(
                        trace_id=trace_id,
                        round=round_idx,
                        tool=tc.name,
                        tool_call_id=tc.id,
                        latency_ms=elapsed_ms,
                        error_type=type(exc).__name__,
                        error=str(exc),
                        max_chars=600,
                    ),
                    exc_info=True,
                )
                out.append(
                    ChatMessage(
                        role="tool",
                        content=_tool_result_to_text(
                            {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
                        ),
                        tool_call_id=tc.id,
                    )
                )
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                logger.exception(
                    "mcp_tool_call_unhandled %s",
                    bounded_log_payload(
                        trace_id=trace_id,
                        round=round_idx,
                        tool=tc.name,
                        tool_call_id=tc.id,
                        latency_ms=elapsed_ms,
                        error=str(exc),
                        max_chars=600,
                    ),
                )
                out.append(
                    ChatMessage(
                        role="tool",
                        content=_tool_result_to_text(
                            {"isError": True, "content": [{"type": "text", "text": f"Unhandled tool error: {exc}"}]}
                        ),
                        tool_call_id=tc.id,
                    )
                )
        return out
