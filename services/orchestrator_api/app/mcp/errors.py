from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class McpError(Exception):
    message: str
    details: Optional[Dict[str, Any]] = None


@dataclass
class McpTransportError(McpError):
    pass


@dataclass
class McpProtocolError(McpError):
    code: Optional[int] = None
