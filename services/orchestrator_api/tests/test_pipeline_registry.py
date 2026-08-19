from dataclasses import replace
import json

from fastapi.testclient import TestClient
import pytest
from unittest.mock import AsyncMock

from app import main
from app.auth import AuthRegistry
from app.pipeline import PipelineRegistry, is_tool_allowed
from common.models import QueryResponse


def _build_registry(monkeypatch) -> PipelineRegistry:
    payload = {
        "risk_pipeline": {
            "default_corpus_id": "risk_corpus",
            "allowed_corpus_ids": ["risk_corpus", "risk_corpus_archive"],
            "default_filters": {"doc_type": "act"},
            "allowed_tools": ["rag", "mcp__analysis"],
        }
    }
    monkeypatch.setenv("ORCHESTRATOR_PIPELINE_REGISTRY_JSON", json.dumps(payload))
    monkeypatch.delenv("ORCHESTRATOR_PIPELINE_REGISTRY_PATH", raising=False)
    return PipelineRegistry.load("default")


def test_pipeline_resolve_merges_default_and_requested_filters(monkeypatch):
    registry = _build_registry(monkeypatch)
    ctx = registry.resolve(
        pipeline_id="risk_pipeline",
        requested_corpus_id=None,
        requested_filters={"region": "EU"},
    )

    assert ctx.resolved_corpus_id == "risk_corpus"
    assert ctx.effective_filters["doc_type"] == "act"
    assert ctx.effective_filters["region"] == "EU"
    assert ctx.allowed_tools == ("rag", "mcp__analysis")


def test_pipeline_resolve_includes_chunking_policy(monkeypatch):
    payload = {
        "ingestion": {
            "default_corpus_id": "risk_corpus",
            "allowed_corpus_ids": ["risk_corpus"],
            "chunking": {
                "enabled": True,
                "default_provider": "deepseek",
                "default_model": "deepseek-v4-pro",
                "allowed_providers": ["deepseek"],
                "allowed_models": ["deepseek:deepseek-v4-pro"],
                "target_chars": 2200,
                "window_chars": 24000,
                "window_overlap_chars": 1500,
                "max_retries": 2,
            },
        }
    }
    monkeypatch.setenv("ORCHESTRATOR_PIPELINE_REGISTRY_JSON", json.dumps(payload))
    registry = PipelineRegistry.load("default")

    ctx = registry.resolve(pipeline_id="ingestion", requested_corpus_id=None, requested_filters=None)

    assert ctx.chunking.enabled is True
    assert ctx.chunking.default_provider == "deepseek"
    assert ctx.chunking.default_model == "deepseek-v4-pro"
    assert ctx.chunking.allowed_providers == ("deepseek",)
    assert ctx.chunking.allowed_models == ("deepseek:deepseek-v4-pro",)
    assert ctx.chunking.window_chars == 24000


def test_pipeline_resolve_allows_no_corpus_policy(monkeypatch):
    payload = {
        "writer": {
            "default_corpus_id": None,
            "allowed_corpus_ids": [],
            "default_filters": {},
            "allowed_tools": [],
            "allowed_providers": ["openai", "deepseek"],
            "chunking": {"enabled": False},
        }
    }
    monkeypatch.setenv("ORCHESTRATOR_PIPELINE_REGISTRY_JSON", json.dumps(payload))

    registry = PipelineRegistry.load("default")
    ctx = registry.resolve(pipeline_id="writer", requested_corpus_id=None, requested_filters=None)

    assert ctx.resolved_corpus_id is None
    assert ctx.allowed_corpus_ids == ()
    assert ctx.allowed_tools == ()
    assert ctx.allowed_providers == ("openai", "deepseek")
    with pytest.raises(ValueError, match="does not allow corpus access"):
        ctx.enforce_corpus(None)


def test_pipeline_resolve_enforces_allowed_corpus_and_unknown_pipeline(monkeypatch):
    registry = _build_registry(monkeypatch)
    with pytest.raises(ValueError, match="corpus_id 'other' is not allowed"):
        registry.resolve(pipeline_id="risk_pipeline", requested_corpus_id="other", requested_filters=None)

    with pytest.raises(ValueError, match="Unknown pipeline_id"):
        registry.resolve(pipeline_id="missing_pipeline", requested_corpus_id=None, requested_filters=None)


def test_no_pipeline_uses_default_corpus(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_PIPELINE_REGISTRY_JSON", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_PIPELINE_REGISTRY_PATH", raising=False)
    registry = PipelineRegistry.load("legacy_default")
    ctx = registry.resolve(pipeline_id=None, requested_corpus_id=None, requested_filters=None)

    assert ctx.pipeline_id is None
    assert ctx.resolved_corpus_id == "legacy_default"


def test_missing_pipeline_snapshot_starts_with_empty_registry(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCHESTRATOR_PIPELINE_REGISTRY_JSON", raising=False)
    monkeypatch.setenv("ORCHESTRATOR_PIPELINE_REGISTRY_PATH", str(tmp_path / "policies.json"))

    registry = PipelineRegistry.load("legacy_default")
    ctx = registry.resolve(pipeline_id=None, requested_corpus_id=None, requested_filters=None)

    assert registry.policies == ()
    assert ctx.resolved_corpus_id == "legacy_default"


def test_is_tool_allowed_gates_namespace_and_rag():
    assert is_tool_allowed("rag__query", None)
    assert not is_tool_allowed("rag__query", [])
    assert is_tool_allowed("rag__query", ["rag"])
    assert is_tool_allowed("rag.query", ["rag"])
    assert is_tool_allowed("mcp__analysis__search", ["mcp__analysis"])
    assert is_tool_allowed("mcp.analysis.search", ["mcp__analysis"])
    assert not is_tool_allowed("analysis__delete", ["mcp__analysis__search"])


def test_rag_query_applies_pipeline_defaults_and_filters(monkeypatch):
    registry = _build_registry(monkeypatch)
    monkeypatch.setattr(main, "pipeline_registry", registry)
    monkeypatch.setattr(main, "settings", replace(main.settings, service_api_key="test-token"))
    monkeypatch.setattr(main, "auth_registry", AuthRegistry(entries=[], legacy_key="test-token"))

    captured = []

    async def fake_retrieval_call(base_url: str, req, timeout_s: float = 20.0, headers=None):
        captured.append(req.model_dump())
        return QueryResponse(answer="ok", citations=[], chunks=[])

    monkeypatch.setattr(main, "_call_retrieval_api", fake_retrieval_call)

    client = TestClient(main.app)
    response = client.post(
        "/v1/rag/query",
        json={
            "query": "risk baseline",
            "pipeline_id": "risk_pipeline",
            "filters": {"region": "EU"},
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert captured
    req = captured[0]
    assert req["corpus_id"] == "risk_corpus"
    assert req["filters"]["doc_type"] == "act"
    assert req["filters"]["region"] == "EU"


def test_rag_lookup_applies_pipeline_defaults_and_filters(monkeypatch):
    registry = _build_registry(monkeypatch)
    monkeypatch.setattr(main, "pipeline_registry", registry)
    monkeypatch.setattr(main, "settings", replace(main.settings, service_api_key="test-token"))
    monkeypatch.setattr(main, "auth_registry", AuthRegistry(entries=[], legacy_key="test-token"))

    captured = []

    async def fake_lookup_call(base_url: str, req, timeout_s: float = 20.0, headers=None):
        captured.append(req.model_dump())
        return QueryResponse(answer="ok", citations=[], chunks=[])

    monkeypatch.setattr(main, "_call_retrieval_lookup_api", fake_lookup_call)

    client = TestClient(main.app)
    response = client.post(
        "/v1/rag/lookup",
        json={
            "terms": ["/v1/query", "IngestionJob"],
            "pipeline_id": "risk_pipeline",
            "filters": {"region": "EU"},
            "top_k": 5,
            "max_results": 12,
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert captured
    req = captured[0]
    assert req["terms"] == ["/v1/query", "IngestionJob"]
    assert req["corpus_id"] == "risk_corpus"
    assert req["filters"]["doc_type"] == "act"
    assert req["filters"]["region"] == "EU"
    assert req["top_k"] == 5
    assert req["max_results"] == 12


def test_rag_query_rejects_disallowed_pipeline_corpus(monkeypatch):
    registry = _build_registry(monkeypatch)
    monkeypatch.setattr(main, "pipeline_registry", registry)
    monkeypatch.setattr(main, "settings", replace(main.settings, service_api_key="test-token"))
    monkeypatch.setattr(main, "auth_registry", AuthRegistry(entries=[], legacy_key="test-token"))
    fake_retrieval_call = AsyncMock()
    monkeypatch.setattr(main, "_call_retrieval_api", fake_retrieval_call)

    client = TestClient(main.app)
    response = client.post(
        "/v1/rag/query",
        json={
            "query": "risk baseline",
            "pipeline_id": "risk_pipeline",
            "corpus_id": "other_corpus",
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 422
