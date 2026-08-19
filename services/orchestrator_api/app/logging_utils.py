from __future__ import annotations

from typing import Any, Dict, Optional


def bounded_log_payload(*, max_chars: int, **fields: Any) -> Dict[str, Any]:
    """
    Truncate long text fields before logging to avoid huge logs.
    """

    out: Dict[str, Any] = {}
    for k, v in fields.items():
        if isinstance(v, str) and max_chars > 0 and len(v) > max_chars:
            out[k] = v[:max_chars] + "…"
        else:
            out[k] = v
    return out


def bounded_response_payload(
    *,
    max_chars: int,
    content: Optional[str] = None,
    **fields: Any,
) -> Dict[str, Any]:
    out = dict(fields)
    if isinstance(content, str):
        out["content"] = content[:max_chars] + ("…" if max_chars > 0 and len(content) > max_chars else "")
    return out
