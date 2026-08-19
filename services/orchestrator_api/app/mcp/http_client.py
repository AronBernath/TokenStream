from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from .errors import McpProtocolError, McpTransportError
from .jsonrpc import make_notification, make_request, parse_response

logger = logging.getLogger("orchestrator-api.mcp")


@dataclass(frozen=True)
class McpServerInfo:
    name: str
    version: str


class BaseMcpClient:
    async def connect(self) -> None:  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover
        raise NotImplementedError

    async def list_tools(self) -> List[Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    async def call_tool(self, *, name: str, arguments: Optional[Dict[str, Any]]) -> Any:  # pragma: no cover
        raise NotImplementedError


class StreamableHttpMcpClient(BaseMcpClient):
    """
    MCP over Streamable HTTP:
    - Single endpoint for JSON-RPC request/response (typically `/mcp`)
    - Server may optionally stream; we treat it as request/response for our use-cases.
    """

    def __init__(
        self,
        *,
        url: str,
        protocol_version: str,
        headers: Optional[Dict[str, str]] = None,
        timeout_s: float = 45.0,
        client_name: str = "rag-orchestrator",
        client_version: str = "1.0.0",
    ):
        self._url = url
        self._protocol_version = protocol_version
        self._timeout_s = timeout_s
        self._client_name = client_name
        self._client_version = client_version
        self._headers = headers or {}
        self._client: Optional[httpx.AsyncClient] = None
        self._next_id = 1
        self.server_info: Optional[McpServerInfo] = None
        # `mcp-streamablehttp-proxy` returns a per-session id in `mcp-session-id` header
        # on initialize. Subsequent requests must include the same header to use the
        # same underlying stdio session.
        self._session_id: Optional[str] = None

    async def connect(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(timeout=self._timeout_s)
        await self._initialize()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._client = None

    async def _post_json(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self._client is None:
            raise McpTransportError("MCP client not connected")
        headers = {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            **self._headers,
        }
        if self._session_id and "mcp-session-id" not in {k.lower(): v for k, v in headers.items()}:
            headers["mcp-session-id"] = self._session_id
        expected_id = payload.get("id")
        try:
            async with self._client.stream("POST", self._url, json=payload, headers=headers) as resp:
                # Capture/refresh session id (proxy returns it on initialize and may repeat it).
                sid = resp.headers.get("mcp-session-id")
                if isinstance(sid, str) and sid.strip():
                    self._session_id = sid.strip()
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise McpTransportError(
                        "MCP server returned an error",
                        details={
                            "url": self._url,
                            "status_code": resp.status_code,
                            "body": (
                                body.decode("utf-8", errors="replace")
                                if isinstance(body, (bytes, bytearray))
                                else str(body)
                            )[:500],
                        },
                    )

                ctype = (resp.headers.get("content-type") or "").lower()
                if "text/event-stream" in ctype:
                    # Streamable HTTP may stream progress notifications via SSE.
                    buf: list[str] = []
                    last_jsonrpc: Optional[Dict[str, Any]] = None
                    async for line in resp.aiter_lines():
                        if line is None:
                            continue
                        if not line.strip():
                            if not buf:
                                continue
                            data_str = "\n".join(buf).strip()
                            buf.clear()
                            try:
                                msg = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            if not isinstance(msg, dict):
                                continue

                            # Some servers return JSON-RPC ids as strings even if we send ints.
                            # Also, some proxies stream multiple events but only one final JSON-RPC
                            # response; keep the last JSON-RPC-looking message as a fallback.
                            if "result" in msg or "error" in msg:
                                last_jsonrpc = msg

                            msg_id = msg.get("id")
                            if expected_id is not None and msg_id is not None and str(msg_id) == str(expected_id):
                                return msg
                            continue
                        if line.startswith("data:"):
                            buf.append(line[len("data:") :].lstrip())
                    return last_jsonrpc or {}

                body = await resp.aread()
        except httpx.HTTPError as exc:
            raise McpTransportError("MCP HTTP request failed", details={"url": self._url, "error": str(exc)}) from exc

        if not body:
            return {}
        try:
            return json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise McpTransportError(
                "MCP server returned invalid JSON",
                details={
                    "url": self._url,
                    "body": body.decode("utf-8", errors="replace")[:500],
                },
            ) from exc

    async def _request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        rid = self._next_id
        self._next_id += 1
        payload = make_request(request_id=rid, method=method, params=params)
        data = await self._post_json(payload)
        result, err = parse_response(data)
        if err is not None:
            # `mcp-streamablehttp-proxy` uses per-session state and may evict idle sessions.
            # If we get an "Invalid session ID" error, re-initialize once and retry.
            if method != "initialize" and err.code == -32002:
                logger.info("mcp_session_expired retrying method=%s url=%s", method, self._url)
                self._session_id = None
                await self._initialize()
                data = await self._post_json(payload)
                result, err = parse_response(data)
                if err is None:
                    return result
            raise McpProtocolError(
                "MCP JSON-RPC error", details={"method": method, "error": err.__dict__}, code=err.code
            )
        return result

    async def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        payload = make_notification(method=method, params=params)
        # Best-effort: notifications may or may not get an HTTP response body.
        try:
            await self._post_json(payload)
        except McpTransportError:
            # Don't fail the entire connection on notify.
            logger.debug("mcp_notify_failed method=%s url=%s", method, self._url, exc_info=True)

    async def _initialize(self) -> None:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": self._protocol_version,
                "capabilities": {"roots": {"listChanged": False}, "sampling": {}},
                "clientInfo": {"name": self._client_name, "version": self._client_version},
            },
        )
        server_info = result.get("serverInfo") if isinstance(result, dict) else None
        if isinstance(server_info, dict):
            name = str(server_info.get("name") or "").strip() or "unknown"
            version = str(server_info.get("version") or "").strip() or "unknown"
            self.server_info = McpServerInfo(name=name, version=version)
        await self._notify("notifications/initialized")

    async def list_tools(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = await self._request("tools/list", params)
            if not isinstance(result, dict):
                raise McpProtocolError(
                    "MCP tools/list result must be an object", details={"result_type": type(result).__name__}
                )
            page = result.get("tools")
            if isinstance(page, list):
                tools.extend([t for t in page if isinstance(t, dict)])
            next_cursor = result.get("nextCursor")
            cursor = next_cursor if isinstance(next_cursor, str) and next_cursor else None
            if not cursor:
                break
        return tools

    async def call_tool(self, *, name: str, arguments: Optional[Dict[str, Any]]) -> Any:
        result = await self._request("tools/call", {"name": name, "arguments": arguments or {}})
        return result


class SseMcpClient(BaseMcpClient):
    """
    Legacy MCP over SSE:
    - Client opens an SSE stream (GET /sse)
    - Client sends JSON-RPC messages via POST /messages
    - Server responses arrive over SSE as JSON-RPC objects.
    """

    def __init__(
        self,
        *,
        sse_url: str,
        messages_url: str,
        protocol_version: str,
        headers: Optional[Dict[str, str]] = None,
        timeout_s: float = 45.0,
        client_name: str = "rag-orchestrator",
        client_version: str = "1.0.0",
    ):
        self._sse_url = sse_url
        self._messages_url = messages_url
        self._protocol_version = protocol_version
        self._timeout_s = timeout_s
        self._client_name = client_name
        self._client_version = client_version
        self._headers = headers or {}

        self._client: Optional[httpx.AsyncClient] = None
        self._next_id = 1
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._pending: Dict[int, asyncio.Future[Any]] = {}

        self.server_info: Optional[McpServerInfo] = None

    async def connect(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(timeout=self._timeout_s)
        self._reader_task = asyncio.create_task(self._read_sse_loop())
        await self._initialize()

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        if self._client is not None:
            await self._client.aclose()
        self._client = None

    async def _read_sse_loop(self) -> None:
        if self._client is None:
            return
        headers = {"accept": "text/event-stream", **self._headers}
        try:
            async with self._client.stream("GET", self._sse_url, headers=headers) as resp:
                if resp.status_code >= 400:
                    raise McpTransportError(
                        "MCP SSE connection failed",
                        details={
                            "url": self._sse_url,
                            "status_code": resp.status_code,
                            "body": (await resp.aread())[:200],
                        },
                    )
                buf: list[str] = []
                async for line in resp.aiter_lines():
                    if line is None:
                        continue
                    if not line.strip():
                        if buf:
                            data_str = "\n".join(buf).strip()
                            buf.clear()
                            await self._handle_sse_data(data_str)
                        continue
                    if line.startswith("data:"):
                        buf.append(line[len("data:") :].lstrip())
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("mcp_sse_reader_crashed sse_url=%s", self._sse_url)
            # Fail all pending futures.
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(McpTransportError("MCP SSE reader crashed"))
            self._pending.clear()

    async def _handle_sse_data(self, data_str: str) -> None:
        try:
            msg = json.loads(data_str)
        except json.JSONDecodeError:
            return
        if not isinstance(msg, dict):
            return
        msg_id = msg.get("id")
        if not isinstance(msg_id, int):
            return
        fut = self._pending.get(msg_id)
        if fut is None or fut.done():
            return
        result, err = parse_response(msg)
        if err is not None:
            fut.set_exception(McpProtocolError("MCP JSON-RPC error", details={"error": err.__dict__}, code=err.code))
        else:
            fut.set_result(result)

    async def _post_message(self, payload: Dict[str, Any]) -> None:
        if self._client is None:
            raise McpTransportError("MCP client not connected")
        headers = {"content-type": "application/json", **self._headers}
        try:
            resp = await self._client.post(self._messages_url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise McpTransportError(
                "MCP POST /messages failed", details={"url": self._messages_url, "error": str(exc)}
            ) from exc
        if resp.status_code >= 400:
            raise McpTransportError(
                "MCP /messages returned an error",
                details={"url": self._messages_url, "status_code": resp.status_code, "body": (resp.text or "")[:500]},
            )

    async def _request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        rid = self._next_id
        self._next_id += 1
        payload = make_request(request_id=rid, method=method, params=params)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[rid] = fut
        await self._post_message(payload)
        try:
            return await asyncio.wait_for(fut, timeout=self._timeout_s)
        finally:
            self._pending.pop(rid, None)

    async def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        payload = make_notification(method=method, params=params)
        await self._post_message(payload)

    async def _initialize(self) -> None:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": self._protocol_version,
                "capabilities": {"roots": {"listChanged": False}, "sampling": {}},
                "clientInfo": {"name": self._client_name, "version": self._client_version},
            },
        )
        server_info = result.get("serverInfo") if isinstance(result, dict) else None
        if isinstance(server_info, dict):
            name = str(server_info.get("name") or "").strip() or "unknown"
            version = str(server_info.get("version") or "").strip() or "unknown"
            self.server_info = McpServerInfo(name=name, version=version)
        await self._notify("notifications/initialized")

    async def list_tools(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = await self._request("tools/list", params)
            if not isinstance(result, dict):
                raise McpProtocolError(
                    "MCP tools/list result must be an object", details={"result_type": type(result).__name__}
                )
            page = result.get("tools")
            if isinstance(page, list):
                tools.extend([t for t in page if isinstance(t, dict)])
            next_cursor = result.get("nextCursor")
            cursor = next_cursor if isinstance(next_cursor, str) and next_cursor else None
            if not cursor:
                break
        return tools

    async def call_tool(self, *, name: str, arguments: Optional[Dict[str, Any]]) -> Any:
        result = await self._request("tools/call", {"name": name, "arguments": arguments or {}})
        return result
