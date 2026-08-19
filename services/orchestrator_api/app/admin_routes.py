import uuid
import hashlib
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .admin_models import (
    ProviderDefinitionModel,
    PipelinePolicyModel,
    ApiKeyEntryModel,
    UserModel,
    RagSettingsModel,
)
from .admin_config import admin_config_store
from .auth import AuthContext
from .config import load_settings
from .provider_registry import build_provider_registry
from .pipeline import PipelineRegistry
from .auth import AuthRegistry
import app.main as main_module
from fastapi import Header


def require_admin_scope(authorization: Optional[str] = Header(None)) -> AuthContext:
    if not authorization:
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})
    ctx = main_module.auth_registry.authenticate(authorization)
    if not ctx:
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})
    if not ctx.has_scope("admin:*"):
        raise HTTPException(status_code=403, detail={"error": "forbidden: requires admin:* scope"})
    return ctx


router = APIRouter(prefix="/v1/admin", tags=["Admin"])


def reload_runtime():
    """Reloads the runtime registries from the config store and environment."""
    # We update the globals in main_module
    settings = load_settings()
    main_module.settings = settings
    main_module.providers = build_provider_registry(settings)
    main_module.pipeline_registry = PipelineRegistry.load(default_corpus_id=settings.default_corpus_id)
    main_module.auth_registry = AuthRegistry.load(legacy_key=settings.service_api_key)

    # Re-init rag tooling with new settings
    from .rag_tools import RagTooling

    main_module.rag_tooling = RagTooling(settings=settings)


# -- Providers --


@router.get("/providers", response_model=List[ProviderDefinitionModel])
def get_providers(_: AuthContext = Depends(require_admin_scope)):
    return admin_config_store.load_providers()


@router.put("/providers", response_model=List[ProviderDefinitionModel])
def update_providers(providers: List[ProviderDefinitionModel], _: AuthContext = Depends(require_admin_scope)):
    admin_config_store.save_providers(providers)
    reload_runtime()
    return providers


class ProviderTestRequest(BaseModel):
    provider: ProviderDefinitionModel


@router.post("/providers/test")
async def test_provider(req: ProviderTestRequest, _: AuthContext = Depends(require_admin_scope)):
    # Try to instantiate the provider and call models or chat
    from common.llm.providers.openai_compat import OpenAICompatibleProvider
    from common.llm.providers.anthropic import AnthropicProvider
    from common.llm.providers.ollama import OllamaNativeProvider

    pdef = req.provider
    try:
        if pdef.type == "openai_compat":
            _provider = OpenAICompatibleProvider(
                name=pdef.name,
                api_key=pdef.api_key or "dummy",
                base_url=pdef.base_url,
                default_model=pdef.default_model,
                default_temperature=0.1,
                default_max_tokens=100,
                require_api_key=pdef.require_api_key,
                timeout_s=10.0,
                max_retries=0,
                retry_backoff_s=0.0,
            )
        elif pdef.type == "anthropic":
            _provider = AnthropicProvider(
                api_key=pdef.api_key or "dummy",
                base_url=pdef.base_url,
                default_model=pdef.default_model,
                default_temperature=0.1,
                default_max_tokens=100,
                timeout_s=10.0,
            )
        elif pdef.type == "ollama":
            _provider = OllamaNativeProvider(
                name=pdef.name,
                api_key=pdef.api_key or "dummy",
                base_url=pdef.base_url,
                default_model=pdef.default_model,
                default_temperature=0.1,
                default_max_tokens=100,
                require_api_key=pdef.require_api_key,
                timeout_s=10.0,
                max_retries=0,
                retry_backoff_s=0.0,
            )
        else:
            raise ValueError(f"Unknown provider type '{pdef.type}'")

        # We don't actually make a network call here unless requested,
        # but we could call a simple chat to test. For now, just return success if instantiation works.
        return {"status": "ok", "message": "Provider instantiated successfully"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# -- Policies --


@router.get("/policies", response_model=List[PipelinePolicyModel])
def get_policies(_: AuthContext = Depends(require_admin_scope)):
    return admin_config_store.load_policies()


@router.put("/policies", response_model=List[PipelinePolicyModel])
def update_policies(policies: List[PipelinePolicyModel], _: AuthContext = Depends(require_admin_scope)):
    admin_config_store.save_policies(policies)
    reload_runtime()
    return policies


# -- API Keys --


class ApiKeyCreateRequest(BaseModel):
    subject: str
    scopes: List[str]
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
    entry: ApiKeyEntryModel


@router.get("/api-keys", response_model=List[ApiKeyEntryModel])
def get_api_keys(_: AuthContext = Depends(require_admin_scope)):
    return admin_config_store.load_api_keys()


@router.post("/api-keys", response_model=ApiKeyCreateResponse)
def create_api_key(req: ApiKeyCreateRequest, _: AuthContext = Depends(require_admin_scope)):
    keys = admin_config_store.load_api_keys()

    key_id = f"key_{uuid.uuid4().hex[:8]}"
    plaintext_key = f"sk_{uuid.uuid4().hex}"
    key_hash = hashlib.sha256(plaintext_key.encode("utf-8")).hexdigest()

    entry = ApiKeyEntryModel(
        key_id=key_id,
        key_hash=key_hash,
        subject=req.subject,
        scopes=req.scopes,
        default_pipeline_id=req.default_pipeline_id,
        allowed_providers=req.allowed_providers,
        allowed_models=req.allowed_models,
        max_input_tokens=req.max_input_tokens,
        max_output_tokens=req.max_output_tokens,
        max_total_tokens=req.max_total_tokens,
        max_top_k=req.max_top_k,
    )

    keys.append(entry)
    admin_config_store.save_api_keys(keys)
    reload_runtime()

    return ApiKeyCreateResponse(key_id=key_id, plaintext_key=plaintext_key, entry=entry)


@router.delete("/api-keys/{key_id}")
def delete_api_key(key_id: str, _: AuthContext = Depends(require_admin_scope)):
    keys = admin_config_store.load_api_keys()
    keys = [k for k in keys if k.key_id != key_id]
    admin_config_store.save_api_keys(keys)
    reload_runtime()
    return {"status": "ok"}


# -- Users --


@router.get("/users", response_model=List[UserModel])
def get_users(_: AuthContext = Depends(require_admin_scope)):
    return admin_config_store.load_users()


@router.put("/users", response_model=List[UserModel])
def update_users(users: List[UserModel], _: AuthContext = Depends(require_admin_scope)):
    admin_config_store.save_users(users)
    return users


# -- RAG Settings --


@router.get("/rag-settings", response_model=RagSettingsModel)
def get_rag_settings(_: AuthContext = Depends(require_admin_scope)):
    settings = admin_config_store.load_rag_settings()
    if not settings:
        settings = RagSettingsModel()
    return settings


@router.put("/rag-settings", response_model=RagSettingsModel)
def update_rag_settings(settings: RagSettingsModel, _: AuthContext = Depends(require_admin_scope)):
    admin_config_store.save_rag_settings(settings)
    reload_runtime()
    return settings


# -- Status --


@router.get("/status")
def get_status(_: AuthContext = Depends(require_admin_scope)):
    return {
        "ok": True,
        "providers_count": len(main_module.providers),
        "policies_count": len(main_module.pipeline_registry.policies),
        "mcp_enabled": main_module.mcp_registry is not None and main_module.mcp_registry.enabled,
        "retrieval_api_url": main_module.settings.retrieval_api_url,
    }
