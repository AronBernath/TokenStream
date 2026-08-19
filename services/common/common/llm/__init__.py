"""
Shared LLM provider abstractions and adapters.

This package is intended to be imported by multiple services (e.g. orchestrator-api)
to avoid duplicating provider adapter logic.
"""

from .errors import LLMError
from .types import (
    ChatMessage,
    ChatResponse,
    GenerationParams,
    LLMProvider,
    LLMResponse,
    ToolCall,
    ToolChoiceFunction,
    ToolDefinition,
)

__all__ = [
    "LLMError",
    "ChatMessage",
    "ChatResponse",
    "GenerationParams",
    "LLMProvider",
    "LLMResponse",
    "ToolCall",
    "ToolChoiceFunction",
    "ToolDefinition",
]
