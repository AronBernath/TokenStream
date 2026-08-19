from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Protocol, Sequence, Union


ChatRole = Literal["system", "developer", "user", "assistant", "tool"]


@dataclass(frozen=True)
class GenerationParams:
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    context_length: Optional[int] = None
    response_format: Optional[Dict[str, Any]] = None


@dataclass
class LLMResponse:
    """
    Legacy/simple text-only generation result.

    This is intentionally shaped like the legacy text-only response type so that
    older services can keep using `generate(system_prompt, user_prompt, ...)`.
    """

    text: str
    model: str
    usage: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)  # JSON Schema


@dataclass(frozen=True)
class ToolChoiceFunction:
    """
    Tool-choice directive: force calling a specific tool by name.
    """

    name: str


ToolChoice = Optional[Union[Literal["auto", "none"], ToolChoiceFunction]]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Optional[Dict[str, Any]] = None
    arguments_raw: Optional[str] = None


@dataclass
class ChatMessage:
    role: ChatRole
    content: Any = ""
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    # When the assistant requests tool calls (OpenAI tool_calls / Anthropic tool_use),
    # we need to preserve them in the conversation history for subsequent turns.
    tool_calls: Optional[List[ToolCall]] = None


@dataclass
class ChatResponse:
    message: ChatMessage
    model: str
    usage: Dict[str, Any] = field(default_factory=dict)
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class LLMProvider(Protocol):
    """
    Shared provider interface.

    - `chat(...)` supports multi-turn chat and tool calling.
    - `generate(...)` is a convenience wrapper for older prompt-only flows.
    """

    name: str
    default_model: str

    async def chat(
        self,
        *,
        messages: Sequence[ChatMessage],
        params: GenerationParams,
        tools: Optional[Sequence[ToolDefinition]] = None,
        tool_choice: ToolChoice = None,
    ) -> ChatResponse: ...

    async def generate(self, *, system_prompt: str, user_prompt: str, params: GenerationParams) -> LLMResponse: ...


def coerce_text_content(content: Any) -> str:
    """
    Best-effort coercion of various "content" shapes into plain text.

    Supports:
    - plain string
    - {"type": "text", "text": "..."}
    - [{"type": "text", "text": "..."}, ...]
    """

    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if isinstance(content.get("text"), str) and content.get("type") in ("text", "output_text"):
            return content["text"]
        return ""
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("text"), str) and item.get("type") in ("text", "output_text"):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def try_parse_json(raw: Any) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    if isinstance(raw, dict):
        return raw, None
    if not isinstance(raw, str):
        return None, None
    s = raw.strip()
    if not s:
        return None, raw
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError:
        return None, raw
    if isinstance(parsed, dict):
        return parsed, raw
    # Tool arguments should be an object; keep raw if it isn't.
    return None, raw
