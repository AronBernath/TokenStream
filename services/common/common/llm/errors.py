from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class LLMError(Exception):
    """
    Shared error type for provider adapters.

    Services should catch this and translate it to their own HTTP error contract.
    """

    code: str
    message: str
    status_code: int
    details: Optional[Dict[str, Any]] = None


def provider_not_configured(provider: str, *, detail: str) -> LLMError:
    return LLMError(
        code="provider_not_configured",
        message=detail,
        status_code=500,
        details={"provider": provider},
    )


def provider_upstream_error(
    provider: str,
    *,
    upstream_status: Optional[int] = None,
    upstream_body: Optional[str] = None,
) -> LLMError:
    details: Dict[str, Any] = {"provider": provider}
    if upstream_status is not None:
        details["upstream_status"] = upstream_status

    # Only include body in details if debug is enabled via env
    import os

    if upstream_body and os.getenv("LOG_LEVEL", "").upper() == "DEBUG":
        details["upstream_body"] = upstream_body

    return LLMError(
        code="provider_error",
        message="Provider returned an error",
        status_code=502,
        details=details,
    )


def provider_request_failed(provider: str, *, detail: Optional[str] = None) -> LLMError:
    details: Dict[str, Any] = {"provider": provider}

    # Only include specific transport error detail if debug is enabled
    import os

    if detail and os.getenv("LOG_LEVEL", "").upper() == "DEBUG":
        details["detail"] = detail

    return LLMError(
        code="provider_error",
        message="Provider request failed",
        status_code=502,
        details=details,
    )
