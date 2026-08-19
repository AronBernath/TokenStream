from __future__ import annotations

import asyncio
import json
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
    ToolCall,
    ToolChoiceFunction,
    ToolDefinition,
    ToolChoice,
    coerce_text_content,
    try_parse_json,
)

logger = logging.getLogger("common.llm.providers.openai_compat")


def _chat_completions_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _openai_requires_max_completion_tokens(model: str) -> bool:
    """gpt-5.x (and o1/o3/o4) require max_completion_tokens; gpt-4.x use max_tokens."""
    if not (model or "").strip():
        return False
    m = model.strip().lower()
    if m.startswith("gpt-5") or m.startswith("o1-") or m.startswith("o3") or m.startswith("o4"):
        return True
    return False


def _is_retryable_status(status_code: int) -> bool:
    # Retry transient upstream conditions that often recover quickly.
    return status_code in {408, 409, 429, 500, 502, 503, 504}


class OpenAICompatibleProvider:
    """
    Adapter for OpenAI-compatible Chat Completions APIs.

    Works for:
    - OpenAI
    - DeepSeek (OpenAI-compatible gateway)
    - many local OpenAI-compatible gateways
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
        require_api_key: bool = True,
        timeout_s: float = 45.0,
        max_retries: int = 2,
        retry_backoff_s: float = 0.8,
        extra_headers: Optional[Dict[str, str]] = None,
        use_max_completion_tokens: bool = False,
        context_length_param: Optional[str] = None,
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
        self._use_max_completion_tokens = use_max_completion_tokens
        self._context_length_param = (context_length_param or "").strip() or None

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
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    **({"name": m.name} if m.name else {}),
                    **({"tool_call_id": m.tool_call_id} if (m.role == "tool" and m.tool_call_id) else {}),
                    **(
                        {
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.name,
                                        "arguments": (
                                            tc.arguments_raw
                                            if isinstance(tc.arguments_raw, str) and tc.arguments_raw.strip()
                                            else json.dumps(tc.arguments or {}, ensure_ascii=False)
                                        ),
                                    },
                                }
                                for tc in (m.tool_calls or [])
                            ]
                        }
                        if (m.role == "assistant" and m.tool_calls)
                        else {}
                    ),
                }
                for m in messages
            ],
            "temperature": self._default_temperature if params.temperature is None else params.temperature,
        }
        # gpt-5.x want max_completion_tokens; gpt-4.x use max_tokens. Never send 0 or missing.
        raw_max = self._default_max_tokens if params.max_tokens is None else params.max_tokens
        max_val = max(1, int(raw_max)) if raw_max is not None else self._default_max_tokens
        use_completion = self._use_max_completion_tokens and _openai_requires_max_completion_tokens(model)
        if use_completion:
            payload["max_completion_tokens"] = max_val
        else:
            payload["max_tokens"] = max_val

        if params.response_format is not None:
            payload["response_format"] = params.response_format
        if params.context_length is not None and self._context_length_param:
            payload[self._context_length_param] = int(params.context_length)

        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        **({"description": t.description} if t.description else {}),
                        "parameters": t.parameters or {},
                    },
                }
                for t in tools
            ]

        if tool_choice is not None:
            if isinstance(tool_choice, ToolChoiceFunction):
                payload["tool_choice"] = {"type": "function", "function": {"name": tool_choice.name}}
            else:
                payload["tool_choice"] = tool_choice  # "auto" | "none"

        headers: Dict[str, str] = dict(self._extra_headers)
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        logger.debug(
            "openai_compat_request_payload provider=%s model=%s context_param=%s context_value=%s tools=%d tool_choice=%s max_tokens=%s response_format=%s",
            self.name,
            model,
            self._context_length_param or None,
            payload.get(self._context_length_param) if self._context_length_param else None,
            len(payload.get("tools", []) if isinstance(payload.get("tools"), list) else []),
            payload.get("tool_choice"),
            payload.get("max_completion_tokens", payload.get("max_tokens")),
            bool(payload.get("response_format")),
        )

        url = _chat_completions_url(self._base_url)
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
        choice = (data.get("choices") or [{}])[0] if isinstance(data.get("choices"), list) else {}
        msg = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        # Use .get("content") not .get("content", "") so null content is detected (not silently "").
        content = msg.get("content")
        if not content:
            # DeepSeek R1 (deepseek-reasoner) returns "" for content and puts the answer in
            # reasoning_content.  Fall back to it so R1 responses aren't silently dropped.
            fallback = msg.get("reasoning_content")
            if fallback and str(fallback).strip():
                content = fallback
        if content is None:
            content = ""
        text = coerce_text_content(content).strip()

        tool_calls: list[ToolCall] = []
        raw_tool_calls = msg.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            for tc in raw_tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = fn.get("name")
                if not isinstance(name, str) or not name:
                    continue
                args_dict, args_raw = try_parse_json(fn.get("arguments"))
                tc_id = tc.get("id")
                tool_calls.append(
                    ToolCall(
                        id=tc_id if isinstance(tc_id, str) and tc_id else f"toolcall_{len(tool_calls)}",
                        name=name,
                        arguments=args_dict,
                        arguments_raw=args_raw,
                    )
                )

        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        response_model = data.get("model") or model
        finish_reason = choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None

        return ChatResponse(
            message=ChatMessage(role="assistant", content=text),
            model=response_model,
            usage=usage,
            tool_calls=tool_calls,
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
