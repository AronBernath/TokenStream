from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from common.object_storage import S3ObjectStorage
from common.registry_validation import normalize_corpus_id, normalize_source_id
from .models import (
    AdminStatus,
    ApiKeyCreateRequest,
    ApiKeyRead,
    CorpusCreateRequest,
    CorpusDetail,
    CorpusEnsureRequest,
    CorpusReadiness,
    CorpusRegistryBundle,
    CorpusRegistryImportResult,
    CorpusSourceRecord,
    CorpusSourceCreateRequest,
    CorpusUpdateRequest,
    IngestionJobCreateRequest,
    IngestionJobStatus,
    McpServerRecord,
    McpSettingsModel,
    PolicyRecord,
    ProcessorRecord,
    ProviderRecord,
    ROLE_PERMISSIONS,
    RagSettingsModel,
    RetrievalProfileRecord,
    UserRead,
    UserSession,
    UserWrite,
)
from .security import (
    hash_api_key,
    hash_password,
    hash_session_token,
    new_api_key,
    new_session_token,
    needs_password_rehash,
    verify_api_key,
    verify_password,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat()


class ConfigRepository:
    def __init__(self, db_path: str, runtime_dir: str):
        self.db_path = db_path
        self.runtime_dir = runtime_dir
        self.bootstrap_providers_path = (os.environ.get("CONFIG_AUTH_BOOTSTRAP_PROVIDERS_PATH", "") or "").strip()
        self.bootstrap_policies_path = (os.environ.get("CONFIG_AUTH_BOOTSTRAP_POLICIES_PATH", "") or "").strip()
        self.bootstrap_processors_path = (os.environ.get("CONFIG_AUTH_BOOTSTRAP_PROCESSORS_PATH", "") or "").strip()
        self.bootstrap_retrieval_profiles_path = (
            os.environ.get("CONFIG_AUTH_BOOTSTRAP_RETRIEVAL_PROFILES_PATH", "") or ""
        ).strip()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs(runtime_dir, exist_ok=True)
        self.max_upload_bytes = int(os.environ.get("CONFIG_AUTH_MAX_UPLOAD_BYTES", str(32 * 1024 * 1024)))
        self.object_storage = self._build_object_storage()
        self.default_storage_environment = os.environ.get("DEFAULT_ENVIRONMENT", "default-env").strip() or "default-env"
        self.default_storage_tenant_id = (
            os.environ.get("DEFAULT_TENANT_ID", "default-tenant").strip() or "default-tenant"
        )

    def _build_object_storage(self) -> Optional[S3ObjectStorage]:
        required = [
            os.environ.get("RAG_OBJECT_STORAGE_ENDPOINT", os.environ.get("MINIO_ENDPOINT", "")).strip(),
            os.environ.get("RAG_OBJECT_STORAGE_ACCESS_KEY", os.environ.get("MINIO_ROOT_USER", "")).strip(),
            os.environ.get("RAG_OBJECT_STORAGE_SECRET_KEY", os.environ.get("MINIO_ROOT_PASSWORD", "")).strip(),
        ]
        if not all(required):
            return None
        return S3ObjectStorage.from_env()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS roles (
                    name TEXT PRIMARY KEY,
                    description TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS permissions (
                    name TEXT PRIMARY KEY
                );

                CREATE TABLE IF NOT EXISTS role_permissions (
                    role_name TEXT NOT NULL,
                    permission_name TEXT NOT NULL,
                    PRIMARY KEY (role_name, permission_name),
                    FOREIGN KEY (role_name) REFERENCES roles(name) ON DELETE CASCADE,
                    FOREIGN KEY (permission_name) REFERENCES permissions(name) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    must_rotate_password INTEGER NOT NULL DEFAULT 0,
                    is_bootstrap INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_roles (
                    user_id INTEGER NOT NULL,
                    role_name TEXT NOT NULL,
                    PRIMARY KEY (user_id, role_name),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (role_name) REFERENCES roles(name) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_id TEXT NOT NULL UNIQUE,
                    algorithm TEXT NOT NULL,
                    salt_b64 TEXT NOT NULL,
                    hash_b64 TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    default_pipeline_id TEXT,
                    allowed_providers_json TEXT,
                    allowed_models_json TEXT,
                    max_input_tokens INTEGER,
                    max_output_tokens INTEGER,
                    max_total_tokens INTEGER,
                    max_top_k INTEGER,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT
                );

                CREATE TABLE IF NOT EXISTS providers (
                    name TEXT PRIMARY KEY,
                    provider_type TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    require_api_key INTEGER NOT NULL DEFAULT 1,
                    default_model TEXT NOT NULL,
                    models_json TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    client_controls_json TEXT NOT NULL DEFAULT '{}',
                    secret_ref TEXT,
                    secret_source_type TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS policies (
                    pipeline_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS processors (
                    processor_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS retrieval_profiles (
                    retrieval_profile_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS rag_settings (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    default_corpus_id TEXT NOT NULL,
                    selected_corpus_ids_json TEXT,
                    default_top_k INTEGER NOT NULL,
                    retrieval_api_url TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mcp_settings (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    selected_servers_json TEXT NOT NULL,
                    servers_json TEXT NOT NULL,
                    timeout_s REAL NOT NULL,
                    strict INTEGER NOT NULL DEFAULT 0,
                    max_tool_rounds INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS corpora (
                    corpus_id TEXT PRIMARY KEY,
                    title TEXT,
                    description TEXT,
                    environment TEXT,
                    tenant_id TEXT,
                    chunking_json TEXT NOT NULL,
                    index_json TEXT NOT NULL DEFAULT '{}',
                    processor_id TEXT,
                    processor_config_json TEXT NOT NULL DEFAULT '{}',
                    retrieval_profile_id TEXT,
                    retrieval_config_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                );

                CREATE TABLE IF NOT EXISTS corpus_sources (
                    source_id TEXT NOT NULL,
                    corpus_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    format TEXT NOT NULL,
                    title TEXT,
                    path TEXT,
                    local_path TEXT,
                    url TEXT,
                    object_uri TEXT,
                    content_hash TEXT,
                    size_bytes INTEGER,
                    content_type TEXT,
                    language TEXT,
                    doc_type TEXT,
                    tags_json TEXT NOT NULL,
                    configuration_json TEXT NOT NULL DEFAULT '{}',
                    processor_id TEXT,
                    processor_config_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT,
                    PRIMARY KEY (corpus_id, source_id),
                    FOREIGN KEY (corpus_id) REFERENCES corpora(corpus_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS ingestion_jobs (
                    job_id TEXT PRIMARY KEY,
                    corpus_id TEXT NOT NULL,
                    environment TEXT,
                    tenant_id TEXT,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL DEFAULT '{}',
                    plan_json TEXT NOT NULL,
                    stats_json TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (corpus_id) REFERENCES corpora(corpus_id) ON DELETE CASCADE
                );
                """
            )
            self._ensure_column(conn, "rag_settings", "selected_corpus_ids_json", "TEXT")
            self._ensure_column(conn, "providers", "client_controls_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "corpora", "index_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "corpora", "processor_id", "TEXT")
            self._ensure_column(conn, "corpora", "processor_config_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "corpora", "retrieval_profile_id", "TEXT")
            self._ensure_column(conn, "corpora", "retrieval_config_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "corpora", "deleted_at", "TEXT")
            self._ensure_column(conn, "corpus_sources", "deleted_at", "TEXT")
            self._ensure_column(conn, "corpus_sources", "configuration_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "corpus_sources", "processor_id", "TEXT")
            self._ensure_column(conn, "corpus_sources", "processor_config_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "corpus_sources", "content_type", "TEXT")
            self._ensure_column(conn, "ingestion_jobs", "environment", "TEXT")
            self._ensure_column(conn, "ingestion_jobs", "tenant_id", "TEXT")
            self._ensure_column(conn, "ingestion_jobs", "request_json", "TEXT NOT NULL DEFAULT '{}'")
        self.seed_rbac()
        self.ensure_rag_defaults()
        self.ensure_mcp_defaults()

    def _ensure_column(self, conn: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
        if column_name in columns:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")

    def seed_rbac(self) -> None:
        with self.connect() as conn:
            for role_name, perms in ROLE_PERMISSIONS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO roles(name, description) VALUES (?, ?)",
                    (role_name, f"{role_name.title()} role"),
                )
                for permission in perms:
                    conn.execute("INSERT OR IGNORE INTO permissions(name) VALUES (?)", (permission,))
                    conn.execute(
                        "INSERT OR IGNORE INTO role_permissions(role_name, permission_name) VALUES (?, ?)",
                        (role_name, permission),
                    )

    def ensure_rag_defaults(self) -> None:
        default_corpus_id = (os.environ.get("DEFAULT_CORPUS_ID", "default") or "default").strip() or "default"
        default_top_k = max(int(os.environ.get("DEFAULT_TOP_K", "8") or "8"), 1)
        retrieval_api_url = (
            os.environ.get("RETRIEVAL_API_URL", "http://retrieval-api:8000") or "http://retrieval-api:8000"
        ).rstrip("/")
        selected_corpus_ids = json.dumps([default_corpus_id])
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO rag_settings(singleton_id, default_corpus_id, selected_corpus_ids_json, default_top_k, retrieval_api_url, updated_at)
                VALUES (1, ?, ?, ?, ?, ?)
                """,
                (default_corpus_id, selected_corpus_ids, default_top_k, retrieval_api_url, iso_now()),
            )
            conn.execute(
                """
                UPDATE rag_settings
                SET selected_corpus_ids_json = COALESCE(NULLIF(selected_corpus_ids_json, ''), ?)
                WHERE singleton_id = 1
                """,
                (selected_corpus_ids,),
            )

    def ensure_mcp_defaults(self) -> None:
        settings = self._default_mcp_settings_from_env()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO mcp_settings(
                    singleton_id, selected_servers_json, servers_json, timeout_s, strict, max_tool_rounds, updated_at
                )
                VALUES (1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    json.dumps(settings.selected_servers),
                    json.dumps([server.model_dump(exclude_none=True) for server in settings.servers]),
                    settings.timeout_s,
                    1 if settings.strict else 0,
                    settings.max_tool_rounds,
                    iso_now(),
                ),
            )

    def bootstrap_admin_if_needed(self, enabled: bool) -> None:
        if not enabled:
            return
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
            if row["count"] > 0:
                return
            now = iso_now()
            password_hash = hash_password("admin")
            cursor = conn.execute(
                """
                INSERT INTO users(username, password_hash, is_active, must_rotate_password, is_bootstrap, created_at, updated_at)
                VALUES (?, ?, 1, 1, 1, ?, ?)
                """,
                ("admin", password_hash, now, now),
            )
            user_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO user_roles(user_id, role_name) VALUES (?, ?)",
                (user_id, "admin"),
            )
            self._insert_audit(conn, "system", "bootstrap_admin_created", {"username": "admin"})

    def import_or_seed_runtime_defaults(self, *, corpora: Optional[Sequence[str]] = None) -> None:
        self._sync_bootstrap_providers_if_configured()
        self._sync_bootstrap_policies_if_configured()
        self._sync_bootstrap_processors_if_configured()
        self._sync_bootstrap_retrieval_profiles_if_configured()
        self._import_runtime_snapshots_if_needed()
        self._seed_providers_if_needed()
        self._seed_mcp_if_needed()
        self._ensure_rag_corpora_if_needed(corpora or [])
        self._seed_placeholder_policy_if_needed(corpora or [])

    def _load_provider_records_from_path(self, path: Path) -> List[ProviderRecord]:
        payload = json.loads(path.read_text(encoding="utf-8") or "[]")
        if not isinstance(payload, list):
            return []
        return [ProviderRecord.model_validate(item) for item in payload if isinstance(item, dict)]

    def _load_policy_records_from_path(self, path: Path) -> List[PolicyRecord]:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        if not isinstance(payload, dict):
            return []
        return [
            PolicyRecord.model_validate({"pipeline_id": key, **value})
            for key, value in payload.items()
            if isinstance(key, str) and isinstance(value, dict)
        ]

    def _load_processor_records_from_path(self, path: Path) -> List[ProcessorRecord]:
        payload = json.loads(path.read_text(encoding="utf-8") or "[]")
        if isinstance(payload, dict):
            items = [
                {"processor_id": key, **value}
                for key, value in payload.items()
                if isinstance(key, str) and isinstance(value, dict)
            ]
        elif isinstance(payload, list):
            items = [item for item in payload if isinstance(item, dict)]
        else:
            return []
        return [ProcessorRecord.model_validate(item) for item in items]

    def _load_retrieval_profile_records_from_path(self, path: Path) -> List[RetrievalProfileRecord]:
        payload = json.loads(path.read_text(encoding="utf-8") or "[]")
        if isinstance(payload, dict):
            items = [
                {"retrieval_profile_id": key, **value}
                for key, value in payload.items()
                if isinstance(key, str) and isinstance(value, dict)
            ]
        elif isinstance(payload, list):
            items = [item for item in payload if isinstance(item, dict)]
        else:
            return []
        return [RetrievalProfileRecord.model_validate(item) for item in items]

    def _provider_payload(self, providers: Sequence[ProviderRecord]) -> List[Dict[str, Any]]:
        return [provider.model_dump(exclude={"has_secret_ref"}) for provider in providers]

    def _policy_payload(self, policies: Sequence[PolicyRecord]) -> Dict[str, Dict[str, Any]]:
        return {
            policy.pipeline_id: {k: v for k, v in policy.model_dump().items() if k != "pipeline_id"}
            for policy in policies
        }

    def _processor_payload(self, processors: Sequence[ProcessorRecord]) -> Dict[str, Dict[str, Any]]:
        return {
            processor.processor_id: {k: v for k, v in processor.model_dump().items() if k != "processor_id"}
            for processor in processors
        }

    def _retrieval_profile_payload(
        self, retrieval_profiles: Sequence[RetrievalProfileRecord]
    ) -> Dict[str, Dict[str, Any]]:
        return {
            profile.retrieval_profile_id: {k: v for k, v in profile.model_dump().items() if k != "retrieval_profile_id"}
            for profile in retrieval_profiles
        }

    def _sync_bootstrap_providers_if_configured(self) -> None:
        if not self.bootstrap_providers_path:
            return
        path = Path(self.bootstrap_providers_path)
        if not path.exists():
            return
        records = self._load_provider_records_from_path(path)
        if not records:
            return
        current = self.list_providers()
        if self._provider_payload(current) != self._provider_payload(records):
            self.replace_providers(records, "bootstrap-sync")

    def _sync_bootstrap_policies_if_configured(self) -> None:
        if not self.bootstrap_policies_path:
            return
        path = Path(self.bootstrap_policies_path)
        if not path.exists():
            return
        records = self._load_policy_records_from_path(path)
        if not records:
            return
        current = self.list_policies()
        if self._policy_payload(current) != self._policy_payload(records):
            self.replace_policies(records, "bootstrap-sync")

    def _sync_bootstrap_processors_if_configured(self) -> None:
        if not self.bootstrap_processors_path:
            return
        path = Path(self.bootstrap_processors_path)
        if not path.exists():
            return
        records = self._load_processor_records_from_path(path)
        if not records:
            return
        current = self.list_processors()
        if self._processor_payload(current) != self._processor_payload(records):
            self.replace_processors(records, "bootstrap-sync")

    def _sync_bootstrap_retrieval_profiles_if_configured(self) -> None:
        if not self.bootstrap_retrieval_profiles_path:
            return
        path = Path(self.bootstrap_retrieval_profiles_path)
        if not path.exists():
            return
        records = self._load_retrieval_profile_records_from_path(path)
        if not records:
            return
        current = self.list_retrieval_profiles()
        if self._retrieval_profile_payload(current) != self._retrieval_profile_payload(records):
            self.replace_retrieval_profiles(records, "bootstrap-sync")

    def _import_runtime_snapshots_if_needed(self) -> None:
        providers = self.list_providers()
        if not providers:
            providers_path = Path(self.runtime_dir) / "providers.json"
            if providers_path.exists():
                records = self._load_provider_records_from_path(providers_path)
                if records:
                    self.replace_providers(records, "system-import")

        policies = self.list_policies()
        if not policies:
            policies_path = Path(self.runtime_dir) / "policies.json"
            if policies_path.exists():
                records = self._load_policy_records_from_path(policies_path)
                if records:
                    self.replace_policies(records, "system-import")

        processors = self.list_processors()
        if not processors:
            processors_path = Path(self.runtime_dir) / "processors.json"
            if processors_path.exists():
                records = self._load_processor_records_from_path(processors_path)
                if records:
                    self.replace_processors(records, "system-import")

        retrieval_profiles = self.list_retrieval_profiles()
        if not retrieval_profiles:
            retrieval_profiles_path = Path(self.runtime_dir) / "retrieval_profiles.json"
            if retrieval_profiles_path.exists():
                records = self._load_retrieval_profile_records_from_path(retrieval_profiles_path)
                if records:
                    self.replace_retrieval_profiles(records, "system-import")

        rag_settings_path = Path(self.runtime_dir) / "rag_settings.json"
        if rag_settings_path.exists():
            payload = json.loads(rag_settings_path.read_text(encoding="utf-8") or "{}")
            if isinstance(payload, dict):
                current = self.get_rag_settings()
                if current.default_corpus_id == "default" and current.retrieval_api_url == "http://retrieval-api:8000":
                    merged = RagSettingsModel.model_validate(
                        {
                            "default_corpus_id": payload.get("default_corpus_id", current.default_corpus_id),
                            "selected_corpus_ids": payload.get("selected_corpus_ids", current.selected_corpus_ids),
                            "default_top_k": payload.get("default_top_k", current.default_top_k),
                            "retrieval_api_url": payload.get("retrieval_api_url", current.retrieval_api_url),
                        }
                    )
                    self.update_rag_settings(merged, "system-import")

    def _seed_providers_if_needed(self) -> None:
        if self.list_providers():
            return
        defaults = self._default_provider_records_from_env()
        if defaults:
            self.replace_providers(defaults, "system-seed")

    def _seed_mcp_if_needed(self) -> None:
        settings = self.get_mcp_settings()
        if settings.servers:
            return
        self.update_mcp_settings(self._default_mcp_settings_from_env(), "system-seed")

    def _ensure_rag_corpora_if_needed(self, corpora: Sequence[str]) -> None:
        discovered = [item for item in dict.fromkeys(str(item).strip() for item in corpora) if item]
        if not discovered:
            return
        current = self.get_rag_settings()
        if current.selected_corpus_ids:
            return
        merged = RagSettingsModel(
            default_corpus_id=current.default_corpus_id if current.default_corpus_id in discovered else discovered[0],
            selected_corpus_ids=discovered,
            default_top_k=current.default_top_k,
            retrieval_api_url=current.retrieval_api_url,
        )
        self.update_rag_settings(merged, "system-seed")

    def _seed_placeholder_policy_if_needed(self, corpora: Sequence[str]) -> None:
        if self.list_policies():
            return
        rag = self.get_rag_settings()
        providers = [provider.name for provider in self.list_providers()]
        mcp_settings = self.get_mcp_settings()
        discovered = [item for item in dict.fromkeys(str(item).strip() for item in corpora) if item]
        selected_corpora = rag.selected_corpus_ids or discovered or [rag.default_corpus_id]
        default_corpus = rag.default_corpus_id if rag.default_corpus_id in selected_corpora else selected_corpora[0]
        allowed_tools = ["rag"]
        for server in mcp_settings.servers:
            if server.name not in mcp_settings.selected_servers:
                continue
            namespace = (server.namespace or server.name).strip()
            if namespace:
                allowed_tools.append(f"mcp__{namespace}")
        placeholder = PolicyRecord(
            pipeline_id="default",
            default_corpus_id=default_corpus,
            allowed_corpus_ids=selected_corpora,
            default_filters={},
            allowed_tools=allowed_tools,
            allowed_providers=providers or None,
            allowed_models=None,
            max_input_tokens=None,
            max_output_tokens=None,
            max_total_tokens=None,
            max_top_k=None,
            default_provider=None,
            default_model=None,
        )
        self.replace_policies([placeholder], "system-seed")

    def _default_provider_records_from_env(self) -> List[ProviderRecord]:
        def models_from_env(default_model: str, raw_value: str) -> List[str]:
            models = [item.strip() for item in raw_value.split(",") if item.strip()]
            if default_model and default_model not in models:
                models.append(default_model)
            return models

        return [
            ProviderRecord(
                name="openai",
                type="openai_compat",
                base_url=(os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1") or "").rstrip("/"),
                require_api_key=True,
                default_model=(os.environ.get("OPENAI_MODEL", "gpt-5.1") or "").strip(),
                models=models_from_env(
                    (os.environ.get("OPENAI_MODEL", "gpt-5.1") or "").strip(),
                    os.environ.get("OPENAI_MODELS", "") or "",
                ),
                capabilities={
                    "tools": True,
                    "json_schema": True,
                    "streaming": True,
                    "max_context_window": 128000,
                    "default_context_window": 128000,
                },
                client_controls={"temperature": True, "max_tokens": True},
                secret_ref="env://OPENAI_API_KEY",
                secret_source_type="env",
                has_secret_ref=True,
            ),
            ProviderRecord(
                name="deepseek",
                type="openai_compat",
                base_url=(os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com") or "").rstrip("/"),
                require_api_key=True,
                default_model=(os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro") or "").strip(),
                models=models_from_env(
                    (os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro") or "").strip(),
                    os.environ.get("DEEPSEEK_MODELS", "") or "",
                ),
                capabilities={
                    "tools": True,
                    "json_schema": True,
                    "streaming": True,
                    "max_context_window": 64000,
                    "default_context_window": 64000,
                },
                client_controls={"temperature": True, "max_tokens": True},
                secret_ref="env://DEEPSEEK_API_KEY",
                secret_source_type="env",
                has_secret_ref=True,
            ),
            ProviderRecord(
                name="anthropic",
                type="anthropic",
                base_url=(os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") or "").rstrip("/"),
                require_api_key=True,
                default_model=(os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest") or "").strip(),
                models=[(os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest") or "").strip()],
                capabilities={
                    "tools": True,
                    "json_schema": False,
                    "streaming": True,
                    "max_context_window": 200000,
                    "default_context_window": 200000,
                },
                client_controls={"temperature": True, "max_tokens": True},
                secret_ref="env://ANTHROPIC_API_KEY",
                secret_source_type="env",
                has_secret_ref=True,
            ),
            ProviderRecord(
                name="local",
                type="ollama",
                base_url=(os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434") or "").rstrip("/"),
                require_api_key=False,
                default_model=(os.environ.get("LOCAL_MODEL", "llama3") or "").strip(),
                models=[(os.environ.get("LOCAL_MODEL", "llama3") or "").strip()],
                capabilities={
                    "tools": False,
                    "json_schema": False,
                    "streaming": False,
                    "max_context_window": 8192,
                    "default_context_window": 8192,
                },
                client_controls={
                    "temperature": True,
                    "max_tokens": True,
                    "context_length": True,
                    "context_length_param": "num_ctx",
                },
                secret_ref=None,
                secret_source_type=None,
                has_secret_ref=False,
            ),
        ]

    def _default_mcp_settings_from_env(self) -> McpSettingsModel:
        raw = (os.environ.get("MCP_SERVERS", "") or "").strip()
        servers: List[McpServerRecord] = []
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    servers = [McpServerRecord.model_validate(item) for item in data if isinstance(item, dict)]
            except Exception:
                servers = []
        return McpSettingsModel(
            selected_servers=[server.name for server in servers],
            servers=servers,
            timeout_s=max(float(os.environ.get("MCP_TIMEOUT_S", "45") or "45"), 1.0),
            strict=(os.environ.get("MCP_STRICT", "") or "").strip().lower() in {"1", "true", "yes", "y"},
            max_tool_rounds=max(int(os.environ.get("MCP_MAX_TOOL_ROUNDS", "6") or "6"), 1),
        )

    def list_users(self) -> List[UserRead]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT u.username, u.is_active, u.must_rotate_password, u.is_bootstrap, ur.role_name
                FROM users u
                LEFT JOIN user_roles ur ON ur.user_id = u.id
                ORDER BY u.username, ur.role_name
                """
            ).fetchall()
        users: Dict[str, UserRead] = {}
        for row in rows:
            username = row["username"]
            if username not in users:
                users[username] = UserRead(
                    username=username,
                    roles=[],
                    is_active=bool(row["is_active"]),
                    must_rotate_password=bool(row["must_rotate_password"]),
                    is_bootstrap=bool(row["is_bootstrap"]),
                )
            role_name = row["role_name"]
            if role_name:
                users[username].roles.append(role_name)
        return list(users.values())

    def replace_users(self, users: Sequence[UserWrite], actor: str) -> List[UserRead]:
        with self.connect() as conn:
            existing = {
                row["username"]: row
                for row in conn.execute("SELECT id, username, password_hash, is_bootstrap FROM users").fetchall()
            }
            seen: set[str] = set()
            for user in users:
                username = user.username.strip()
                if not username:
                    raise ValueError("username is required")
                seen.add(username)
                now = iso_now()
                current = existing.get(username)
                if current is None:
                    if not user.password:
                        raise ValueError(f"password is required for new user '{username}'")
                    cursor = conn.execute(
                        """
                        INSERT INTO users(username, password_hash, is_active, must_rotate_password, is_bootstrap, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            username,
                            hash_password(user.password),
                            1 if user.is_active else 0,
                            1 if user.must_rotate_password else 0,
                            1 if user.is_bootstrap else 0,
                            now,
                            now,
                        ),
                    )
                    user_id = int(cursor.lastrowid)
                else:
                    password_hash = current["password_hash"]
                    if user.password:
                        password_hash = hash_password(user.password)
                    conn.execute(
                        """
                        UPDATE users
                        SET password_hash = ?, is_active = ?, must_rotate_password = ?, is_bootstrap = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            password_hash,
                            1 if user.is_active else 0,
                            1 if user.must_rotate_password else 0,
                            1 if user.is_bootstrap else 0,
                            now,
                            current["id"],
                        ),
                    )
                    user_id = int(current["id"])

                conn.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
                for role in sorted(set(user.roles)):
                    if role not in ROLE_PERMISSIONS:
                        raise ValueError(f"unknown role '{role}'")
                    conn.execute(
                        "INSERT INTO user_roles(user_id, role_name) VALUES (?, ?)",
                        (user_id, role),
                    )

            for username, row in existing.items():
                if username not in seen:
                    conn.execute("DELETE FROM users WHERE id = ?", (row["id"],))

            self._insert_audit(conn, actor, "users_replaced", {"count": len(users)})
        return self.list_users()

    def authenticate_user(self, username: str, password: str) -> Optional[UserSession]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT u.id, u.username, u.password_hash, u.is_active, u.must_rotate_password, ur.role_name
                FROM users u
                LEFT JOIN user_roles ur ON ur.user_id = u.id
                WHERE u.username = ?
                """,
                (username,),
            ).fetchall()
            if not row:
                return None
            head = row[0]
            if not bool(head["is_active"]):
                return None
            if not verify_password(password, head["password_hash"]):
                return None
            if needs_password_rehash(head["password_hash"]):
                conn.execute(
                    "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                    (hash_password(password), iso_now(), head["id"]),
                )
            roles = [r["role_name"] for r in row if r["role_name"]]
            permissions = sorted({perm for role in roles for perm in ROLE_PERMISSIONS.get(role, [])})
            return UserSession(
                username=head["username"],
                roles=roles,
                permissions=permissions,
                must_rotate_password=bool(head["must_rotate_password"]),
            )

    def create_session(self, username: str, ttl_hours: int = 8) -> str:
        token = new_session_token()
        token_hash = hash_session_token(token)
        expires_at = (utcnow() + timedelta(hours=ttl_hours)).isoformat()
        with self.connect() as conn:
            user_row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if user_row is None:
                raise ValueError("unknown user")
            conn.execute(
                """
                INSERT INTO sessions(token_hash, user_id, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (token_hash, user_row["id"], expires_at, iso_now()),
            )
        return token

    def get_session(self, token: str) -> Optional[UserSession]:
        token_hash = hash_session_token(token)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT u.username, u.is_active, u.must_rotate_password, s.expires_at, ur.role_name
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                LEFT JOIN user_roles ur ON ur.user_id = u.id
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchall()
            if not row:
                return None
            expires_at = datetime.fromisoformat(row[0]["expires_at"])
            if expires_at <= utcnow():
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                return None
            if not bool(row[0]["is_active"]):
                return None
            roles = [r["role_name"] for r in row if r["role_name"]]
            permissions = sorted({perm for role in roles for perm in ROLE_PERMISSIONS.get(role, [])})
            return UserSession(
                username=row[0]["username"],
                roles=roles,
                permissions=permissions,
                must_rotate_password=bool(row[0]["must_rotate_password"]),
            )

    def authenticate_api_key(self, token: str) -> Optional[UserSession]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT key_id, algorithm, salt_b64, hash_b64, subject, scopes_json
                FROM api_keys
                WHERE revoked_at IS NULL AND is_active = 1
                """
            ).fetchall()
        for row in rows:
            if verify_api_key(token, row["algorithm"], row["salt_b64"], row["hash_b64"]):
                scopes = sorted({str(scope) for scope in json.loads(row["scopes_json"] or "[]")})
                return UserSession(
                    username=f"service:{row['subject']}",
                    roles=["service"],
                    permissions=scopes,
                    auth_type="api_key",
                )
        return None

    def delete_session(self, token: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_session_token(token),))

    def list_api_keys(self) -> List[ApiKeyRead]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT key_id, subject, scopes_json, default_pipeline_id, allowed_providers_json,
                       allowed_models_json, max_input_tokens, max_output_tokens, max_total_tokens,
                       max_top_k, is_active, created_at
                FROM api_keys
                WHERE revoked_at IS NULL
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [self._api_key_read_from_row(row) for row in rows]

    def create_api_key(self, req: ApiKeyCreateRequest, actor: str) -> tuple[str, ApiKeyRead]:
        key_id = f"key_{int(time.time())}_{os.urandom(3).hex()}"
        plaintext_key = new_api_key()
        algorithm, salt_b64, hash_b64 = hash_api_key(plaintext_key)
        now = iso_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO api_keys(
                    key_id, algorithm, salt_b64, hash_b64, subject, scopes_json,
                    default_pipeline_id, allowed_providers_json, allowed_models_json,
                    max_input_tokens, max_output_tokens, max_total_tokens, max_top_k,
                    is_active, created_at, revoked_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL)
                """,
                (
                    key_id,
                    algorithm,
                    salt_b64,
                    hash_b64,
                    req.subject,
                    json.dumps(req.scopes),
                    req.default_pipeline_id,
                    json.dumps(req.allowed_providers) if req.allowed_providers is not None else None,
                    json.dumps(req.allowed_models) if req.allowed_models is not None else None,
                    req.max_input_tokens,
                    req.max_output_tokens,
                    req.max_total_tokens,
                    req.max_top_k,
                    now,
                ),
            )
            self._insert_audit(conn, actor, "api_key_created", {"key_id": key_id, "subject": req.subject})
        self.export_runtime_snapshots()
        return plaintext_key, self.get_api_key(key_id)

    def get_api_key(self, key_id: str) -> ApiKeyRead:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT key_id, subject, scopes_json, default_pipeline_id, allowed_providers_json,
                       allowed_models_json, max_input_tokens, max_output_tokens, max_total_tokens,
                       max_top_k, is_active, created_at
                FROM api_keys
                WHERE key_id = ? AND revoked_at IS NULL
                """,
                (key_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"unknown key '{key_id}'")
        return self._api_key_read_from_row(row)

    def revoke_api_key(self, key_id: str, actor: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE api_keys SET revoked_at = ?, is_active = 0 WHERE key_id = ?",
                (iso_now(), key_id),
            )
            self._insert_audit(conn, actor, "api_key_revoked", {"key_id": key_id})
        self.export_runtime_snapshots()

    def list_providers(self) -> List[ProviderRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT name, provider_type, base_url, require_api_key, default_model,
                       models_json, capabilities_json, client_controls_json, secret_ref, secret_source_type
                FROM providers
                ORDER BY name
                """
            ).fetchall()
        out: List[ProviderRecord] = []
        for row in rows:
            out.append(
                ProviderRecord(
                    name=row["name"],
                    type=row["provider_type"],
                    base_url=row["base_url"],
                    require_api_key=bool(row["require_api_key"]),
                    default_model=row["default_model"],
                    models=json.loads(row["models_json"] or "[]"),
                    capabilities=json.loads(row["capabilities_json"] or "{}"),
                    client_controls=json.loads(row["client_controls_json"] or "{}"),
                    secret_ref=row["secret_ref"],
                    secret_source_type=row["secret_source_type"],
                    has_secret_ref=bool(row["secret_ref"]),
                )
            )
        return out

    def replace_providers(self, providers: Sequence[ProviderRecord], actor: str) -> List[ProviderRecord]:
        with self.connect() as conn:
            conn.execute("DELETE FROM providers")
            now = iso_now()
            for provider in providers:
                conn.execute(
                    """
                    INSERT INTO providers(
                        name, provider_type, base_url, require_api_key, default_model,
                        models_json, capabilities_json, client_controls_json, secret_ref, secret_source_type,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        provider.name,
                        provider.type,
                        provider.base_url,
                        1 if provider.require_api_key else 0,
                        provider.default_model,
                        json.dumps(provider.models),
                        json.dumps(provider.capabilities.model_dump()),
                        json.dumps(provider.client_controls.model_dump(exclude_none=True)),
                        provider.secret_ref,
                        provider.secret_source_type,
                        now,
                        now,
                    ),
                )
            self._insert_audit(conn, actor, "providers_replaced", {"count": len(providers)})
        self.export_runtime_snapshots()
        return self.list_providers()

    def list_policies(self) -> List[PolicyRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT payload_json FROM policies ORDER BY pipeline_id").fetchall()
        return [PolicyRecord.model_validate(json.loads(row["payload_json"])) for row in rows]

    def replace_policies(self, policies: Sequence[PolicyRecord], actor: str) -> List[PolicyRecord]:
        with self.connect() as conn:
            conn.execute("DELETE FROM policies")
            now = iso_now()
            for policy in policies:
                payload = policy.model_dump()
                conn.execute(
                    """
                    INSERT INTO policies(pipeline_id, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (policy.pipeline_id, json.dumps(payload), now, now),
                )
            self._insert_audit(conn, actor, "policies_replaced", {"count": len(policies)})
        self.export_runtime_snapshots()
        return self.list_policies()

    def list_processors(self) -> List[ProcessorRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT payload_json FROM processors ORDER BY processor_id").fetchall()
        return [ProcessorRecord.model_validate(json.loads(row["payload_json"])) for row in rows]

    def replace_processors(self, processors: Sequence[ProcessorRecord], actor: str) -> List[ProcessorRecord]:
        with self.connect() as conn:
            conn.execute("DELETE FROM processors")
            now = iso_now()
            for processor in processors:
                payload = processor.model_dump()
                conn.execute(
                    """
                    INSERT INTO processors(processor_id, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (processor.processor_id, json.dumps(payload), now, now),
                )
            self._insert_audit(conn, actor, "processors_replaced", {"count": len(processors)})
        self.export_runtime_snapshots()
        return self.list_processors()

    def list_retrieval_profiles(self) -> List[RetrievalProfileRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT payload_json FROM retrieval_profiles ORDER BY retrieval_profile_id").fetchall()
        return [RetrievalProfileRecord.model_validate(json.loads(row["payload_json"])) for row in rows]

    def replace_retrieval_profiles(
        self, retrieval_profiles: Sequence[RetrievalProfileRecord], actor: str
    ) -> List[RetrievalProfileRecord]:
        with self.connect() as conn:
            conn.execute("DELETE FROM retrieval_profiles")
            now = iso_now()
            for profile in retrieval_profiles:
                payload = profile.model_dump()
                conn.execute(
                    """
                    INSERT INTO retrieval_profiles(retrieval_profile_id, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (profile.retrieval_profile_id, json.dumps(payload), now, now),
                )
            self._insert_audit(conn, actor, "retrieval_profiles_replaced", {"count": len(retrieval_profiles)})
        self.export_runtime_snapshots()
        return self.list_retrieval_profiles()

    def get_rag_settings(self) -> RagSettingsModel:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT default_corpus_id, selected_corpus_ids_json, default_top_k, retrieval_api_url
                FROM rag_settings
                WHERE singleton_id = 1
                """
            ).fetchone()
        if row is None:
            return RagSettingsModel()
        selected_corpus_ids = json.loads(row["selected_corpus_ids_json"] or "[]")
        if not selected_corpus_ids:
            selected_corpus_ids = [row["default_corpus_id"]]
        return RagSettingsModel(
            default_corpus_id=row["default_corpus_id"],
            selected_corpus_ids=selected_corpus_ids,
            default_top_k=int(row["default_top_k"]),
            retrieval_api_url=row["retrieval_api_url"],
        )

    def update_rag_settings(self, settings: RagSettingsModel, actor: str) -> RagSettingsModel:
        selected_corpus_ids = [
            item for item in dict.fromkeys((item or "").strip() for item in settings.selected_corpus_ids) if item
        ]
        default_corpus_id = (settings.default_corpus_id or "").strip()
        if not default_corpus_id:
            default_corpus_id = selected_corpus_ids[0] if selected_corpus_ids else "default"
        if default_corpus_id not in selected_corpus_ids:
            selected_corpus_ids.insert(0, default_corpus_id)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO rag_settings(singleton_id, default_corpus_id, selected_corpus_ids_json, default_top_k, retrieval_api_url, updated_at)
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    default_corpus_id = excluded.default_corpus_id,
                    selected_corpus_ids_json = excluded.selected_corpus_ids_json,
                    default_top_k = excluded.default_top_k,
                    retrieval_api_url = excluded.retrieval_api_url,
                    updated_at = excluded.updated_at
                """,
                (
                    default_corpus_id,
                    json.dumps(selected_corpus_ids),
                    settings.default_top_k,
                    settings.retrieval_api_url,
                    iso_now(),
                ),
            )
            self._insert_audit(
                conn,
                actor,
                "rag_settings_updated",
                {
                    "default_corpus_id": default_corpus_id,
                    "selected_corpus_ids": selected_corpus_ids,
                    "default_top_k": settings.default_top_k,
                    "retrieval_api_url": settings.retrieval_api_url,
                },
            )
        self.export_runtime_snapshots()
        return self.get_rag_settings()

    def get_mcp_settings(self) -> McpSettingsModel:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT selected_servers_json, servers_json, timeout_s, strict, max_tool_rounds
                FROM mcp_settings
                WHERE singleton_id = 1
                """
            ).fetchone()
        if row is None:
            return self._default_mcp_settings_from_env()
        servers_data = json.loads(row["servers_json"] or "[]")
        return McpSettingsModel(
            selected_servers=json.loads(row["selected_servers_json"] or "[]"),
            servers=[McpServerRecord.model_validate(item) for item in servers_data if isinstance(item, dict)],
            timeout_s=float(row["timeout_s"]),
            strict=bool(row["strict"]),
            max_tool_rounds=int(row["max_tool_rounds"]),
        )

    def update_mcp_settings(self, settings: McpSettingsModel, actor: str) -> McpSettingsModel:
        server_names = {server.name for server in settings.servers}
        selected_servers = [
            item
            for item in dict.fromkeys((item or "").strip() for item in settings.selected_servers)
            if item in server_names
        ]
        if not selected_servers and settings.servers:
            selected_servers = [server.name for server in settings.servers]
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO mcp_settings(singleton_id, selected_servers_json, servers_json, timeout_s, strict, max_tool_rounds, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    selected_servers_json = excluded.selected_servers_json,
                    servers_json = excluded.servers_json,
                    timeout_s = excluded.timeout_s,
                    strict = excluded.strict,
                    max_tool_rounds = excluded.max_tool_rounds,
                    updated_at = excluded.updated_at
                """,
                (
                    json.dumps(selected_servers),
                    json.dumps([server.model_dump(exclude_none=True) for server in settings.servers]),
                    settings.timeout_s,
                    1 if settings.strict else 0,
                    settings.max_tool_rounds,
                    iso_now(),
                ),
            )
            self._insert_audit(
                conn,
                actor,
                "mcp_settings_updated",
                {
                    "selected_servers": selected_servers,
                    "servers_count": len(settings.servers),
                    "timeout_s": settings.timeout_s,
                    "strict": settings.strict,
                    "max_tool_rounds": settings.max_tool_rounds,
                },
            )
        self.export_runtime_snapshots()
        return self.get_mcp_settings()

    def get_status(self) -> AdminStatus:
        with self.connect() as conn:
            users_count = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
            providers_count = int(conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0])
            policies_count = int(conn.execute("SELECT COUNT(*) FROM policies").fetchone()[0])
            machine_keys_count = int(
                conn.execute("SELECT COUNT(*) FROM api_keys WHERE revoked_at IS NULL AND is_active = 1").fetchone()[0]
            )
        rag = self.get_rag_settings()
        mcp = self.get_mcp_settings()
        return AdminStatus(
            ok=True,
            users_count=users_count,
            providers_count=providers_count,
            policies_count=policies_count,
            machine_keys_count=machine_keys_count,
            mcp_servers_count=len(mcp.selected_servers),
            default_corpus_id=rag.default_corpus_id,
            retrieval_api_url=rag.retrieval_api_url,
        )

    def create_corpus(self, request: CorpusCreateRequest, actor: str) -> CorpusDetail:
        corpus_id = normalize_corpus_id(request.corpus_id)
        now = iso_now()
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM corpora WHERE corpus_id = ? AND deleted_at IS NOT NULL",
                (corpus_id,),
            )
            try:
                conn.execute(
                    """
                    INSERT INTO corpora(
                        corpus_id, title, description, environment, tenant_id,
                        chunking_json, index_json, processor_id,
                        processor_config_json, retrieval_profile_id, retrieval_config_json,
                        metadata_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        corpus_id,
                        request.title,
                        request.description,
                        request.environment,
                        request.tenant_id,
                        json.dumps(request.chunking),
                        json.dumps(request.index),
                        request.processor_id,
                        json.dumps(request.processor_config),
                        request.retrieval_profile_id,
                        json.dumps(request.retrieval_config),
                        json.dumps(request.metadata),
                        now,
                        now,
                    ),
                )
                self._insert_audit(conn, actor, "corpus_created", {"corpus_id": corpus_id})
            except sqlite3.IntegrityError:
                raise ValueError(f"Corpus {corpus_id} already exists")
        return self.get_corpus_detail(corpus_id)

    def ensure_corpus(self, corpus_id: str, request: CorpusEnsureRequest, actor: str) -> CorpusDetail:
        corpus_id = normalize_corpus_id(corpus_id)
        try:
            return self.get_corpus_detail(corpus_id)
        except FileNotFoundError:
            pass

        try:
            return self.create_corpus(
                CorpusCreateRequest(
                    corpus_id=corpus_id,
                    title=request.title,
                    description=request.description,
                    environment=request.environment,
                    tenant_id=request.tenant_id,
                    chunking=request.chunking,
                    index=request.index,
                    processor_id=request.processor_id,
                    processor_config=request.processor_config,
                    retrieval_profile_id=request.retrieval_profile_id,
                    retrieval_config=request.retrieval_config,
                    metadata=request.metadata,
                ),
                actor,
            )
        except ValueError as exc:
            if "already exists" not in str(exc):
                raise
            return self.get_corpus_detail(corpus_id)

    def update_corpus(self, corpus_id: str, request: CorpusUpdateRequest, actor: str) -> CorpusDetail:
        corpus_id = normalize_corpus_id(corpus_id)
        now = iso_now()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM corpora WHERE corpus_id = ? AND deleted_at IS NULL",
                (corpus_id,),
            ).fetchone()
            if not row:
                raise FileNotFoundError(f"Corpus {corpus_id} not found")

            updates = []
            params = []
            if request.title is not None:
                updates.append("title = ?")
                params.append(request.title)
            if request.description is not None:
                updates.append("description = ?")
                params.append(request.description)
            if request.environment is not None:
                updates.append("environment = ?")
                params.append(request.environment)
            if request.tenant_id is not None:
                updates.append("tenant_id = ?")
                params.append(request.tenant_id)
            if request.chunking is not None:
                updates.append("chunking_json = ?")
                params.append(json.dumps(request.chunking))
            if request.index is not None:
                updates.append("index_json = ?")
                params.append(json.dumps(request.index))
            if request.processor_id is not None:
                updates.append("processor_id = ?")
                params.append(request.processor_id)
            if request.processor_config is not None:
                updates.append("processor_config_json = ?")
                params.append(json.dumps(request.processor_config))
            if request.retrieval_profile_id is not None:
                updates.append("retrieval_profile_id = ?")
                params.append(request.retrieval_profile_id)
            if request.retrieval_config is not None:
                updates.append("retrieval_config_json = ?")
                params.append(json.dumps(request.retrieval_config))
            if request.metadata is not None:
                updates.append("metadata_json = ?")
                params.append(json.dumps(request.metadata))

            if updates:
                updates.append("updated_at = ?")
                params.append(now)
                params.append(corpus_id)
                conn.execute(f"UPDATE corpora SET {', '.join(updates)} WHERE corpus_id = ?", params)
                self._insert_audit(conn, actor, "corpus_updated", {"corpus_id": corpus_id})
        return self.get_corpus_detail(corpus_id)

    def delete_corpus(self, corpus_id: str, actor: str) -> None:
        corpus_id = normalize_corpus_id(corpus_id)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM corpora WHERE corpus_id = ? AND deleted_at IS NULL",
                (corpus_id,),
            ).fetchone()
            if not row:
                raise FileNotFoundError(f"Corpus {corpus_id} not found")
            active_jobs = conn.execute(
                "SELECT 1 FROM ingestion_jobs WHERE corpus_id = ? AND status IN ('pending', 'running')",
                (corpus_id,),
            ).fetchone()
            if active_jobs:
                raise ValueError(f"Corpus {corpus_id} has active ingestion jobs")
            conn.execute("DELETE FROM corpora WHERE corpus_id = ?", (corpus_id,))
            self._insert_audit(conn, actor, "corpus_deleted", {"corpus_id": corpus_id})

    def list_corpus_sources(self, corpus_id: str) -> List[CorpusSourceRecord]:
        corpus_id = normalize_corpus_id(corpus_id)
        with self.connect() as conn:
            corpus = conn.execute(
                "SELECT 1 FROM corpora WHERE corpus_id = ? AND deleted_at IS NULL",
                (corpus_id,),
            ).fetchone()
            if not corpus:
                raise FileNotFoundError(f"Corpus {corpus_id} not found")
            rows = conn.execute(
                "SELECT * FROM corpus_sources WHERE corpus_id = ? AND deleted_at IS NULL",
                (corpus_id,),
            ).fetchall()
            return [
                CorpusSourceRecord(
                    id=row["source_id"],
                    type=row["type"],
                    format=row["format"],
                    title=row["title"],
                    path=row["path"],
                    local_path=row["local_path"],
                    url=row["url"],
                    object_uri=row["object_uri"],
                    content_hash=row["content_hash"],
                    size_bytes=row["size_bytes"],
                    content_type=row["content_type"],
                    language=row["language"],
                    doc_type=row["doc_type"],
                    tags=json.loads(row["tags_json"]),
                    configuration=json.loads(row["configuration_json"]),
                    processor_id=row["processor_id"],
                    processor_config=json.loads(row["processor_config_json"]),
                    metadata=json.loads(row["metadata_json"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    deleted_at=row["deleted_at"],
                )
                for row in rows
            ]

    def create_corpus_source(
        self, corpus_id: str, request: CorpusSourceCreateRequest, actor: str
    ) -> CorpusSourceRecord:
        corpus_id = normalize_corpus_id(corpus_id)
        now = iso_now()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM corpora WHERE corpus_id = ? AND deleted_at IS NULL",
                (corpus_id,),
            ).fetchone()
            if not row:
                raise FileNotFoundError(f"Corpus {corpus_id} not found")
            try:
                conn.execute(
                    """
                    INSERT INTO corpus_sources(
                        source_id, corpus_id, type, format, title, url, object_uri,
                        content_type, language, doc_type, tags_json,
                        configuration_json, processor_id, processor_config_json,
                        metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.source_id,
                        corpus_id,
                        request.type,
                        request.format,
                        request.title,
                        request.url,
                        request.object_uri,
                        request.content_type,
                        request.language,
                        request.doc_type,
                        json.dumps(request.tags),
                        json.dumps(request.configuration),
                        request.processor_id,
                        json.dumps(request.processor_config),
                        json.dumps(request.metadata),
                        now,
                        now,
                    ),
                )
                self._insert_audit(
                    conn, actor, "corpus_source_created", {"corpus_id": corpus_id, "source_id": request.source_id}
                )
            except sqlite3.IntegrityError:
                existing = conn.execute(
                    "SELECT deleted_at FROM corpus_sources WHERE corpus_id = ? AND source_id = ?",
                    (corpus_id, request.source_id),
                ).fetchone()
                if not existing or existing["deleted_at"] is None:
                    raise ValueError(f"Source {request.source_id} already exists in corpus {corpus_id}")
                conn.execute(
                    """
                    UPDATE corpus_sources
                    SET type = ?, format = ?, title = ?, path = NULL, local_path = NULL,
                        url = ?, object_uri = ?, content_hash = NULL, size_bytes = NULL,
                        content_type = ?, language = ?, doc_type = ?, tags_json = ?,
                        configuration_json = ?, processor_id = ?,
                        processor_config_json = ?, metadata_json = ?,
                        created_at = ?, updated_at = ?, deleted_at = NULL
                    WHERE corpus_id = ? AND source_id = ?
                    """,
                    (
                        request.type,
                        request.format,
                        request.title,
                        request.url,
                        request.object_uri,
                        request.content_type,
                        request.language,
                        request.doc_type,
                        json.dumps(request.tags),
                        json.dumps(request.configuration),
                        request.processor_id,
                        json.dumps(request.processor_config),
                        json.dumps(request.metadata),
                        now,
                        now,
                        corpus_id,
                        request.source_id,
                    ),
                )
                self._insert_audit(
                    conn, actor, "corpus_source_restored", {"corpus_id": corpus_id, "source_id": request.source_id}
                )
        return self.get_corpus_source(corpus_id, request.source_id)

    def get_corpus_source(self, corpus_id: str, source_id: str) -> CorpusSourceRecord:
        corpus_id = normalize_corpus_id(corpus_id)
        source_id = normalize_source_id(source_id)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM corpus_sources WHERE corpus_id = ? AND source_id = ? AND deleted_at IS NULL",
                (corpus_id, source_id),
            ).fetchone()
            if row:
                return CorpusSourceRecord(
                    id=row["source_id"],
                    type=row["type"],
                    format=row["format"],
                    title=row["title"],
                    path=row["path"],
                    local_path=row["local_path"],
                    url=row["url"],
                    object_uri=row["object_uri"],
                    content_hash=row["content_hash"],
                    size_bytes=row["size_bytes"],
                    content_type=row["content_type"],
                    language=row["language"],
                    doc_type=row["doc_type"],
                    tags=json.loads(row["tags_json"]),
                    configuration=json.loads(row["configuration_json"]),
                    processor_id=row["processor_id"],
                    processor_config=json.loads(row["processor_config_json"]),
                    metadata=json.loads(row["metadata_json"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    deleted_at=row["deleted_at"],
                )
        raise FileNotFoundError(f"Source {source_id} not found in corpus {corpus_id}")

    def update_corpus_source(
        self, corpus_id: str, source_id: str, request: CorpusSourceCreateRequest, actor: str
    ) -> CorpusSourceRecord:
        corpus_id = normalize_corpus_id(corpus_id)
        source_id = normalize_source_id(source_id)
        if request.source_id != source_id:
            raise ValueError("source_id in the request must match the URL path")
        now = iso_now()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM corpus_sources WHERE corpus_id = ? AND source_id = ? AND deleted_at IS NULL",
                (corpus_id, source_id),
            ).fetchone()
            if not row:
                raise FileNotFoundError(f"Source {source_id} not found in corpus {corpus_id}")
            conn.execute(
                """
                UPDATE corpus_sources SET type = ?, format = ?, title = ?,
                    url = ?, object_uri = ?, content_type = ?, language = ?,
                    doc_type = ?, tags_json = ?, configuration_json = ?,
                    processor_id = ?, processor_config_json = ?,
                    metadata_json = ?, updated_at = ?
                WHERE corpus_id = ? AND source_id = ?
                """,
                (
                    request.type,
                    request.format,
                    request.title,
                    request.url,
                    request.object_uri,
                    request.content_type,
                    request.language,
                    request.doc_type,
                    json.dumps(request.tags),
                    json.dumps(request.configuration),
                    request.processor_id,
                    json.dumps(request.processor_config),
                    json.dumps(request.metadata),
                    now,
                    corpus_id,
                    source_id,
                ),
            )
            self._insert_audit(conn, actor, "corpus_source_updated", {"corpus_id": corpus_id, "source_id": source_id})
        return self.get_corpus_source(corpus_id, source_id)

    def create_ingestion_job(
        self, corpus_id: str, request: IngestionJobCreateRequest, actor: str
    ) -> IngestionJobStatus:
        import uuid

        corpus_id = normalize_corpus_id(corpus_id)
        job_id = str(uuid.uuid4())
        now = iso_now()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM corpora WHERE corpus_id = ? AND deleted_at IS NULL",
                (corpus_id,),
            ).fetchone()
            if not row:
                raise FileNotFoundError(f"Corpus {corpus_id} not found")
            if request.source_ids:
                requested_source_ids = {normalize_source_id(source_id) for source_id in request.source_ids}
                active_sources = {
                    source["source_id"]
                    for source in conn.execute(
                        "SELECT source_id FROM corpus_sources WHERE corpus_id = ? AND deleted_at IS NULL",
                        (corpus_id,),
                    ).fetchall()
                }
                missing_source_ids = requested_source_ids - active_sources
                if missing_source_ids:
                    missing = ", ".join(sorted(missing_source_ids))
                    raise ValueError(f"Unknown active source IDs for corpus {corpus_id}: {missing}")
            conn.execute(
                """
                INSERT INTO ingestion_jobs(
                    job_id, corpus_id, environment, tenant_id, status, request_json, plan_json,
                    stats_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    corpus_id,
                    row["environment"],
                    row["tenant_id"],
                    "pending",
                    json.dumps(request.model_dump()),
                    json.dumps({"to_embed_source_ids": request.source_ids or [], "skipped_source_ids": []}),
                    "{}",
                    now,
                    now,
                ),
            )
            self._insert_audit(conn, actor, "ingestion_job_created", {"job_id": job_id, "corpus_id": corpus_id})
        return self.get_ingestion_job(job_id)

    def list_ingestion_jobs(self) -> List[IngestionJobStatus]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM ingestion_jobs ORDER BY created_at DESC").fetchall()
            return [
                IngestionJobStatus(
                    job_id=row["job_id"],
                    corpus_id=row["corpus_id"],
                    environment=row["environment"],
                    tenant_id=row["tenant_id"],
                    status=row["status"],
                    request=json.loads(row["request_json"]),
                    plan=json.loads(row["plan_json"]),
                    stats=json.loads(row["stats_json"]),
                    error=row["error"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                for row in rows
            ]

    def get_ingestion_job(self, job_id: str) -> IngestionJobStatus:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM ingestion_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                raise FileNotFoundError(f"Job {job_id} not found")
            return IngestionJobStatus(
                job_id=row["job_id"],
                corpus_id=row["corpus_id"],
                environment=row["environment"],
                tenant_id=row["tenant_id"],
                status=row["status"],
                request=json.loads(row["request_json"]),
                plan=json.loads(row["plan_json"]),
                stats=json.loads(row["stats_json"]),
                error=row["error"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def get_corpus_readiness(self, corpus_id: str) -> CorpusReadiness:
        corpus_id = normalize_corpus_id(corpus_id)
        with self.connect() as conn:
            corpus = conn.execute(
                "SELECT 1 FROM corpora WHERE corpus_id = ? AND deleted_at IS NULL",
                (corpus_id,),
            ).fetchone()
            if not corpus:
                raise FileNotFoundError(f"Corpus {corpus_id} not found")

            source_row = conn.execute(
                """
                SELECT COUNT(*) AS source_count, MAX(updated_at) AS latest_source_updated_at
                FROM corpus_sources
                WHERE corpus_id = ? AND deleted_at IS NULL
                """,
                (corpus_id,),
            ).fetchone()
            latest_completed = conn.execute(
                """
                SELECT job_id, updated_at
                FROM ingestion_jobs
                WHERE corpus_id = ? AND status = 'completed'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (corpus_id,),
            ).fetchone()
            latest_job = conn.execute(
                """
                SELECT job_id, status
                FROM ingestion_jobs
                WHERE corpus_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (corpus_id,),
            ).fetchone()

        source_count = int(source_row["source_count"] or 0)
        latest_source_updated_at = source_row["latest_source_updated_at"]
        latest_completed_at = latest_completed["updated_at"] if latest_completed else None
        reasons: List[str] = []

        if source_count == 0:
            reasons.append("no_active_sources")
        if latest_completed is None:
            reasons.append("no_completed_ingestion_job")
        elif latest_source_updated_at and latest_completed_at and latest_source_updated_at > latest_completed_at:
            reasons.append("sources_changed_after_latest_completed_ingestion")

        ready = not reasons
        if ready:
            status = "ready"
        elif latest_job and latest_job["status"] in {"pending", "running"}:
            status = latest_job["status"]
        elif latest_job and latest_job["status"] == "failed":
            status = "failed"
        else:
            status = "not_ready"

        return CorpusReadiness(
            corpus_id=corpus_id,
            ready=ready,
            status=status,
            source_count=source_count,
            latest_source_updated_at=latest_source_updated_at,
            latest_completed_job_id=latest_completed["job_id"] if latest_completed else None,
            latest_completed_job_updated_at=latest_completed_at,
            latest_job_id=latest_job["job_id"] if latest_job else None,
            latest_job_status=latest_job["status"] if latest_job else None,
            reasons=reasons,
        )

    def cancel_ingestion_job(self, job_id: str, actor: str) -> IngestionJobStatus:
        now = iso_now()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM ingestion_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                raise FileNotFoundError(f"Job {job_id} not found")
            if row["status"] in ("completed", "failed", "cancelled"):
                raise ValueError(f"Job {job_id} cannot be cancelled from status {row['status']}")
            conn.execute(
                "UPDATE ingestion_jobs SET status = 'cancelled', updated_at = ? WHERE job_id = ?", (now, job_id)
            )
            self._insert_audit(conn, actor, "ingestion_job_cancelled", {"job_id": job_id})
        return self.get_ingestion_job(job_id)

    def claim_ingestion_job(self, job_id: str, worker_id: str) -> IngestionJobStatus:
        now = iso_now()
        with self.connect() as conn:
            updated = conn.execute(
                "UPDATE ingestion_jobs SET status = 'running', updated_at = ? WHERE job_id = ? AND status = 'pending'",
                (now, job_id),
            )
            if updated.rowcount == 0:
                row = conn.execute("SELECT status FROM ingestion_jobs WHERE job_id = ?", (job_id,)).fetchone()
                if not row:
                    raise FileNotFoundError(f"Job {job_id} not found")
                raise ValueError(f"Job {job_id} cannot be claimed from status {row['status']}")
            self._insert_audit(conn, f"worker:{worker_id}", "ingestion_job_claimed", {"job_id": job_id})
        return self.get_ingestion_job(job_id)

    def update_ingestion_job(
        self, job_id: str, status: Optional[str], stats: Optional[Dict[str, Any]], error: Optional[str], worker_id: str
    ) -> IngestionJobStatus:
        allowed_statuses = {"pending", "running", "completed", "failed", "cancelled"}
        if status is not None and status not in allowed_statuses:
            raise ValueError(f"Unsupported ingestion job status: {status}")
        now = iso_now()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM ingestion_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                raise FileNotFoundError(f"Job {job_id} not found")

            updates = []
            params = []
            if status is not None:
                updates.append("status = ?")
                params.append(status)
            if stats is not None:
                updates.append("stats_json = ?")
                params.append(json.dumps(stats))
            if error is not None:
                updates.append("error = ?")
                params.append(error)

            if updates:
                updates.append("updated_at = ?")
                params.append(now)
                params.append(job_id)
                conn.execute(f"UPDATE ingestion_jobs SET {', '.join(updates)} WHERE job_id = ?", params)
                self._insert_audit(
                    conn, f"worker:{worker_id}", "ingestion_job_updated", {"job_id": job_id, "status": status}
                )
        return self.get_ingestion_job(job_id)

    def heartbeat_ingestion_job(self, job_id: str, worker_id: str) -> None:
        with self.connect() as conn:
            updated = conn.execute(
                "UPDATE ingestion_jobs SET updated_at = ? WHERE job_id = ? AND status = 'running'",
                (iso_now(), job_id),
            )
            if updated.rowcount == 0:
                row = conn.execute("SELECT status FROM ingestion_jobs WHERE job_id = ?", (job_id,)).fetchone()
                if not row:
                    raise FileNotFoundError(f"Job {job_id} not found")
                raise ValueError(f"Job {job_id} cannot be heartbeated from status {row['status']}")
            self._insert_audit(conn, f"worker:{worker_id}", "ingestion_job_heartbeat", {"job_id": job_id})

    def list_corpora(self) -> List[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT corpus_id FROM corpora WHERE deleted_at IS NULL").fetchall()
            return [row["corpus_id"] for row in rows]

    def get_corpus_detail(self, corpus_id: str) -> CorpusDetail:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM corpora WHERE corpus_id = ? AND deleted_at IS NULL",
                (corpus_id,),
            ).fetchone()
            if row:
                sources = self.list_corpus_sources(corpus_id)
                return CorpusDetail(
                    corpus_id=row["corpus_id"],
                    title=row["title"],
                    description=row["description"],
                    environment=row["environment"],
                    tenant_id=row["tenant_id"],
                    chunking=json.loads(row["chunking_json"]),
                    index=json.loads(row["index_json"]),
                    processor_id=row["processor_id"],
                    processor_config=json.loads(row["processor_config_json"]),
                    retrieval_profile_id=row["retrieval_profile_id"],
                    retrieval_config=json.loads(row["retrieval_config_json"]),
                    metadata=json.loads(row["metadata_json"]),
                    source_count=len(sources),
                    sources=sources,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    deleted_at=row["deleted_at"],
                )
        raise FileNotFoundError(f"Corpus {corpus_id} not found")

    def export_corpus_registry_bundle(self, corpus_id: str) -> CorpusRegistryBundle:
        detail = self.get_corpus_detail(corpus_id)
        return CorpusRegistryBundle(
            exported_at=iso_now(),
            corpus=detail,
            notes=[
                "This bundle migrates config-auth registry metadata only.",
                "Object-backed sources keep their existing s3:// object_uri values; copy object bytes separately if the target instance uses different object storage.",
                "Generated retrieval indexes are not included; re-ingest the corpus after import.",
            ],
        )

    def import_corpus_registry_bundle(
        self,
        bundle: CorpusRegistryBundle,
        *,
        actor: str,
        conflict_strategy: str = "fail",
    ) -> CorpusRegistryImportResult:
        if bundle.schema_version != "config-auth.corpus-registry.v1":
            raise ValueError(f"Unsupported corpus registry bundle schema_version: {bundle.schema_version}")
        strategy = str(conflict_strategy or "fail").strip().lower()
        if strategy not in {"fail", "replace"}:
            raise ValueError("conflict_strategy must be 'fail' or 'replace'")

        corpus = bundle.corpus
        corpus_id = normalize_corpus_id(corpus.corpus_id)
        now = iso_now()
        sources = list(corpus.sources or [])
        for source in sources:
            CorpusSourceCreateRequest(
                source_id=source.id,
                type=source.type or "",
                format=source.format or "",
                title=source.title,
                url=source.url,
                object_uri=source.object_uri,
                content_type=source.content_type,
                language=source.language,
                doc_type=source.doc_type,
                tags=source.tags or [],
                configuration=source.configuration or {},
                metadata=source.metadata or {},
                processor_id=source.processor_id,
                processor_config=source.processor_config or {},
            )

        with self.connect() as conn:
            conn.execute(
                "DELETE FROM corpora WHERE corpus_id = ? AND deleted_at IS NOT NULL",
                (corpus_id,),
            )
            existing = conn.execute(
                "SELECT 1 FROM corpora WHERE corpus_id = ? AND deleted_at IS NULL",
                (corpus_id,),
            ).fetchone()
            if existing and strategy == "fail":
                raise ValueError(f"Corpus {corpus_id} already exists")
            if existing and strategy == "replace":
                active_jobs = conn.execute(
                    "SELECT 1 FROM ingestion_jobs WHERE corpus_id = ? AND status IN ('pending', 'running')",
                    (corpus_id,),
                ).fetchone()
                if active_jobs:
                    raise ValueError(f"Corpus {corpus_id} has active ingestion jobs")
                conn.execute("DELETE FROM corpora WHERE corpus_id = ?", (corpus_id,))

            conn.execute(
                """
                INSERT INTO corpora(
                    corpus_id, title, description, environment, tenant_id,
                    chunking_json, index_json, processor_id, processor_config_json,
                    retrieval_profile_id, retrieval_config_json, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    corpus_id,
                    corpus.title,
                    corpus.description,
                    corpus.environment,
                    corpus.tenant_id,
                    json.dumps(corpus.chunking or {}),
                    json.dumps(corpus.index or {}),
                    corpus.processor_id,
                    json.dumps(corpus.processor_config or {}),
                    corpus.retrieval_profile_id,
                    json.dumps(corpus.retrieval_config or {}),
                    json.dumps(corpus.metadata or {}),
                    corpus.created_at or now,
                    now,
                ),
            )

            for source in sources:
                source_id = normalize_source_id(source.id)
                source_type = str(source.type or "").strip()
                source_format = str(source.format or "").strip()
                conn.execute(
                    """
                    INSERT INTO corpus_sources(
                        source_id, corpus_id, type, format, title, path, local_path,
                        url, object_uri, content_hash, size_bytes, content_type,
                        language, doc_type, tags_json, configuration_json,
                        processor_id, processor_config_json, metadata_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        corpus_id,
                        source_type,
                        source_format,
                        source.title,
                        source.url,
                        source.object_uri,
                        source.content_hash,
                        source.size_bytes,
                        source.content_type,
                        source.language,
                        source.doc_type,
                        json.dumps(source.tags or []),
                        json.dumps(source.configuration or {}),
                        source.processor_id,
                        json.dumps(source.processor_config or {}),
                        json.dumps(source.metadata or {}),
                        source.created_at or now,
                        now,
                    ),
                )
            self._insert_audit(
                conn,
                actor,
                "corpus_registry_imported",
                {"corpus_id": corpus_id, "sources": len(sources), "conflict_strategy": strategy},
            )

        self.export_runtime_snapshots()
        return CorpusRegistryImportResult(
            status="imported",
            corpus_id=corpus_id,
            sources_imported=len(sources),
            conflict_strategy=strategy,
            notes=[
                "Registry metadata imported. Re-ingest this corpus to rebuild Qdrant and lexical indexes.",
                "Object-backed sources require the target instance to reach the referenced s3:// object URIs.",
            ],
        )

    def add_uploaded_corpus_source(
        self,
        corpus_id: str,
        *,
        source_id: str,
        title: Optional[str],
        filename: str,
        content: bytes,
        format: str,
        language: Optional[str],
        doc_type: Optional[str],
        tags: Optional[List[str]],
        configuration: Optional[Dict[str, Any]],
        processor_id: Optional[str],
        processor_config: Optional[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]],
        actor: str,
        content_type: Optional[str] = None,
    ) -> CorpusSourceRecord:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM corpora WHERE corpus_id = ? AND deleted_at IS NULL",
                (corpus_id,),
            ).fetchone()
            if not row:
                raise FileNotFoundError(f"Corpus {corpus_id} not found")

            if self.object_storage is None:
                raise ValueError("S3-compatible object storage is required for uploads")
            source_id = normalize_source_id(source_id)
            if len(content) > self.max_upload_bytes:
                raise ValueError(f"uploaded file exceeds the {self.max_upload_bytes}-byte limit")

            # Simple format normalization and validation
            normalized_format = format.lower().strip()
            if not normalized_format:
                normalized_format = "text"

            # Simple filename sanitization
            import re

            safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)
            if not safe_name:
                raise ValueError("uploaded filename is invalid")

            resolved_content_type = content_type or "application/octet-stream"
            stored = self.object_storage.put_source_bytes(
                environment=row["environment"] or self.default_storage_environment,
                tenant_id=row["tenant_id"] or self.default_storage_tenant_id,
                corpus_id=corpus_id,
                source_id=source_id,
                original_name=safe_name,
                content=content,
                content_type=resolved_content_type,
                metadata={"source_id": source_id, "format": normalized_format},
            )

            now = iso_now()
            try:
                conn.execute(
                    """
                    INSERT INTO corpus_sources(
                        source_id, corpus_id, type, format, title, object_uri,
                        content_hash, size_bytes, content_type, language,
                        doc_type, tags_json, configuration_json, processor_id,
                        processor_config_json, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        corpus_id,
                        "object",
                        normalized_format,
                        title or safe_name,
                        stored.object_uri,
                        stored.content_hash,
                        stored.size_bytes,
                        stored.content_type,
                        language,
                        doc_type,
                        json.dumps(tags or []),
                        json.dumps(configuration or {}),
                        processor_id,
                        json.dumps(processor_config or {}),
                        json.dumps(metadata or {}),
                        now,
                        now,
                    ),
                )
                self._insert_audit(
                    conn, actor, "corpus_source_uploaded", {"corpus_id": corpus_id, "source_id": source_id}
                )
            except sqlite3.IntegrityError:
                existing = conn.execute(
                    "SELECT deleted_at FROM corpus_sources WHERE corpus_id = ? AND source_id = ?",
                    (corpus_id, source_id),
                ).fetchone()
                if not existing or existing["deleted_at"] is None:
                    raise ValueError(f"Source {source_id} already exists in corpus {corpus_id}")
                conn.execute(
                    """
                    UPDATE corpus_sources
                    SET type = 'object', format = ?, title = ?, path = NULL, local_path = NULL,
                        url = NULL, object_uri = ?, content_hash = ?, size_bytes = ?,
                        content_type = ?, language = ?, doc_type = ?, tags_json = ?,
                        configuration_json = ?, processor_id = ?,
                        processor_config_json = ?, metadata_json = ?,
                        created_at = ?, updated_at = ?, deleted_at = NULL
                    WHERE corpus_id = ? AND source_id = ?
                    """,
                    (
                        normalized_format,
                        title or safe_name,
                        stored.object_uri,
                        stored.content_hash,
                        stored.size_bytes,
                        stored.content_type,
                        language,
                        doc_type,
                        json.dumps(tags or []),
                        json.dumps(configuration or {}),
                        processor_id,
                        json.dumps(processor_config or {}),
                        json.dumps(metadata or {}),
                        now,
                        now,
                        corpus_id,
                        source_id,
                    ),
                )
                self._insert_audit(
                    conn, actor, "corpus_source_restored", {"corpus_id": corpus_id, "source_id": source_id}
                )
        return self.get_corpus_source(corpus_id, source_id)

    def delete_corpus_source(self, corpus_id: str, source_id: str, actor: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM corpora WHERE corpus_id = ?", (corpus_id,)).fetchone()
            if row:
                source_row = conn.execute(
                    "SELECT * FROM corpus_sources WHERE corpus_id = ? AND source_id = ? AND deleted_at IS NULL",
                    (corpus_id, source_id),
                ).fetchone()
                if not source_row:
                    return False
                conn.execute(
                    "UPDATE corpus_sources SET deleted_at = ?, updated_at = ? WHERE corpus_id = ? AND source_id = ?",
                    (iso_now(), iso_now(), corpus_id, source_id),
                )
                self._insert_audit(
                    conn, actor, "corpus_source_deleted", {"corpus_id": corpus_id, "source_id": source_id}
                )
                return True
            return False

    def export_runtime_snapshots(self) -> None:
        providers_path = Path(self.runtime_dir) / "providers.json"
        policies_path = Path(self.runtime_dir) / "policies.json"
        processors_path = Path(self.runtime_dir) / "processors.json"
        retrieval_profiles_path = Path(self.runtime_dir) / "retrieval_profiles.json"
        api_keys_path = Path(self.runtime_dir) / "api_keys.json"
        rag_settings_path = Path(self.runtime_dir) / "rag_settings.json"
        mcp_servers_path = Path(self.runtime_dir) / "mcp_servers.json"
        mcp_settings_path = Path(self.runtime_dir) / "mcp_settings.json"

        providers_payload = [
            {
                "name": provider.name,
                "type": provider.type,
                "base_url": provider.base_url,
                "require_api_key": provider.require_api_key,
                "default_model": provider.default_model,
                "models": provider.models,
                "capabilities": provider.capabilities.model_dump(),
                "client_controls": provider.client_controls.model_dump(exclude_none=True),
                "secret_ref": provider.secret_ref,
                "secret_source_type": provider.secret_source_type,
            }
            for provider in self.list_providers()
        ]
        policies_payload = {
            policy.pipeline_id: {k: v for k, v in policy.model_dump().items() if k != "pipeline_id"}
            for policy in self.list_policies()
        }
        processors_payload = self._processor_payload(self.list_processors())
        retrieval_profiles_payload = self._retrieval_profile_payload(self.list_retrieval_profiles())
        api_keys_payload = self._runtime_api_key_payload()
        rag_payload = self.get_rag_settings().model_dump()
        mcp_settings = self.get_mcp_settings()
        mcp_servers_payload = [
            server.model_dump(exclude_none=True)
            for server in mcp_settings.servers
            if server.name in set(mcp_settings.selected_servers)
        ]
        mcp_settings_payload = {
            "timeout_s": mcp_settings.timeout_s,
            "strict": mcp_settings.strict,
            "max_tool_rounds": mcp_settings.max_tool_rounds,
        }

        providers_path.write_text(json.dumps(providers_payload, indent=2), encoding="utf-8")
        policies_path.write_text(json.dumps(policies_payload, indent=2), encoding="utf-8")
        processors_path.write_text(json.dumps(processors_payload, indent=2), encoding="utf-8")
        retrieval_profiles_path.write_text(json.dumps(retrieval_profiles_payload, indent=2), encoding="utf-8")
        api_keys_path.write_text(json.dumps(api_keys_payload, indent=2), encoding="utf-8")
        rag_settings_path.write_text(json.dumps(rag_payload, indent=2), encoding="utf-8")
        mcp_servers_path.write_text(json.dumps(mcp_servers_payload, indent=2), encoding="utf-8")
        mcp_settings_path.write_text(json.dumps(mcp_settings_payload, indent=2), encoding="utf-8")

    def _runtime_api_key_payload(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT key_id, algorithm, salt_b64, hash_b64, subject, scopes_json, default_pipeline_id,
                       allowed_providers_json, allowed_models_json, max_input_tokens,
                       max_output_tokens, max_total_tokens, max_top_k
                FROM api_keys
                WHERE revoked_at IS NULL AND is_active = 1
                ORDER BY created_at DESC
                """
            ).fetchall()
        payload: List[Dict[str, Any]] = []
        for row in rows:
            payload.append(
                {
                    "key_id": row["key_id"],
                    "key_hash": row["hash_b64"],
                    "key_salt": row["salt_b64"],
                    "key_algorithm": row["algorithm"],
                    "subject": row["subject"],
                    "scopes": json.loads(row["scopes_json"] or "[]"),
                    "default_pipeline_id": row["default_pipeline_id"],
                    "allowed_providers": json.loads(row["allowed_providers_json"])
                    if row["allowed_providers_json"]
                    else None,
                    "allowed_models": json.loads(row["allowed_models_json"]) if row["allowed_models_json"] else None,
                    "max_input_tokens": row["max_input_tokens"],
                    "max_output_tokens": row["max_output_tokens"],
                    "max_total_tokens": row["max_total_tokens"],
                    "max_top_k": row["max_top_k"],
                }
            )
        return payload

    def _api_key_read_from_row(self, row: sqlite3.Row) -> ApiKeyRead:
        return ApiKeyRead(
            key_id=row["key_id"],
            subject=row["subject"],
            scopes=json.loads(row["scopes_json"] or "[]"),
            default_pipeline_id=row["default_pipeline_id"],
            allowed_providers=json.loads(row["allowed_providers_json"]) if row["allowed_providers_json"] else None,
            allowed_models=json.loads(row["allowed_models_json"]) if row["allowed_models_json"] else None,
            max_input_tokens=row["max_input_tokens"],
            max_output_tokens=row["max_output_tokens"],
            max_total_tokens=row["max_total_tokens"],
            max_top_k=row["max_top_k"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
        )

    def _insert_audit(self, conn: sqlite3.Connection, actor: str, event_type: str, detail: Dict[str, Any]) -> None:
        conn.execute(
            "INSERT INTO audit_events(actor, event_type, detail_json, created_at) VALUES (?, ?, ?, ?)",
            (actor, event_type, json.dumps(detail), iso_now()),
        )
