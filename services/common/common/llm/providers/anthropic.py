from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

import httpx

from ..errors import provider_not_configured, provider_request_failed, provider_upstream_error
from ..types import (
    ChatMessage,
    ChatResponse,
    GenerationParams,
    LLMResponse,
    ToolCall,
    ToolChoice,
    ToolChoiceFunction,
    ToolDefinition,
    coerce_text_content,
)


def _messages_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: Optional[str],
        base_url: str,
        default_model: str,
        default_temperature: float = 0.1,
        default_max_tokens: int = 700,
        anthropic_version: str = "2023-06-01",
        timeout_s: float = 45.0,
        context_length_param: Optional[str] = None,
    ):
        self.default_model = default_model
        self._api_key = api_key
        self._base_url = base_url
        self._default_temperature = default_temperature
        self._default_max_tokens = default_max_tokens
        self._anthropic_version = anthropic_version
        self._timeout_s = timeout_s
        self._context_length_param = (context_length_param or "").strip() or None

    async def chat(
        self,
        *,
        messages: Sequence[ChatMessage],
        params: GenerationParams,
        tools: Optional[Sequence[ToolDefinition]] = None,
        tool_choice: ToolChoice = None,
    ) -> ChatResponse:
        if not self._api_key or not self._base_url:
            raise provider_not_configured(self.name, detail="Anthropic provider is not configured")

        model = params.model or self.default_model
        if not (model or "").strip():
            raise provider_request_failed(self.name, detail=f"{self.name} provider requires an explicit model")

        system_parts: list[str] = []
        msg_payload: list[Dict[str, Any]] = []
        for m in messages:
            if m.role in {"system", "developer"}:
                txt = coerce_text_content(m.content).strip()
                if txt:
                    system_parts.append(txt)
                continue

            if m.role in {"user", "assistant"}:
                # Anthropic "messages" content can be either a plain string (text-only)
                # or an array of content blocks (including tool_use).
                if m.role == "assistant" and m.tool_calls:
                    blocks: list[Dict[str, Any]] = []
                    txt = coerce_text_content(m.content)
                    if isinstance(txt, str) and txt.strip():
                        blocks.append({"type": "text", "text": txt})
                    for tc in m.tool_calls:
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": tc.id,
                                "name": tc.name,
                                "input": tc.arguments or {},
                            }
                        )
                    msg_payload.append({"role": "assistant", "content": blocks})
                elif isinstance(m.content, list) and all(isinstance(x, dict) for x in m.content):
                    msg_payload.append({"role": m.role, "content": m.content})
                else:
                    msg_payload.append({"role": m.role, "content": coerce_text_content(m.content)})
                continue

            if m.role == "tool":
                # Anthropic expects tool results as "user" messages with tool_result blocks.
                tool_use_id = m.tool_call_id or ""
                msg_payload.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": coerce_text_content(m.content),
                            }
                        ],
                    }
                )
                continue

        payload: Dict[str, Any] = {
            "model": model,
            "system": "\n\n".join(system_parts).strip(),
            "messages": msg_payload,
            "temperature": self._default_temperature if params.temperature is None else params.temperature,
            "max_tokens": self._default_max_tokens if params.max_tokens is None else params.max_tokens,
        }
        if params.context_length is not None and self._context_length_param:
            payload[self._context_length_param] = int(params.context_length)

        if tools:
            payload["tools"] = [
                {
                    "name": t.name,
                    **({"description": t.description} if t.description else {}),
                    "input_schema": t.parameters or {},
                }
                for t in tools
            ]

        if tool_choice is not None:
            if isinstance(tool_choice, ToolChoiceFunction):
                payload["tool_choice"] = {"type": "tool", "name": tool_choice.name}
            elif tool_choice == "auto":
                payload["tool_choice"] = {"type": "auto"}
            elif tool_choice == "none":
                payload["tool_choice"] = {"type": "none"}

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self._anthropic_version,
            "content-type": "application/json",
        }

        url = _messages_url(self._base_url)
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            try:
                resp = await client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise provider_request_failed(self.name, detail=str(exc)) from exc

        if resp.status_code >= 400:
            raise provider_upstream_error(self.name, upstream_status=resp.status_code)

        data = resp.json() if resp.content else {}
        content_blocks = data.get("content") if isinstance(data, dict) else None

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        if isinstance(content_blocks, list):
            for item in content_blocks:
                if not isinstance(item, dict):
                    continue
                t = item.get("type")
                if t == "text":
                    txt = item.get("text")
                    if isinstance(txt, str) and txt:
                        text_parts.append(txt)
                elif t == "tool_use":
                    tc_id = item.get("id")
                    name = item.get("name")
                    inp = item.get("input")
                    if isinstance(name, str) and name:
                        tool_calls.append(
                            ToolCall(
                                id=tc_id if isinstance(tc_id, str) and tc_id else f"toolcall_{len(tool_calls)}",
                                name=name,
                                arguments=inp if isinstance(inp, dict) else None,
                                arguments_raw=None,
                            )
                        )

        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        response_model = data.get("model") or model
        finish_reason = data.get("stop_reason") if isinstance(data.get("stop_reason"), str) else None

        return ChatResponse(
            message=ChatMessage(role="assistant", content="\n".join(text_parts).strip()),
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
