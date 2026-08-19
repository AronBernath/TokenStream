from __future__ import annotations

import os
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from .provider_settings import ProviderDefinition


@dataclass(frozen=True)
class Settings:
    default_provider: Optional[str]

    # RAG services
    retrieval_api_url: str
    default_corpus_id: str
    default_environment: str
    default_tenant_id: str
    default_top_k: int

    providers: Tuple[ProviderDefinition, ...]

    default_temperature: float
    default_max_tokens: int
    enable_server_tools: bool
    llm_timeout_s: float
    llm_max_retries: int
    llm_retry_backoff_s: float
    service_api_key: Optional[str]
    log_text_max_chars: int

    # MCP (remote tool servers)
    mcp_servers_json: str
    mcp_protocol_version: str
    mcp_timeout_s: float
    mcp_strict: bool
    mcp_max_tool_rounds: int


def load_settings() -> Settings:
    from .provider_settings import load_providers

    service_api_key = os.environ.get("ORCHESTRATOR_API_KEY")

    retrieval_api_url = os.environ.get("RETRIEVAL_API_URL", "http://retrieval-api:8000").rstrip("/")
    default_corpus_id = os.environ.get("DEFAULT_CORPUS_ID", "default").strip()
    default_environment = os.environ.get("DEFAULT_ENVIRONMENT", "default-env").strip()
    default_tenant_id = os.environ.get("DEFAULT_TENANT_ID", "default-tenant").strip()
    default_top_k = max(int(os.environ.get("DEFAULT_TOP_K", "8").strip() or "8"), 1)

    rag_settings_path = os.environ.get("RAG_SETTINGS_PATH", "").strip()
    if rag_settings_path:
        try:
            with open(rag_settings_path, "r", encoding="utf-8") as fp:
                rag_settings = json.load(fp)
            if isinstance(rag_settings, dict):
                retrieval_api_url = str(rag_settings.get("retrieval_api_url") or retrieval_api_url).rstrip("/")
                default_corpus_id = str(rag_settings.get("default_corpus_id") or default_corpus_id).strip()
                default_top_k = max(int(rag_settings.get("default_top_k") or default_top_k), 1)
        except FileNotFoundError:
            pass
        except Exception:
            pass

    mcp_servers_json = os.environ.get("MCP_SERVERS", "").strip()
    mcp_servers_path = os.environ.get("MCP_SERVERS_PATH", "").strip()
    if mcp_servers_path:
        try:
            with open(mcp_servers_path, "r", encoding="utf-8") as fp:
                mcp_servers_json = fp.read().strip()
        except FileNotFoundError:
            pass
        except Exception:
            pass

    mcp_protocol_version = os.environ.get("MCP_PROTOCOL_VERSION", "2024-11-05").strip()
    mcp_timeout_s = float(os.environ.get("MCP_TIMEOUT_S", "45").strip() or "45")
    mcp_strict = os.environ.get("MCP_STRICT", "").strip().lower() in {"1", "true", "yes", "y"}
    mcp_max_tool_rounds = int(os.environ.get("MCP_MAX_TOOL_ROUNDS", "6").strip() or "6")
    mcp_settings_path = os.environ.get("MCP_SETTINGS_PATH", "").strip()
    if mcp_settings_path:
        try:
            with open(mcp_settings_path, "r", encoding="utf-8") as fp:
                mcp_settings = json.load(fp)
            if isinstance(mcp_settings, dict):
                mcp_timeout_s = float(mcp_settings.get("timeout_s") or mcp_timeout_s)
                mcp_strict = bool(mcp_settings.get("strict")) if "strict" in mcp_settings else mcp_strict
                mcp_max_tool_rounds = int(mcp_settings.get("max_tool_rounds") or mcp_max_tool_rounds)
        except FileNotFoundError:
            pass
        except Exception:
            pass

    return Settings(
        default_provider=(os.environ.get("LLM_PROVIDER") or "").strip().lower() or None,
        retrieval_api_url=retrieval_api_url,
        default_corpus_id=default_corpus_id,
        default_environment=default_environment,
        default_tenant_id=default_tenant_id,
        default_top_k=default_top_k,
        providers=tuple(load_providers()),
        default_temperature=float(os.environ.get("DEFAULT_TEMPERATURE", "0.1")),
        default_max_tokens=int(os.environ.get("DEFAULT_MAX_TOKENS", "8192")),
        enable_server_tools=os.environ.get("ENABLE_SERVER_TOOLS", "true").strip().lower() in {"1", "true", "yes", "y"},
        llm_timeout_s=max(float(os.environ.get("LLM_TIMEOUT_S", "120").strip() or "120"), 1.0),
        llm_max_retries=max(int(os.environ.get("LLM_MAX_RETRIES", "2").strip() or "2"), 0),
        llm_retry_backoff_s=max(float(os.environ.get("LLM_RETRY_BACKOFF_S", "0.8").strip() or "0.8"), 0.0),
        service_api_key=service_api_key,
        log_text_max_chars=int(os.environ.get("LOG_TEXT_MAX_CHARS", "240")),
        mcp_servers_json=mcp_servers_json,
        mcp_protocol_version=mcp_protocol_version,
        mcp_timeout_s=mcp_timeout_s,
        mcp_strict=mcp_strict,
        mcp_max_tool_rounds=mcp_max_tool_rounds,
    )
