from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class ErrorObject(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    error: ErrorObject


class OpenAIChatMessage(BaseModel):
    role: str
    content: Optional[Union[str, Dict[str, Any], List[Dict[str, Any]]]] = ""
    name: Optional[str] = None
    tool_call_id: Optional[str] = None


class OpenAIChatCompletionRequest(BaseModel):
    model: str
    messages: List[OpenAIChatMessage] = Field(min_length=1)
    pipeline_id: str | None = Field(
        default=None,
        description="Optional pipeline identifier to resolve tool and corpus policy.",
    )
    task: Optional[str] = Field(
        default=None,
        description="Optional internal task label such as 'chunking' for policy capability enforcement.",
    )
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = Field(default=None, gt=0)
    context_length: Optional[int] = Field(
        default=None,
        gt=0,
        description="Optional client-requested context window limit. Only honored when enabled by the selected provider definition.",
    )
    tools: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Optional OpenAI-compatible client tools. Only honored for internal chunking requests.",
    )
    tool_choice: Optional[Union[str, Dict[str, Any]]] = Field(
        default=None,
        description="OpenAI tool_choice: 'none' to disable tools, 'auto' for default.",
    )
    response_format: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional response format (e.g. {'type': 'json_object'} or {'type': 'json_schema', ...}).",
    )


class RagQueryRequest(BaseModel):
    query: str = Field(description="Natural language query string.")
    pipeline_id: str | None = Field(
        default=None,
        description="Optional pipeline identifier to resolve policy defaults and allowlists.",
    )
    corpus_id: Optional[str] = Field(
        default=None,
        description="Target corpus identifier. If omitted, uses DEFAULT_CORPUS_ID.",
    )
    filters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional metadata filters applied consistently to retrieval.",
    )
    top_k: Optional[int] = Field(
        default=None,
        gt=0,
        description="Number of chunks to return. If omitted, uses DEFAULT_TOP_K.",
    )


class RagLookupRequest(BaseModel):
    terms: List[str] = Field(
        min_length=1,
        max_length=50,
        description="Exact lexical terms, identifiers, endpoint paths, symbols, or configuration keys to look up.",
    )
    pipeline_id: str | None = Field(
        default=None,
        description="Optional pipeline identifier to resolve policy defaults and allowlists.",
    )
    corpus_id: Optional[str] = Field(
        default=None,
        description="Target corpus identifier. If omitted, uses DEFAULT_CORPUS_ID.",
    )
    filters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional metadata filters applied consistently to lookup.",
    )
    top_k: Optional[int] = Field(
        default=None,
        gt=0,
        description="Maximum chunks to return per lookup term. If omitted, uses 5.",
    )
    max_results: Optional[int] = Field(
        default=None,
        gt=0,
        description="Maximum chunks to return across all terms. If omitted, uses 20.",
    )
