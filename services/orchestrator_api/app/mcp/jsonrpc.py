from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


JSONRPC_VERSION = "2.0"


@dataclass(frozen=True)
class JsonRpcError:
    code: int
    message: str
    data: Optional[Any] = None


def make_request(*, request_id: int, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def make_notification(*, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def parse_response(data: Any) -> tuple[Any, Optional[JsonRpcError]]:
    """
    Parse a JSON-RPC response object.
    Returns: (result, error)
    """
    if not isinstance(data, dict):
        return None, JsonRpcError(code=-32603, message="Invalid JSON-RPC response", data={"raw": data})
    if "error" in data and isinstance(data.get("error"), dict):
        err = data["error"]
        return None, JsonRpcError(
            code=int(err.get("code", -32603)),
            message=str(err.get("message", "JSON-RPC error")),
            data=err.get("data"),
        )
    return data.get("result"), None
