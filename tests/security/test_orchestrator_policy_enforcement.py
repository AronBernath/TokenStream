from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from conftest import evidence_marker


class RecordingProvider:
    def __init__(self, name: str):
        self.name = name
        self.last_model = None
        self.last_temperature = None
        self.last_max_tokens = None
        self.last_context_length = None
        self.last_tools = None
        self.last_tool_choice = None

    async def chat(self, *, messages, params, tools=None, tool_choice=None):
        from common.llm.types import ChatMessage, ChatResponse

        self.last_model = params.model
        self.last_temperature = params.temperature
        self.last_max_tokens = params.max_tokens
        self.last_context_length = params.context_length
        self.last_tools = tools
        self.last_tool_choice = tool_choice
        return ChatResponse(
            message=ChatMessage(role="assistant", content="ok"),
            model=params.model or "missing-model",
            usage={},
            tool_calls=[],
        )


def _provider_def(name: str, models: tuple[str, ...], *, context_length: bool = False):
    from app.provider_settings import ProviderCapabilities, ProviderClientControls, ProviderDefinition

    return ProviderDefinition(
        name=name,
        type="openai_compat",
        base_url=f"http://{name}.test",
        require_api_key=False,
        default_model=models[0],
        models=models,
        capabilities=ProviderCapabilities(tools=True, json_schema=True, max_context_window=8192),
        client_controls=ProviderClientControls(
            temperature=True,
            max_tokens=True,
            context_length=context_length,
            context_length_param="num_ctx" if context_length else None,
        ),
    )


def _configure_runtime(main, monkeypatch):
    from app.auth import AuthRegistry
    from app.pipeline import PipelineRegistry

    openai = RecordingProvider("openai")
    deepseek = RecordingProvider("deepseek")
    provider_defs = (
        _provider_def("openai", ("gpt-policy", "gpt-explicit"), context_length=True),
        _provider_def("deepseek", ("deepseek-v4-pro",), context_length=False),
    )
    monkeypatch.setattr(main, "providers", {"openai": openai, "deepseek": deepseek})
    monkeypatch.setattr(
        main,
        "settings",
        replace(
            main.settings,
            service_api_key="test-token",
            default_provider=None,
            default_top_k=8,
            enable_server_tools=True,
            providers=provider_defs,
        ),
    )
    monkeypatch.setattr(main, "auth_registry", AuthRegistry(entries=[], legacy_key="test-token"))
    monkeypatch.setattr(main, "pipeline_registry", PipelineRegistry.load("default"))
    return openai, deepseek


def _set_pipeline_registry(main, monkeypatch, payload):
    from app.pipeline import PipelineRegistry

    monkeypatch.setenv("ORCHESTRATOR_PIPELINE_REGISTRY_JSON", json.dumps(payload))
    monkeypatch.delenv("ORCHESTRATOR_PIPELINE_REGISTRY_PATH", raising=False)
    monkeypatch.setattr(main, "pipeline_registry", PipelineRegistry.load("legacy_default"))


def _policy_payload():
    return {
        "provider_limited": {
            "default_corpus_id": "risk_corpus",
            "allowed_corpus_ids": ["risk_corpus"],
            "default_filters": {"tenant": "tenant-a"},
            "allowed_tools": [],
            "allowed_providers": ["openai"],
            "allowed_models": ["openai:gpt-policy", "gpt-policy"],
            "default_provider": "openai",
            "default_model": "gpt-policy",
            "max_input_tokens": 4096,
            "max_output_tokens": 256,
            "max_top_k": 3,
        }
    }


@evidence_marker(
    "POLICY-ORCH-001",
    "Client cannot request provider outside allowed_providers",
    "orchestrator-api",
    "403 forbidden_provider before provider call",
    control_ids=["CTRL-IAM-002", "CTRL-POL-001"],
    risk_ids=["ORCH-E-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_provider_outside_policy_is_rejected(orchestrator_main, monkeypatch):
    _, deepseek = _configure_runtime(orchestrator_main, monkeypatch)
    _set_pipeline_registry(orchestrator_main, monkeypatch, _policy_payload())
    client = TestClient(orchestrator_main.app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "deepseek:deepseek-v4-pro",
            "pipeline_id": "provider_limited",
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden_provider"
    assert deepseek.last_model is None


@evidence_marker(
    "POLICY-ORCH-002",
    "Client cannot request model outside allowed_models",
    "orchestrator-api",
    "403 forbidden_model before provider call",
    control_ids=["CTRL-IAM-002", "CTRL-POL-001"],
    risk_ids=["ORCH-E-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_model_outside_policy_is_rejected(orchestrator_main, monkeypatch):
    openai, _ = _configure_runtime(orchestrator_main, monkeypatch)
    _set_pipeline_registry(orchestrator_main, monkeypatch, _policy_payload())
    client = TestClient(orchestrator_main.app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "openai:gpt-explicit",
            "pipeline_id": "provider_limited",
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden_model"
    assert openai.last_model is None


@evidence_marker(
    "POLICY-ORCH-003",
    "Models listing hides disallowed providers and models",
    "orchestrator-api",
    "only allowed models returned",
    control_ids=["CTRL-IAM-002", "CTRL-POL-001"],
    risk_ids=["ORCH-I-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_models_listing_hides_disallowed_models(orchestrator_main, monkeypatch):
    _configure_runtime(orchestrator_main, monkeypatch)
    _set_pipeline_registry(orchestrator_main, monkeypatch, _policy_payload())
    client = TestClient(orchestrator_main.app)

    response = client.get(
        "/v1/models?pipeline_id=provider_limited",
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "id": "openai:gpt-policy",
            "object": "model",
            "created": response.json()["data"][0]["created"],
            "owned_by": "openai",
        }
    ]


@evidence_marker(
    "POLICY-ORCH-004",
    "Unknown pipeline_id is rejected",
    "orchestrator-api",
    "422 invalid_pipeline",
    control_ids=["CTRL-POL-001"],
    risk_ids=["ORCH-E-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_unknown_pipeline_is_rejected(orchestrator_main, monkeypatch):
    _configure_runtime(orchestrator_main, monkeypatch)
    _set_pipeline_registry(orchestrator_main, monkeypatch, _policy_payload())
    client = TestClient(orchestrator_main.app)

    response = client.post(
        "/v1/rag/query",
        json={"query": "risk", "pipeline_id": "missing", "corpus_id": "risk_corpus"},
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_pipeline"


@evidence_marker(
    "POLICY-ORCH-005",
    "Corpus outside pipeline allowlist is rejected",
    "orchestrator-api",
    "422 invalid_pipeline before retrieval call",
    control_ids=["CTRL-POL-001"],
    risk_ids=["ORCH-E-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_corpus_outside_pipeline_is_rejected(orchestrator_main, monkeypatch):
    _configure_runtime(orchestrator_main, monkeypatch)
    _set_pipeline_registry(orchestrator_main, monkeypatch, _policy_payload())
    called = False

    async def fake_retrieval_call(**kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(orchestrator_main, "_call_retrieval_api", fake_retrieval_call)
    client = TestClient(orchestrator_main.app)

    response = client.post(
        "/v1/rag/query",
        json={"query": "risk", "pipeline_id": "provider_limited", "corpus_id": "other_corpus"},
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_pipeline"
    assert called is False


@evidence_marker(
    "POLICY-ORCH-006",
    "RAG query top_k is clamped by policy max_top_k",
    "orchestrator-api",
    "retrieval call receives clamped top_k",
    control_ids=["CTRL-POL-001", "CTRL-RES-001"],
    risk_ids=["ORCH-D-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_rag_query_top_k_is_clamped(orchestrator_main, monkeypatch):
    from common.models import QueryResponse

    _configure_runtime(orchestrator_main, monkeypatch)
    _set_pipeline_registry(orchestrator_main, monkeypatch, _policy_payload())
    captured = {}

    async def fake_retrieval_call(*, req, headers=None, **kwargs):
        captured["req"] = req
        captured["headers"] = headers
        return QueryResponse(answer="ok", citations=[], chunks=[])

    monkeypatch.setattr(orchestrator_main, "_call_retrieval_api", fake_retrieval_call)
    client = TestClient(orchestrator_main.app)

    response = client.post(
        "/v1/rag/query",
        json={"query": "risk", "pipeline_id": "provider_limited", "top_k": 100},
        headers={"authorization": "Bearer test-token", "x-request-id": "trace-123"},
    )

    assert response.status_code == 200
    assert captured["req"].top_k == 3
    assert captured["headers"]["authorization"] == "Bearer test-token"
    assert captured["headers"]["x-request-id"] == "trace-123"


@evidence_marker(
    "POLICY-ORCH-007",
    "RAG lookup top_k and max_results are clamped by policy max_top_k",
    "orchestrator-api",
    "lookup call receives clamped top_k and max_results",
    control_ids=["CTRL-POL-001", "CTRL-RES-001"],
    risk_ids=["ORCH-D-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_rag_lookup_limits_are_clamped(orchestrator_main, monkeypatch):
    from common.models import QueryResponse

    _configure_runtime(orchestrator_main, monkeypatch)
    _set_pipeline_registry(orchestrator_main, monkeypatch, _policy_payload())
    captured = {}

    async def fake_lookup_call(*, req, headers=None, **kwargs):
        captured["req"] = req
        captured["headers"] = headers
        return QueryResponse(answer="ok", citations=[], chunks=[])

    monkeypatch.setattr(orchestrator_main, "_call_retrieval_lookup_api", fake_lookup_call)
    client = TestClient(orchestrator_main.app)

    response = client.post(
        "/v1/rag/lookup",
        json={
            "terms": ["risk"],
            "pipeline_id": "provider_limited",
            "top_k": 50,
            "max_results": 80,
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert captured["req"].top_k == 3
    assert captured["req"].max_results == 3


@pytest.mark.xfail(reason="Current pipeline filter merge lets requested filters override default filter keys")
@evidence_marker(
    "POLICY-ORCH-008",
    "Client filter cannot override policy default filter",
    "orchestrator-api",
    "policy-protected default filter is preserved",
    control_ids=["CTRL-POL-001"],
    risk_ids=["ORCH-I-02"],
    cra_requirements=["Annex I Part I 2(d)"],
    notes="Known gap: requested filters currently override matching default filter keys.",
)
def test_client_filter_cannot_override_policy_filter(orchestrator_main, monkeypatch):
    from common.models import QueryResponse

    _configure_runtime(orchestrator_main, monkeypatch)
    _set_pipeline_registry(orchestrator_main, monkeypatch, _policy_payload())
    captured = {}

    async def fake_retrieval_call(*, req, **kwargs):
        captured["req"] = req
        return QueryResponse(answer="ok", citations=[], chunks=[])

    monkeypatch.setattr(orchestrator_main, "_call_retrieval_api", fake_retrieval_call)
    client = TestClient(orchestrator_main.app)

    response = client.post(
        "/v1/rag/query",
        json={
            "query": "risk",
            "pipeline_id": "provider_limited",
            "filters": {"tenant": "tenant-b"},
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert captured["req"].filters["tenant"] == "tenant-a"


@evidence_marker(
    "POLICY-ORCH-009",
    "Allowed client generation controls are forwarded",
    "orchestrator-api",
    "temperature, max_tokens, and context_length forwarded",
    control_ids=["CTRL-POL-001"],
    risk_ids=["ORCH-E-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_allowed_generation_controls_are_forwarded(orchestrator_main, monkeypatch):
    openai, _ = _configure_runtime(orchestrator_main, monkeypatch)
    _set_pipeline_registry(orchestrator_main, monkeypatch, _policy_payload())
    client = TestClient(orchestrator_main.app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "openai:gpt-policy",
            "pipeline_id": "provider_limited",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.2,
            "max_tokens": 128,
            "context_length": 2048,
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert openai.last_temperature == 0.2
    assert openai.last_max_tokens == 128
    assert openai.last_context_length == 2048


@evidence_marker(
    "POLICY-ORCH-010",
    "Disabled context_length provider control is not forwarded",
    "orchestrator-api",
    "context_length stays unset",
    control_ids=["CTRL-POL-001"],
    risk_ids=["ORCH-E-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_disabled_context_length_control_is_not_forwarded(orchestrator_main, monkeypatch):
    _, deepseek = _configure_runtime(orchestrator_main, monkeypatch)
    _set_pipeline_registry(
        orchestrator_main,
        monkeypatch,
        {
            "deepseek_policy": {
                "allowed_corpus_ids": [],
                "allowed_tools": [],
                "allowed_providers": ["deepseek"],
                "allowed_models": ["deepseek:deepseek-v4-pro", "deepseek-v4-pro"],
                "default_provider": "deepseek",
                "default_model": "deepseek-v4-pro",
            }
        },
    )
    client = TestClient(orchestrator_main.app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "deepseek:deepseek-v4-pro",
            "pipeline_id": "deepseek_policy",
            "messages": [{"role": "user", "content": "hello"}],
            "context_length": 2048,
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert deepseek.last_context_length is None


@evidence_marker(
    "TOOL-ORCH-001",
    "Provider supports tools but server tools are globally disabled",
    "orchestrator-api",
    "no server tools advertised",
    control_ids=["CTRL-POL-001", "CTRL-TOOL-001"],
    risk_ids=["ORCH-E-02"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_server_tools_disabled_hides_tools(orchestrator_main, monkeypatch):
    openai, _ = _configure_runtime(orchestrator_main, monkeypatch)
    monkeypatch.setattr(orchestrator_main, "settings", replace(orchestrator_main.settings, enable_server_tools=False))
    _set_pipeline_registry(orchestrator_main, monkeypatch, _policy_payload())
    client = TestClient(orchestrator_main.app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "openai:gpt-policy",
            "pipeline_id": "provider_limited",
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert openai.last_tools == []


@evidence_marker(
    "TRACE-ORCH-001",
    "Client request ID is forwarded to retrieval API",
    "orchestrator-api",
    "retrieval receives same x-request-id",
    control_ids=["CTRL-LOG-001"],
    risk_ids=["ORCH-R-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_request_id_is_forwarded_to_retrieval(orchestrator_main, monkeypatch):
    from common.models import QueryResponse

    _configure_runtime(orchestrator_main, monkeypatch)
    _set_pipeline_registry(orchestrator_main, monkeypatch, _policy_payload())
    captured = {}

    async def fake_retrieval_call(*, req, headers=None, **kwargs):
        captured["headers"] = headers
        return QueryResponse(answer="ok", citations=[], chunks=[])

    monkeypatch.setattr(orchestrator_main, "_call_retrieval_api", fake_retrieval_call)
    client = TestClient(orchestrator_main.app)

    response = client.post(
        "/v1/rag/query",
        json={"query": "risk", "pipeline_id": "provider_limited"},
        headers={"authorization": "Bearer test-token", "x-request-id": "trace-abc"},
    )

    assert response.status_code == 200
    assert captured["headers"]["x-request-id"] == "trace-abc"
    assert captured["headers"]["x-correlation-id"] == "trace-abc"


@evidence_marker(
    "TRACE-ORCH-002",
    "Orchestrator creates a request ID when client omits it",
    "orchestrator-api",
    "generated request ID is forwarded",
    control_ids=["CTRL-LOG-001"],
    risk_ids=["ORCH-R-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_request_id_is_generated_when_missing(orchestrator_main, monkeypatch):
    from common.models import QueryResponse

    _configure_runtime(orchestrator_main, monkeypatch)
    _set_pipeline_registry(orchestrator_main, monkeypatch, _policy_payload())
    captured = {}

    async def fake_retrieval_call(*, req, headers=None, **kwargs):
        captured["headers"] = headers
        return QueryResponse(answer="ok", citations=[], chunks=[])

    monkeypatch.setattr(orchestrator_main, "_call_retrieval_api", fake_retrieval_call)
    client = TestClient(orchestrator_main.app)

    response = client.post(
        "/v1/rag/query",
        json={"query": "risk", "pipeline_id": "provider_limited"},
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert captured["headers"]["x-request-id"]
    assert captured["headers"]["x-request-id"] == captured["headers"]["x-correlation-id"]


@evidence_marker(
    "TRACE-ORCH-003",
    "Authorization header is forwarded to retrieval API",
    "orchestrator-api",
    "retrieval receives original Authorization header",
    control_ids=["CTRL-IAM-001", "CTRL-LOG-001"],
    risk_ids=["ORCH-R-01"],
    cra_requirements=["Annex I Part I 2(d)"],
)
def test_authorization_header_is_forwarded_to_retrieval(orchestrator_main, monkeypatch):
    from common.models import QueryResponse

    _configure_runtime(orchestrator_main, monkeypatch)
    _set_pipeline_registry(orchestrator_main, monkeypatch, _policy_payload())
    captured = {}

    async def fake_retrieval_call(*, req, headers=None, **kwargs):
        captured["headers"] = headers
        return QueryResponse(answer="ok", citations=[], chunks=[])

    monkeypatch.setattr(orchestrator_main, "_call_retrieval_api", fake_retrieval_call)
    client = TestClient(orchestrator_main.app)

    response = client.post(
        "/v1/rag/query",
        json={"query": "risk", "pipeline_id": "provider_limited"},
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert captured["headers"]["authorization"] == "Bearer test-token"


@evidence_marker(
    "SNAPSHOT-002",
    "Internal reload fails closed when reload token is absent",
    "orchestrator-api",
    "503 reload_not_configured",
    control_ids=["CTRL-IAM-001"],
    risk_ids=["ORCH-S-01"],
    cra_requirements=["Annex I Part I 1"],
)
def test_internal_reload_requires_configured_token(orchestrator_main, monkeypatch):
    monkeypatch.setattr(orchestrator_main, "reload_token", "")
    client = TestClient(orchestrator_main.app)

    response = client.post("/v1/internal/reload")

    assert response.status_code == 503
    assert response.json()["error"] == "reload_not_configured"


@evidence_marker(
    "SNAPSHOT-003",
    "Internal reload accepts valid reload token",
    "orchestrator-api",
    "200 reload accepted",
    control_ids=["CTRL-IAM-001"],
    risk_ids=["ORCH-S-01"],
    cra_requirements=["Annex I Part I 1"],
)
def test_internal_reload_accepts_valid_token(orchestrator_main, monkeypatch):
    monkeypatch.setattr(orchestrator_main, "reload_token", "reload-token")
    monkeypatch.setattr(orchestrator_main, "reload_runtime_state", lambda: None)
    client = TestClient(orchestrator_main.app)

    response = client.post("/v1/internal/reload", headers={"x-config-auth-token": "reload-token"})

    assert response.status_code == 200
    assert response.json()["ok"] is True
