from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Query, Response, UploadFile

from .db import ConfigRepository
from .models import (
    AdminStatus,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyRead,
    CorpusCatalog,
    CorpusDetail,
    CorpusEnsureRequest,
    CorpusReadiness,
    CorpusRegistryBundle,
    CorpusRegistryImportRequest,
    CorpusRegistryImportResult,
    CorpusSourceRecord,
    CorpusCreateRequest,
    CorpusUpdateRequest,
    CorpusSourceCreateRequest,
    IngestionJobCreateRequest,
    IngestionJobStatus,
    IngestionJobClaimRequest,
    IngestionJobUpdateRequest,
    LoginRequest,
    McpSettingsModel,
    PolicyRecord,
    ProcessorRecord,
    ProviderRecord,
    RagSettingsModel,
    RetrievalProfileRecord,
    UserRead,
    UserSession,
    UserWrite,
)


DB_PATH = os.environ.get("CONFIG_AUTH_DB_PATH", "/data/config-auth/config_auth.db")
RUNTIME_DIR = os.environ.get("CONFIG_AUTH_RUNTIME_DIR", "/data/config-auth/runtime")
SESSION_COOKIE_NAME = os.environ.get("CONFIG_AUTH_SESSION_COOKIE_NAME", "config_auth_session")
SESSION_COOKIE_SECURE = os.environ.get("CONFIG_AUTH_SESSION_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}
DEV_BOOTSTRAP_ADMIN = os.environ.get("CONFIG_AUTH_DEV_BOOTSTRAP_ADMIN", "").strip().lower() in {"1", "true", "yes"}
ORCHESTRATOR_RELOAD_URL = os.environ.get(
    "ORCHESTRATOR_RELOAD_URL", "http://orchestrator-api:8004/v1/internal/reload"
).strip()
ORCHESTRATOR_RELOAD_TOKEN = os.environ.get("ORCHESTRATOR_RELOAD_TOKEN", "").strip()
INGESTION_WORKER_URL = os.environ.get("INGESTION_WORKER_URL", "http://ingestion-worker:8002").strip().rstrip("/")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


INGESTION_WORKER_TIMEOUT_S = _float_env("INGESTION_WORKER_TIMEOUT_S", 30.0)
logger = logging.getLogger("config-auth.api")

repo = ConfigRepository(DB_PATH, RUNTIME_DIR)
router = APIRouter()
internal_router = APIRouter(prefix="/internal")

INTERNAL_API_TOKEN = os.environ.get("CONFIG_AUTH_INTERNAL_TOKEN", "").strip()


def _internal_auth_headers() -> dict[str, str]:
    if not INTERNAL_API_TOKEN:
        return {}
    return {"Authorization": f"Bearer {INTERNAL_API_TOKEN}"}


def _purge_source_artifacts(corpus_id: str, source_id: str) -> Dict[str, Any]:
    if not INGESTION_WORKER_URL:
        raise HTTPException(status_code=503, detail={"error": "ingestion worker URL is not configured"})
    try:
        with httpx.Client(timeout=INGESTION_WORKER_TIMEOUT_S) as client:
            response = client.post(
                f"{INGESTION_WORKER_URL}/v1/purge/source",
                json={"corpus_id": corpus_id, "source_id": source_id},
                headers=_internal_auth_headers(),
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(
            status_code=502,
            detail={"error": "ingestion worker source purge failed", "status_code": status_code, "detail": detail},
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "ingestion worker source purge failed", "detail": str(exc)},
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=502,
            detail={"error": "ingestion worker source purge returned an invalid response"},
        )
    return payload


def _parse_json_object_field(value: Optional[str], field_name: str) -> Dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail={"error": f"{field_name} must be valid JSON"}) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail={"error": f"{field_name} must be a JSON object"})
    return parsed


def require_internal_token(authorization: Optional[str] = Header(default=None)) -> str:
    if not INTERNAL_API_TOKEN:
        raise HTTPException(status_code=503, detail={"error": "internal authentication is not configured"})
    if not authorization:
        raise HTTPException(status_code=401, detail={"error": "missing authorization header"})
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail={"error": "invalid authorization header format"})
    if not secrets.compare_digest(parts[1], INTERNAL_API_TOKEN):
        raise HTTPException(status_code=403, detail={"error": "invalid internal token"})
    return parts[1]


app = FastAPI(
    title="Config Auth API",
    version="0.1.0",
    description="Configuration, authentication, and RBAC service for orchestrator admin.",
)


async def notify_orchestrator_reload(*, required: bool = False) -> None:
    if not ORCHESTRATOR_RELOAD_URL:
        return
    headers = {}
    if ORCHESTRATOR_RELOAD_TOKEN:
        headers["x-config-auth-token"] = ORCHESTRATOR_RELOAD_TOKEN
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(ORCHESTRATOR_RELOAD_URL, headers=headers)
        if response.status_code >= 400:
            message = f"orchestrator reload failed with HTTP {response.status_code}: {response.text[:300]}"
            logger.warning(message)
            if required:
                raise HTTPException(status_code=502, detail={"error": message})
    except HTTPException:
        raise
    except Exception as exc:
        message = f"orchestrator reload failed: {exc}"
        logger.warning(message)
        if required:
            raise HTTPException(status_code=502, detail={"error": message})


async def discover_corpora() -> List[str]:
    retrieval_api_url = repo.get_rag_settings().retrieval_api_url.rstrip("/")
    if not retrieval_api_url:
        return []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{retrieval_api_url}/corpora")
            response.raise_for_status()
        payload = response.json()
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    corpora = payload.get("corpora")
    if not isinstance(corpora, list):
        return []
    return [str(item).strip() for item in corpora if str(item).strip()]


async def init_config_auth_runtime() -> None:
    repo.ensure_schema()
    repo.bootstrap_admin_if_needed(DEV_BOOTSTRAP_ADMIN)
    repo.import_or_seed_runtime_defaults(corpora=await discover_corpora())
    repo.export_runtime_snapshots()
    await notify_orchestrator_reload()


@app.on_event("startup")
async def startup() -> None:
    await init_config_auth_runtime()


def current_session(
    config_auth_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: Optional[str] = Header(default=None),
) -> UserSession:
    if authorization:
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(status_code=401, detail={"error": "invalid authorization header format"})
        session = repo.authenticate_api_key(parts[1])
        if session is None:
            raise HTTPException(status_code=401, detail={"error": "unauthorized"})
        return session

    if config_auth_session:
        session = repo.get_session(config_auth_session)
        if session is not None:
            return session

    raise HTTPException(status_code=401, detail={"error": "unauthorized"})


def require_permission(permission: str):
    def dependency(session: UserSession = Depends(current_session)) -> UserSession:
        if permission not in session.permissions and "admin:*" not in session.permissions:
            raise HTTPException(status_code=403, detail={"error": f"forbidden: requires {permission}"})
        return session

    return dependency


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.post("/v1/auth/login", response_model=UserSession)
def login(body: LoginRequest, response: Response) -> UserSession:
    session = repo.authenticate_user(body.username, body.password)
    if session is None:
        raise HTTPException(status_code=401, detail={"error": "invalid_credentials"})
    token = repo.create_session(session.username)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
        max_age=8 * 60 * 60,
    )
    return session


@router.post("/v1/auth/logout")
def logout(
    response: Response,
    session: UserSession = Depends(current_session),
    config_auth_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, str]:
    if config_auth_session:
        repo.delete_session(config_auth_session)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"status": "ok"}


@router.get("/v1/auth/me", response_model=UserSession)
def me(session: UserSession = Depends(current_session)) -> UserSession:
    return session


@router.get("/v1/management/status", response_model=AdminStatus)
def status(_: UserSession = Depends(require_permission("status:read"))) -> AdminStatus:
    return repo.get_status()


@router.get("/v1/management/providers", response_model=List[ProviderRecord])
def get_providers(_: UserSession = Depends(require_permission("providers:read"))) -> List[ProviderRecord]:
    return repo.list_providers()


@router.put("/v1/management/providers", response_model=List[ProviderRecord])
async def put_providers(
    providers: List[ProviderRecord],
    session: UserSession = Depends(require_permission("providers:write")),
) -> List[ProviderRecord]:
    result = repo.replace_providers(providers, session.username)
    await notify_orchestrator_reload(required=True)
    return result


@router.get("/v1/management/policies", response_model=List[PolicyRecord])
def get_policies(_: UserSession = Depends(require_permission("policies:read"))) -> List[PolicyRecord]:
    return repo.list_policies()


@router.put("/v1/management/policies", response_model=List[PolicyRecord])
async def put_policies(
    policies: List[PolicyRecord],
    session: UserSession = Depends(require_permission("policies:write")),
) -> List[PolicyRecord]:
    result = repo.replace_policies(policies, session.username)
    await notify_orchestrator_reload(required=True)
    return result


@router.get("/v1/management/processors", response_model=List[ProcessorRecord])
def get_processors(_: UserSession = Depends(require_permission("processors:read"))) -> List[ProcessorRecord]:
    return repo.list_processors()


@router.put("/v1/management/processors", response_model=List[ProcessorRecord])
async def put_processors(
    processors: List[ProcessorRecord],
    session: UserSession = Depends(require_permission("processors:write")),
) -> List[ProcessorRecord]:
    result = repo.replace_processors(processors, session.username)
    await notify_orchestrator_reload(required=True)
    return result


@router.get("/v1/management/retrieval-profiles", response_model=List[RetrievalProfileRecord])
def get_retrieval_profiles(
    _: UserSession = Depends(require_permission("retrieval:read")),
) -> List[RetrievalProfileRecord]:
    return repo.list_retrieval_profiles()


@router.put("/v1/management/retrieval-profiles", response_model=List[RetrievalProfileRecord])
async def put_retrieval_profiles(
    retrieval_profiles: List[RetrievalProfileRecord],
    session: UserSession = Depends(require_permission("retrieval:write")),
) -> List[RetrievalProfileRecord]:
    result = repo.replace_retrieval_profiles(retrieval_profiles, session.username)
    await notify_orchestrator_reload(required=True)
    return result


@router.get("/v1/management/api-keys", response_model=List[ApiKeyRead])
def get_api_keys(_: UserSession = Depends(require_permission("keys:read"))) -> List[ApiKeyRead]:
    return repo.list_api_keys()


@router.post("/v1/management/api-keys", response_model=ApiKeyCreateResponse)
async def create_api_key(
    body: ApiKeyCreateRequest,
    session: UserSession = Depends(require_permission("keys:write")),
) -> ApiKeyCreateResponse:
    plaintext, entry = repo.create_api_key(body, session.username)
    await notify_orchestrator_reload()
    return ApiKeyCreateResponse(key_id=entry.key_id, plaintext_key=plaintext, entry=entry)


@router.delete("/v1/management/api-keys/{key_id}")
async def delete_api_key(
    key_id: str,
    session: UserSession = Depends(require_permission("keys:write")),
) -> dict[str, str]:
    repo.revoke_api_key(key_id, session.username)
    await notify_orchestrator_reload()
    return {"status": "ok"}


@router.get("/v1/management/users", response_model=List[UserRead])
def get_users(_: UserSession = Depends(require_permission("users:read"))) -> List[UserRead]:
    return repo.list_users()


@router.put("/v1/management/users", response_model=List[UserRead])
def put_users(
    users: List[UserWrite],
    session: UserSession = Depends(require_permission("users:write")),
) -> List[UserRead]:
    return repo.replace_users(users, session.username)


@router.get("/v1/management/rag-settings", response_model=RagSettingsModel)
def get_rag_settings(_: UserSession = Depends(require_permission("rag:read"))) -> RagSettingsModel:
    return repo.get_rag_settings()


@router.put("/v1/management/rag-settings", response_model=RagSettingsModel)
async def put_rag_settings(
    settings: RagSettingsModel,
    session: UserSession = Depends(require_permission("rag:write")),
) -> RagSettingsModel:
    result = repo.update_rag_settings(settings, session.username)
    await notify_orchestrator_reload()
    return result


@router.get("/v1/management/corpora", response_model=CorpusCatalog)
async def get_corpora(_: UserSession = Depends(require_permission("corpora:read"))) -> CorpusCatalog:
    corpora = repo.list_corpora()
    if corpora:
        return CorpusCatalog(corpora=corpora)
    settings = repo.get_rag_settings()
    return CorpusCatalog(corpora=settings.selected_corpus_ids or [settings.default_corpus_id])


@router.post("/v1/management/corpora", response_model=CorpusDetail)
def create_corpus(
    body: CorpusCreateRequest,
    session: UserSession = Depends(require_permission("corpora:write")),
) -> CorpusDetail:
    try:
        return repo.create_corpus(body, session.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})


@router.put("/v1/management/corpora/{corpus_id}/ensure", response_model=CorpusDetail)
def ensure_corpus(
    corpus_id: str,
    body: CorpusEnsureRequest,
    session: UserSession = Depends(require_permission("corpora:write")),
) -> CorpusDetail:
    try:
        return repo.ensure_corpus(corpus_id, body, session.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})


@router.post("/v1/management/corpora/registry-import", response_model=CorpusRegistryImportResult)
def import_corpus_registry(
    body: CorpusRegistryImportRequest,
    session: UserSession = Depends(require_permission("corpora:write")),
) -> CorpusRegistryImportResult:
    try:
        return repo.import_corpus_registry_bundle(
            body.bundle,
            actor=session.username,
            conflict_strategy=body.conflict_strategy,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})


@router.put("/v1/management/corpora/{corpus_id}", response_model=CorpusDetail)
def update_corpus(
    corpus_id: str,
    body: CorpusUpdateRequest,
    session: UserSession = Depends(require_permission("corpora:write")),
) -> CorpusDetail:
    try:
        return repo.update_corpus(corpus_id, body, session.username)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})


@router.delete("/v1/management/corpora/{corpus_id}")
def delete_corpus(
    corpus_id: str,
    session: UserSession = Depends(require_permission("corpora:write")),
) -> dict[str, str]:
    try:
        repo.delete_corpus(corpus_id, session.username)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})
    return {"status": "ok"}


@router.get("/v1/management/corpora/{corpus_id}", response_model=CorpusDetail)
def get_corpus_detail(corpus_id: str, _: UserSession = Depends(require_permission("corpora:read"))) -> CorpusDetail:
    try:
        return repo.get_corpus_detail(corpus_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc


@router.get("/v1/management/corpora/{corpus_id}/readiness", response_model=CorpusReadiness)
def get_corpus_readiness(
    corpus_id: str,
    _: UserSession = Depends(require_permission("corpora:read")),
) -> CorpusReadiness:
    try:
        return repo.get_corpus_readiness(corpus_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc


@router.get("/v1/management/corpora/{corpus_id}/registry-export", response_model=CorpusRegistryBundle)
def export_corpus_registry(
    corpus_id: str,
    response: Response,
    _: UserSession = Depends(require_permission("corpora:read")),
) -> CorpusRegistryBundle:
    try:
        bundle = repo.export_corpus_registry_bundle(corpus_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
    response.headers["Content-Disposition"] = f'attachment; filename="{bundle.corpus.corpus_id}-registry.json"'
    return bundle


@router.get("/v1/management/corpora/{corpus_id}/sources", response_model=List[CorpusSourceRecord])
def list_corpus_sources(
    corpus_id: str,
    session: UserSession = Depends(require_permission("corpora:read")),
) -> List[CorpusSourceRecord]:
    try:
        return repo.list_corpus_sources(corpus_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})


@router.post("/v1/management/corpora/{corpus_id}/sources", response_model=CorpusSourceRecord)
def create_corpus_source(
    corpus_id: str,
    body: CorpusSourceCreateRequest,
    session: UserSession = Depends(require_permission("corpora:write")),
) -> CorpusSourceRecord:
    try:
        return repo.create_corpus_source(corpus_id, body, session.username)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})


@router.get("/v1/management/corpora/{corpus_id}/sources/{source_id}", response_model=CorpusSourceRecord)
def get_corpus_source(
    corpus_id: str,
    source_id: str,
    session: UserSession = Depends(require_permission("corpora:read")),
) -> CorpusSourceRecord:
    try:
        return repo.get_corpus_source(corpus_id, source_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})


@router.put("/v1/management/corpora/{corpus_id}/sources/{source_id}", response_model=CorpusSourceRecord)
def update_corpus_source(
    corpus_id: str,
    source_id: str,
    body: CorpusSourceCreateRequest,
    session: UserSession = Depends(require_permission("corpora:write")),
) -> CorpusSourceRecord:
    try:
        return repo.update_corpus_source(corpus_id, source_id, body, session.username)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})


@router.delete("/v1/management/corpora/{corpus_id}/sources/{source_id}")
def delete_corpus_source(
    corpus_id: str,
    source_id: str,
    purge: bool = Query(default=False),
    session: UserSession = Depends(require_permission("corpora:write")),
) -> Dict[str, Any]:
    try:
        purge_result: Dict[str, Any] | None = None
        if purge:
            repo.get_corpus_source(corpus_id, source_id)
            purge_result = _purge_source_artifacts(corpus_id, source_id)
        deleted = repo.delete_corpus_source(corpus_id, source_id, session.username)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})
    if not deleted:
        raise HTTPException(status_code=404, detail={"error": "resource not found"})
    response: Dict[str, Any] = {"status": "ok"}
    if purge_result is not None:
        response["purge"] = purge_result
    return response


@router.post("/v1/management/corpora/{corpus_id}/sources/upload", response_model=CorpusSourceRecord)
async def upload_corpus_source(
    corpus_id: str,
    source_id: str = Form(...),
    format: str = Form(...),
    upload: UploadFile = File(...),
    title: Optional[str] = Form(default=None),
    language: Optional[str] = Form(default=None),
    doc_type: Optional[str] = Form(default=None),
    tags_json: Optional[str] = Form(default=None),
    configuration_json: Optional[str] = Form(default=None),
    processor_id: Optional[str] = Form(default=None),
    processor_config_json: Optional[str] = Form(default=None),
    metadata_json: Optional[str] = Form(default=None),
    session: UserSession = Depends(require_permission("corpora:write")),
) -> CorpusSourceRecord:
    try:
        tags = []
        if tags_json:
            try:
                parsed_json = json.loads(tags_json)
                if isinstance(parsed_json, list):
                    tags = [str(item).strip() for item in parsed_json if str(item).strip()]
                else:
                    tags = [part.strip() for part in str(tags_json).split(",") if part.strip()]
            except Exception:
                tags = [part.strip() for part in str(tags_json).split(",") if part.strip()]
        configuration = _parse_json_object_field(configuration_json, "configuration_json")
        processor_config = _parse_json_object_field(processor_config_json, "processor_config_json")
        metadata = _parse_json_object_field(metadata_json, "metadata_json")
        validated_source = CorpusSourceCreateRequest(
            source_id=source_id,
            type="object",
            format=format or "text",
            object_uri="s3://validation-placeholder",
            title=title,
            language=language,
            doc_type=doc_type,
            tags=tags,
            configuration=configuration,
            processor_id=processor_id,
            processor_config=processor_config,
            metadata=metadata,
        )
        content = await upload.read()
        return repo.add_uploaded_corpus_source(
            corpus_id,
            source_id=validated_source.source_id,
            title=title,
            filename=upload.filename or "",
            content=content,
            format=format,
            language=language,
            doc_type=doc_type,
            tags=tags,
            configuration=configuration,
            processor_id=validated_source.processor_id,
            processor_config=processor_config,
            metadata=metadata,
            actor=session.username,
            content_type=upload.content_type,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})


@router.post("/v1/management/corpora/{corpus_id}/ingestion-jobs", response_model=IngestionJobStatus)
def create_ingestion_job(
    corpus_id: str,
    body: IngestionJobCreateRequest,
    session: UserSession = Depends(require_permission("corpora:write")),
) -> IngestionJobStatus:
    try:
        return repo.create_ingestion_job(corpus_id, body, session.username)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})


@router.get("/v1/management/ingestion-jobs", response_model=List[IngestionJobStatus])
def list_ingestion_jobs(
    session: UserSession = Depends(require_permission("corpora:read")),
) -> List[IngestionJobStatus]:
    return repo.list_ingestion_jobs()


@router.get("/v1/management/ingestion-jobs/{job_id}", response_model=IngestionJobStatus)
def get_ingestion_job(
    job_id: str,
    session: UserSession = Depends(require_permission("corpora:read")),
) -> IngestionJobStatus:
    try:
        return repo.get_ingestion_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})


@router.post("/v1/management/ingestion-jobs/{job_id}/cancel", response_model=IngestionJobStatus)
def cancel_ingestion_job(
    job_id: str,
    session: UserSession = Depends(require_permission("corpora:write")),
) -> IngestionJobStatus:
    try:
        return repo.cancel_ingestion_job(job_id, session.username)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})


@router.get("/v1/management/mcp-settings", response_model=McpSettingsModel)
def get_mcp_settings(_: UserSession = Depends(require_permission("mcp:read"))) -> McpSettingsModel:
    return repo.get_mcp_settings()


@router.put("/v1/management/mcp-settings", response_model=McpSettingsModel)
async def put_mcp_settings(
    settings: McpSettingsModel,
    session: UserSession = Depends(require_permission("mcp:write")),
) -> McpSettingsModel:
    result = repo.update_mcp_settings(settings, session.username)
    await notify_orchestrator_reload()
    return result


@internal_router.get("/health")
def internal_health(_: str = Depends(require_internal_token)) -> dict[str, bool]:
    return {"ok": True}


@internal_router.post("/reload")
async def internal_reload(_: str = Depends(require_internal_token)) -> dict[str, str]:
    await init_config_auth_runtime()
    return {"status": "ok"}


@internal_router.get("/corpora", response_model=CorpusCatalog)
def internal_get_corpora(_: str = Depends(require_internal_token)) -> CorpusCatalog:
    return CorpusCatalog(corpora=repo.list_corpora())


@internal_router.get("/corpora/{corpus_id}", response_model=CorpusDetail)
def internal_get_corpus_detail(corpus_id: str, _: str = Depends(require_internal_token)) -> CorpusDetail:
    try:
        return repo.get_corpus_detail(corpus_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})


@internal_router.get("/corpora/{corpus_id}/sources", response_model=List[CorpusSourceRecord])
def internal_list_corpus_sources(corpus_id: str, _: str = Depends(require_internal_token)) -> List[CorpusSourceRecord]:
    try:
        return repo.list_corpus_sources(corpus_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})


@internal_router.get("/processors", response_model=List[ProcessorRecord])
def internal_get_processors(_: str = Depends(require_internal_token)) -> List[ProcessorRecord]:
    return repo.list_processors()


@internal_router.get("/retrieval-profiles", response_model=List[RetrievalProfileRecord])
def internal_get_retrieval_profiles(_: str = Depends(require_internal_token)) -> List[RetrievalProfileRecord]:
    return repo.list_retrieval_profiles()


@internal_router.get("/ingestion-jobs", response_model=List[IngestionJobStatus])
def internal_list_ingestion_jobs(
    status: Optional[str] = None,
    _: str = Depends(require_internal_token),
) -> List[IngestionJobStatus]:
    jobs = repo.list_ingestion_jobs()
    if status:
        jobs = [job for job in jobs if job.status == status]
    return jobs


@internal_router.get("/ingestion-jobs/{job_id}", response_model=IngestionJobStatus)
def internal_get_ingestion_job(
    job_id: str,
    _: str = Depends(require_internal_token),
) -> IngestionJobStatus:
    try:
        return repo.get_ingestion_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})


@internal_router.post("/ingestion-jobs/{job_id}/claim", response_model=IngestionJobStatus)
def internal_claim_ingestion_job(
    job_id: str,
    body: IngestionJobClaimRequest,
    _: str = Depends(require_internal_token),
) -> IngestionJobStatus:
    try:
        return repo.claim_ingestion_job(job_id, body.worker_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})


@internal_router.patch("/ingestion-jobs/{job_id}", response_model=IngestionJobStatus)
def internal_update_ingestion_job(
    job_id: str,
    body: IngestionJobUpdateRequest,
    worker_id: str = Header(alias="x-worker-id"),
    _: str = Depends(require_internal_token),
) -> IngestionJobStatus:
    try:
        return repo.update_ingestion_job(job_id, body.status, body.stats, body.error, worker_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})


@internal_router.post("/ingestion-jobs/{job_id}/heartbeat")
def internal_heartbeat_ingestion_job(
    job_id: str,
    worker_id: str = Header(alias="x-worker-id"),
    _: str = Depends(require_internal_token),
) -> dict[str, str]:
    try:
        repo.heartbeat_ingestion_job(job_id, worker_id)
        return {"status": "ok"}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})


app.include_router(router)
app.include_router(internal_router)
