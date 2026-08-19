import json
import sys
from pathlib import Path

import pytest


SERVICES_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_ROOT = SERVICES_ROOT / "orchestrator_api"
COMMON_ROOT = SERVICES_ROOT / "common"
if str(ORCHESTRATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_ROOT))
if str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

from app.mcp.errors import McpError
from app.mcp.jsonrpc import JsonRpcError, make_notification, make_request, parse_response
from app.mcp.settings import parse_mcp_servers, summarize_mcp_servers


def test_jsonrpc_request_and_notification_omit_empty_params():
    assert make_request(request_id=7, method="tools/list") == {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/list",
    }
    assert make_notification(method="notifications/initialized") == {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }


def test_jsonrpc_parse_response_handles_success_error_and_invalid_payloads():
    assert parse_response({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}) == ({"ok": True}, None)

    result, error = parse_response({"error": {"code": -32001, "message": "denied", "data": {"scope": "tools"}}})
    assert result is None
    assert error == JsonRpcError(code=-32001, message="denied", data={"scope": "tools"})

    result, error = parse_response(["not", "a", "dict"])
    assert result is None
    assert error.code == -32603
    assert error.data == {"raw": ["not", "a", "dict"]}


def test_parse_mcp_servers_supports_streamable_http_and_sse_configs():
    servers = parse_mcp_servers(
        json.dumps(
            [
                {
                    "name": "search",
                    "transport": "streamable_http",
                    "url": "https://mcp.example.test/mcp",
                    "namespace": "knowledge",
                    "headers": {"Authorization": "Bearer token", "x-int": 7},
                },
                {
                    "name": "legacy",
                    "transport": "sse",
                    "url": "https://legacy.example.test/sse",
                },
            ]
        )
    )

    assert servers[0].tool_namespace == "knowledge"
    assert servers[0].headers == {"Authorization": "Bearer token", "x-int": "7"}
    assert servers[1].sse_url == "https://legacy.example.test/sse"
    assert servers[1].messages_url == "https://legacy.example.test/messages"
    assert summarize_mcp_servers(servers)[0]["headers"] == ["Authorization", "x-int"]


@pytest.mark.parametrize(
    "raw_json, match",
    [
        ("{}", "must be a JSON array"),
        (json.dumps([{"transport": "streamable_http", "url": "https://example.test/mcp"}]), "missing name"),
        (json.dumps([{"name": "bad", "transport": "streamable_http"}]), "missing url"),
        (json.dumps([{"name": "bad", "transport": "websocket"}]), "Unsupported MCP transport"),
        (
            json.dumps([{"name": "same", "url": "https://a.test"}, {"name": "same", "url": "https://b.test"}]),
            "Duplicate",
        ),
    ],
)
def test_parse_mcp_servers_rejects_invalid_configs(raw_json, match):
    with pytest.raises(McpError, match=match):
        parse_mcp_servers(raw_json)
