from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


class ProviderCapabilitiesModel(BaseModel):
    tools: bool = True
    json_schema: bool = False
    streaming: bool = True
    chunking: bool = False
    max_context_window: int = 8192
    default_context_window: int = 8192


class ProviderDefinitionModel(BaseModel):
    name: str
    type: str
    base_url: str
    require_api_key: bool = True
    default_model: str = ""
    models: List[str] = Field(default_factory=list)
    capabilities: ProviderCapabilitiesModel = Field(default_factory=ProviderCapabilitiesModel)
    api_key_env: Optional[str] = None
    api_key: Optional[str] = None


class PipelinePolicyModel(BaseModel):
    pipeline_id: str
    default_corpus_id: Optional[str] = None
    allowed_corpus_ids: List[str] = Field(default_factory=list)
    default_filters: Dict[str, Any] = Field(default_factory=dict)
    allowed_tools: List[str] = Field(default_factory=list)
    allowed_providers: Optional[List[str]] = None
    allowed_models: Optional[List[str]] = None
    chunking: Dict[str, Any] = Field(default_factory=dict)
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    max_total_tokens: Optional[int] = None
    max_top_k: Optional[int] = None
    default_provider: Optional[str] = None
    default_model: Optional[str] = None


class ApiKeyEntryModel(BaseModel):
    key_id: str
    key_hash: str
    subject: str
    scopes: List[str] = Field(default_factory=list)
    default_pipeline_id: Optional[str] = None
    allowed_providers: Optional[List[str]] = None
    allowed_models: Optional[List[str]] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    max_total_tokens: Optional[int] = None
    max_top_k: Optional[int] = None


class UserModel(BaseModel):
    username: str
    password_hash: str
    roles: List[str] = Field(default_factory=list)
    is_active: bool = True


class RagSettingsModel(BaseModel):
    default_corpus_id: str = "default"
    default_top_k: int = 8
    retrieval_api_url: str = "http://retrieval-api:8000"
