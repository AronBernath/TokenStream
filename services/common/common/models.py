from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional


class Chunk(BaseModel):
    chunk_id: str
    corpus_id: str
    doc_id: str
    title: str
    section_id: Optional[str] = None
    version_date: Optional[str] = None  # ISO date
    language: Optional[str] = None
    jurisdiction: Optional[str] = None
    source_url: Optional[str] = None
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class QueryRequest(BaseModel):
    query: str = Field(description="Natural language query string.")
    corpus_id: str = Field(description="Target corpus identifier. Retrieval never crosses corpora.")
    filters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional metadata filters applied consistently to vector and lexical retrieval.",
    )
    top_k: int = Field(default=8, gt=0, description="Number of chunks to return. Must be > 0.")


class LookupRequest(BaseModel):
    terms: List[str] = Field(
        min_length=1,
        max_length=50,
        description="Exact lexical terms, identifiers, endpoint paths, symbols, or configuration keys to look up.",
    )
    corpus_id: str = Field(description="Target corpus identifier. Lookup never crosses corpora.")
    filters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional metadata filters applied to lexical lookup.",
    )
    top_k: int = Field(default=5, gt=0, description="Maximum chunks to return per lookup term. Must be > 0.")
    max_results: int = Field(default=20, gt=0, description="Maximum chunks to return across all terms. Must be > 0.")


class RetrievedChunk(BaseModel):
    chunk_id: str = Field(description="Stable chunk identifier, unique within corpus at minimum.")
    score: float = Field(description="Comparable retrieval score. Higher means more relevant.")
    text: str = Field(description="Raw chunk text, without prompt formatting.")
    doc_id: str = Field(description="Source document identifier.")
    doc_type: str = Field(description="Document type, e.g. law, decree, guidance.")
    source_url: str = Field(description="Canonical source URL for citations.")
    title: Optional[str] = Field(default=None, description="Document or section title when available.")
    section_id: Optional[str] = Field(default=None, description="Section/article identifier when available.")
    tags: Optional[List[str]] = Field(default=None, description="Document/chunk tags when available.")
    version_date: Optional[str] = Field(default=None, description="Source version date when available.")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional chunk metadata.")


class QueryResponse(BaseModel):
    api_version: str = Field(default="v1", description="API contract version for this payload.")
    answer: str = Field(description="Grounded textual answer synthesized from returned chunks.")
    citations: List[Dict[str, Any]] = Field(description="Citation objects aligned with returned chunks.")
    chunks: List[RetrievedChunk] = Field(description="Portable citation-ready chunks.")


class ErrorObject(BaseModel):
    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable error message.")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Optional structured error details.")


class ErrorResponse(BaseModel):
    error: ErrorObject
