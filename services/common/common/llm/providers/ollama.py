from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Sequence

import httpx

from ..errors import (
    provider_not_configured,
    provider_request_failed,
    provider_upstream_error,
)
from ..types import (
    ChatMessage,
    ChatResponse,
    GenerationParams,
    LLMResponse,
    ToolChoice,
    ToolDefinition,
    coerce_text_content,
)

logger = logging.getLogger("common.llm.providers.ollama")


def _chat_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/v1"):
        # If the user configured the base URL as ending with /v1, strip it to reach the native API
        base = base[:-3]
    return f"{base}/api/chat"


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 429, 500, 502, 503, 504}


class OllamaNativeProvider:
    """
    Adapter for Ollama's native /api/chat endpoint.
    """

    def __init__(
        self,
        *,
        name: str,
        api_key: Optional[str],
        base_url: str,
        default_model: str,
        default_temperature: float = 0.1,
        default_max_tokens: int = 700,
        require_api_key: bool = False,
        timeout_s: float = 45.0,
        max_retries: int = 2,
        retry_backoff_s: float = 0.8,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        self.name = name
        self.default_model = default_model
        self._api_key = api_key
        self._base_url = base_url
        self._default_temperature = default_temperature
        self._default_max_tokens = default_max_tokens
        self._require_api_key = require_api_key
        self._timeout_s = timeout_s
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff_s = max(0.0, float(retry_backoff_s))
        self._extra_headers = extra_headers or {}

    async def chat(
        self,
        *,
        messages: Sequence[ChatMessage],
        params: GenerationParams,
        tools: Optional[Sequence[ToolDefinition]] = None,
        tool_choice: ToolChoice = None,
    ) -> ChatResponse:
        if self._require_api_key and not self._api_key:
            raise provider_not_configured(self.name, detail=f"{self.name} provider is not configured")
        if not self._base_url:
            raise provider_not_configured(self.name, detail=f"{self.name} provider is not configured")

        model = params.model or self.default_model
        if not (model or "").strip():
            raise provider_request_failed(self.name, detail=f"{self.name} provider requires an explicit model")

        ollama_messages: list[Dict[str, Any]] = []
        for m in messages:
            # Ollama native API supports roles: system, user, assistant
            # It also supports tool calls, but we are not implementing them in v1
            role = m.role
            if role == "developer":
                role = "system"
            elif role == "tool":
                # We do not support tools yet, but if we receive a tool message, map it to user
                role = "user"

            content = coerce_text_content(m.content).strip()
            if content:
                ollama_messages.append({"role": role, "content": content})

        options: Dict[str, Any] = {
            "temperature": self._default_temperature if params.temperature is None else params.temperature,
        }

        # Map max_tokens to num_predict
        raw_max = self._default_max_tokens if params.max_tokens is None else params.max_tokens
        if raw_max is not None:
            options["num_predict"] = max(1, int(raw_max))

        # Map context_length to num_ctx
        if params.context_length is not None:
            options["num_ctx"] = int(params.context_length)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": ollama_messages,
            "options": options,
            "stream": False,  # We don't support native streaming yet, rely on replay SSE
        }

        if params.response_format is not None:
            fmt_type = params.response_format.get("type")
            if fmt_type == "json_object":
                payload["format"] = "json"
            elif fmt_type == "json_schema":
                schema = params.response_format.get("json_schema", {}).get("schema")
                if schema:
                    payload["format"] = schema
                else:
                    payload["format"] = "json"

        headers: Dict[str, str] = dict(self._extra_headers)
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        logger.debug(
            "ollama_native_request_payload provider=%s model=%s num_ctx=%s num_predict=%s format=%s",
            self.name,
            model,
            options.get("num_ctx"),
            options.get("num_predict"),
            bool(payload.get("format")),
        )

        url = _chat_url(self._base_url)
        resp: Optional[httpx.Response] = None
        last_transport_error: Optional[Exception] = None
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            for attempt in range(self._max_retries + 1):
                try:
                    resp = await client.post(url, json=payload, headers=headers)
                    if (
                        resp.status_code >= 400
                        and _is_retryable_status(resp.status_code)
                        and attempt < self._max_retries
                    ):
                        await asyncio.sleep(self._retry_backoff_s * (2**attempt))
                        continue
                    break
                except (
                    httpx.ConnectError,
                    httpx.ConnectTimeout,
                    httpx.ReadTimeout,
                    httpx.WriteTimeout,
                    httpx.PoolTimeout,
                    httpx.ReadError,
                    httpx.WriteError,
                    httpx.RemoteProtocolError,
                ) as exc:
                    last_transport_error = exc
                    if attempt < self._max_retries:
                        await asyncio.sleep(self._retry_backoff_s * (2**attempt))
                        continue
                    break
                except httpx.HTTPError as exc:
                    raise provider_request_failed(self.name, detail=str(exc)) from exc

        if resp is None:
            detail = str(last_transport_error) if last_transport_error is not None else None
            raise provider_request_failed(self.name, detail=detail)

        if resp.status_code >= 400:
            body_excerpt = None
            try:
                if resp.content:
                    body_excerpt = resp.text[:800]
            except Exception:
                body_excerpt = None
            raise provider_upstream_error(
                self.name,
                upstream_status=resp.status_code,
                upstream_body=body_excerpt,
            )

        data = resp.json() if resp.content else {}

        msg = data.get("message") if isinstance(data.get("message"), dict) else {}
        content = msg.get("content")
        if content is None:
            content = ""
        text = coerce_text_content(content).strip()

        usage = {}
        if "prompt_eval_count" in data:
            usage["prompt_tokens"] = data["prompt_eval_count"]
        if "eval_count" in data:
            usage["completion_tokens"] = data["eval_count"]
        if "prompt_tokens" in usage and "completion_tokens" in usage:
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]

        response_model = data.get("model") or model
        # Ollama finish reasons: "stop", "length", etc.
        finish_reason = data.get("done_reason") if isinstance(data.get("done_reason"), str) else None
        if finish_reason is None and data.get("done") is True:
            finish_reason = "stop"

        return ChatResponse(
            message=ChatMessage(role="assistant", content=text),
            model=response_model,
            usage=usage,
            tool_calls=[],
            finish_reason=finish_reason,
            raw=data if isinstance(data, dict) else None,
        )

    async def generate(self, *, system_prompt: str, user_prompt: str, params: GenerationParams) -> LLMResponse:
        resp = await self.chat(
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_prompt),
            ],
            params=params,
        )
        return LLMResponse(text=coerce_text_content(resp.message.content).strip(), model=resp.model, usage=resp.usage)
