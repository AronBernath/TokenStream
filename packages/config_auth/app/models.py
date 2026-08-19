from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

_CONTAINER_COMMON_ROOT = Path(__file__).resolve().parents[2] / "common"
_CHECKOUT_COMMON_ROOT = Path(__file__).resolve().parents[3] / "services" / "common"
for _COMMON_ROOT in (_CONTAINER_COMMON_ROOT, _CHECKOUT_COMMON_ROOT):
    if (_COMMON_ROOT / "common").exists() and str(_COMMON_ROOT) not in sys.path:
        sys.path.insert(0, str(_COMMON_ROOT))
        break

from common.registry_validation import normalize_corpus_id, normalize_source_id, validate_source_definition


def _normalize_processor_id(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) > 160:
        raise ValueError("processor_id must be 160 characters or fewer")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
    if any(ch not in allowed for ch in text):
        raise ValueError("processor_id may only contain letters, numbers, '.', '_', ':', and '-'")
    return text


def _normalize_retrieval_profile_id(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) > 160:
        raise ValueError("retrieval_profile_id must be 160 characters or fewer")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
    if any(ch not in allowed for ch in text):
        raise ValueError("retrieval_profile_id may only contain letters, numbers, '.', '_', ':', and '-'")
    return text


ROLE_PERMISSIONS: Dict[str, List[str]] = {
    "admin": [
        "providers:read",
        "providers:write",
        "policies:read",
        "policies:write",
        "processors:read",
        "processors:write",
        "retrieval:read",
        "retrieval:write",
        "keys:read",
        "keys:write",
        "users:read",
        "users:write",
        "rag:read",
        "rag:write",
        "corpora:read",
        "corpora:write",
        "mcp:read",
        "mcp:write",
        "status:read",
    ],
    "operator": [
        "providers:read",
        "providers:write",
        "policies:read",
        "policies:write",
        "processors:read",
        "processors:write",
        "retrieval:read",
        "retrieval:write",
        "keys:read",
        "keys:write",
        "rag:read",
        "rag:write",
        "corpora:read",
        "corpora:write",
        "mcp:read",
        "mcp:write",
        "status:read",
    ],
    "viewer": [
        "providers:read",
        "policies:read",
        "processors:read",
        "retrieval:read",
        "keys:read",
        "users:read",
        "rag:read",
        "corpora:read",
        "mcp:read",
        "status:read",
    ],
    "service": [
        "status:read",
    ],
}


class LoginRequest(BaseModel):
    username: str
    password: str


class UserSession(BaseModel):
    username: str
    roles: List[str] = Field(default_factory=list)
    permissions: List[str] = Field(default_factory=list)
    must_rotate_password: bool = False
    auth_type: str = "session"


class ProviderCapabilitiesModel(BaseModel):
    tools: bool = True
    json_schema: bool = False
    streaming: bool = True
    chunking: bool = False
    max_context_window: int = 8192
    default_context_window: int = 8192


class ProviderClientControlsModel(BaseModel):
    temperature: bool = True
    max_tokens: bool = True
    context_length: bool = False
    context_length_param: Optional[str] = None


class ProviderRecord(BaseModel):
    name: str
    type: str
    base_url: str
    require_api_key: bool = True
    default_model: str = ""
    models: List[str] = Field(default_factory=list)
    capabilities: ProviderCapabilitiesModel = Field(default_factory=ProviderCapabilitiesModel)
    client_controls: ProviderClientControlsModel = Field(default_factory=ProviderClientControlsModel)
    secret_ref: Optional[str] = None
    secret_source_type: Optional[str] = None
    has_secret_ref: bool = False


class PolicyRecord(BaseModel):
    pipeline_id: str
    default_corpus_id: Optional[str] = None
    allowed_corpus_ids: List[str] = Field(default_factory=list)
    default_filters: Dict[str, Any] = Field(default_factory=dict)
    allowed_tools: List[str] = Field(default_factory=list)
    allowed_providers: Optional[List[str]] = None
    allowed_models: Optional[List[str]] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    max_total_tokens: Optional[int] = None
    max_top_k: Optional[int] = None
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    chunking: Dict[str, Any] = Field(default_factory=dict)


class ProcessorRecord(BaseModel):
    processor_id: str
    type: str = "generic"
    name: Optional[str] = None
    description: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("processor_id")
    @classmethod
    def validate_processor_id(cls, value: str) -> str:
        normalized = _normalize_processor_id(value)
        if not normalized:
            raise ValueError("processor_id is required")
        return normalized

    @field_validator("type")
    @classmethod
    def validate_processor_type(cls, value: str) -> str:
        text = str(value or "generic").strip().lower()
        if not text:
            raise ValueError("type is required")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
        if any(ch not in allowed for ch in text):
            raise ValueError("type may only contain letters, numbers, '.', '_', ':', and '-'")
        return text


class RetrievalProfileRecord(BaseModel):
    retrieval_profile_id: str
    type: str = "hybrid"
    name: Optional[str] = None
    description: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("retrieval_profile_id")
    @classmethod
    def validate_retrieval_profile_id(cls, value: str) -> str:
        normalized = _normalize_retrieval_profile_id(value)
        if not normalized:
            raise ValueError("retrieval_profile_id is required")
        return normalized

    @field_validator("type")
    @classmethod
    def validate_retrieval_type(cls, value: str) -> str:
        text = str(value or "hybrid").strip().lower()
        if not text:
            raise ValueError("type is required")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
        if any(ch not in allowed for ch in text):
            raise ValueError("type may only contain letters, numbers, '.', '_', ':', and '-'")
        return text


class ApiKeyRead(BaseModel):
    key_id: str
    subject: str
    scopes: List[str] = Field(default_factory=list)
    default_pipeline_id: Optional[str] = None
    allowed_providers: Optional[List[str]] = None
    allowed_models: Optional[List[str]] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    max_total_tokens: Optional[int] = None
    max_top_k: Optional[int] = None
    is_active: bool = True
    created_at: str


class ApiKeyCreateRequest(BaseModel):
    subject: str
    scopes: List[str] = Field(default_factory=list)
    default_pipeline_id: Optional[str] = None
    allowed_providers: Optional[List[str]] = None
    allowed_models: Optional[List[str]] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    max_total_tokens: Optional[int] = None
    max_top_k: Optional[int] = None


class ApiKeyCreateResponse(BaseModel):
    key_id: str
    plaintext_key: str
    entry: ApiKeyRead


class UserRead(BaseModel):
    username: str
    roles: List[str] = Field(default_factory=list)
    is_active: bool = True
    must_rotate_password: bool = False
    is_bootstrap: bool = False


class UserWrite(BaseModel):
    username: str
    roles: List[str] = Field(default_factory=list)
    is_active: bool = True
    password: Optional[str] = None
    must_rotate_password: bool = False
    is_bootstrap: bool = False


class RagSettingsModel(BaseModel):
    default_corpus_id: str = "default"
    selected_corpus_ids: List[str] = Field(default_factory=list)
    default_top_k: int = 8
    retrieval_api_url: str = "http://retrieval-api:8000"


class McpServerRecord(BaseModel):
    name: str
    transport: str = "streamable_http"
    url: Optional[str] = None
    sse_url: Optional[str] = None
    messages_url: Optional[str] = None
    namespace: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)


class McpSettingsModel(BaseModel):
    selected_servers: List[str] = Field(default_factory=list)
    servers: List[McpServerRecord] = Field(default_factory=list)
    timeout_s: float = 45.0
    strict: bool = False
    max_tool_rounds: int = 6


class CorpusCatalog(BaseModel):
    corpora: List[str] = Field(default_factory=list)


class CorpusSourceRecord(BaseModel):
    id: str
    type: str
    format: str
    title: Optional[str] = None
    path: Optional[str] = None
    local_path: Optional[str] = None
    url: Optional[str] = None
    object_uri: Optional[str] = None
    content_hash: Optional[str] = None
    size_bytes: Optional[int] = None
    content_type: Optional[str] = None
    language: Optional[str] = None
    doc_type: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    configuration: Dict[str, Any] = Field(default_factory=dict)
    processor_id: Optional[str] = None
    processor_config: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    deleted_at: Optional[str] = None


class CorpusSourceCreateRequest(BaseModel):
    source_id: str
    type: str
    format: str
    title: Optional[str] = None
    url: Optional[str] = None
    object_uri: Optional[str] = None
    content_type: Optional[str] = None
    language: Optional[str] = None
    doc_type: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    configuration: Dict[str, Any] = Field(default_factory=dict)
    processor_id: Optional[str] = None
    processor_config: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        return normalize_source_id(value)

    @field_validator("processor_id")
    @classmethod
    def validate_processor_id(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_processor_id(value)

    @model_validator(mode="after")
    def validate_source(self) -> "CorpusSourceCreateRequest":
        validate_source_definition(self.model_dump())
        return self


class CorpusCreateRequest(BaseModel):
    corpus_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    environment: Optional[str] = None
    tenant_id: Optional[str] = None
    chunking: Dict[str, Any] = Field(default_factory=dict)
    index: Dict[str, Any] = Field(default_factory=dict)
    processor_id: Optional[str] = None
    processor_config: Dict[str, Any] = Field(default_factory=dict)
    retrieval_profile_id: Optional[str] = None
    retrieval_config: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("corpus_id")
    @classmethod
    def validate_corpus_id(cls, value: str) -> str:
        return normalize_corpus_id(value)

    @field_validator("processor_id")
    @classmethod
    def validate_processor_id(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_processor_id(value)

    @field_validator("retrieval_profile_id")
    @classmethod
    def validate_retrieval_profile_id(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_retrieval_profile_id(value)


class CorpusEnsureRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    environment: Optional[str] = None
    tenant_id: Optional[str] = None
    chunking: Dict[str, Any] = Field(default_factory=dict)
    index: Dict[str, Any] = Field(default_factory=dict)
    processor_id: Optional[str] = None
    processor_config: Dict[str, Any] = Field(default_factory=dict)
    retrieval_profile_id: Optional[str] = None
    retrieval_config: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("processor_id")
    @classmethod
    def validate_processor_id(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_processor_id(value)

    @field_validator("retrieval_profile_id")
    @classmethod
    def validate_retrieval_profile_id(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_retrieval_profile_id(value)


class CorpusUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    environment: Optional[str] = None
    tenant_id: Optional[str] = None
    chunking: Optional[Dict[str, Any]] = None
    index: Optional[Dict[str, Any]] = None
    processor_id: Optional[str] = None
    processor_config: Optional[Dict[str, Any]] = None
    retrieval_profile_id: Optional[str] = None
    retrieval_config: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

    @field_validator("processor_id")
    @classmethod
    def validate_processor_id(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_processor_id(value)

    @field_validator("retrieval_profile_id")
    @classmethod
    def validate_retrieval_profile_id(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_retrieval_profile_id(value)


class CorpusDetail(BaseModel):
    corpus_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    environment: Optional[str] = None
    tenant_id: Optional[str] = None
    chunking: Dict[str, Any] = Field(default_factory=dict)
    index: Dict[str, Any] = Field(default_factory=dict)
    processor_id: Optional[str] = None
    processor_config: Dict[str, Any] = Field(default_factory=dict)
    retrieval_profile_id: Optional[str] = None
    retrieval_config: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source_count: int = 0
    sources: List[CorpusSourceRecord] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    deleted_at: Optional[str] = None


class CorpusRegistryBundle(BaseModel):
    schema_version: str = "config-auth.corpus-registry.v1"
    exported_at: Optional[str] = None
    corpus: CorpusDetail
    notes: List[str] = Field(default_factory=list)


class CorpusRegistryImportRequest(BaseModel):
    bundle: CorpusRegistryBundle
    conflict_strategy: str = "fail"

    @field_validator("conflict_strategy")
    @classmethod
    def validate_conflict_strategy(cls, value: str) -> str:
        strategy = str(value or "fail").strip().lower()
        if strategy not in {"fail", "replace"}:
            raise ValueError("conflict_strategy must be 'fail' or 'replace'")
        return strategy


class CorpusRegistryImportResult(BaseModel):
    status: str
    corpus_id: str
    sources_imported: int = 0
    conflict_strategy: str = "fail"
    notes: List[str] = Field(default_factory=list)


class IngestionJobCreateRequest(BaseModel):
    pipeline_id: Optional[str] = None
    source_ids: Optional[List[str]] = None
    force_reembed: bool = False
    processor_id: Optional[str] = None
    processor_config: Dict[str, Any] = Field(default_factory=dict)
    configuration: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("processor_id")
    @classmethod
    def validate_processor_id(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_processor_id(value)


class IngestionJobStatus(BaseModel):
    job_id: str
    corpus_id: str
    environment: Optional[str] = None
    tenant_id: Optional[str] = None
    status: str
    request: Dict[str, Any] = Field(default_factory=dict)
    plan: Dict[str, Any] = Field(default_factory=dict)
    stats: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CorpusReadiness(BaseModel):
    corpus_id: str
    ready: bool
    status: str
    source_count: int = 0
    latest_source_updated_at: Optional[str] = None
    latest_completed_job_id: Optional[str] = None
    latest_completed_job_updated_at: Optional[str] = None
    latest_job_id: Optional[str] = None
    latest_job_status: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)


class IngestionJobClaimRequest(BaseModel):
    worker_id: str


class IngestionJobUpdateRequest(BaseModel):
    status: Optional[str] = None
    stats: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    @model_validator(mode="after")
    def validate_nonempty_update(self) -> "IngestionJobUpdateRequest":
        if self.status is None and self.stats is None and self.error is None:
            raise ValueError("at least one job update field is required")
        return self


class AdminStatus(BaseModel):
    ok: bool
    users_count: int
    providers_count: int
    policies_count: int
    machine_keys_count: int
    mcp_servers_count: int
    default_corpus_id: str
    retrieval_api_url: str
