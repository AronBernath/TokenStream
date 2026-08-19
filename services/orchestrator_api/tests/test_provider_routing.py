from dataclasses import replace
import json

from fastapi.testclient import TestClient

from app import main
from app.auth import AuthRegistry
from app.pipeline import PipelineRegistry
from app.provider_settings import ProviderCapabilities, ProviderClientControls, ProviderDefinition
from common.llm.types import ChatMessage, ChatResponse, ToolCall


class DummyProvider:
    def __init__(self, name: str):
        self.name = name
        self.default_model = ""
        self.last_model = None
        self.last_temperature = None
        self.last_max_tokens = None
        self.last_context_length = None
        self.last_response_format = None
        self.last_tools = None
        self.last_tool_choice = None
        self.content = "ok"
        self.tool_calls = []

    async def chat(self, *, messages, params, tools=None, tool_choice=None):
        self.last_model = params.model
        self.last_temperature = params.temperature
        self.last_max_tokens = params.max_tokens
        self.last_context_length = params.context_length
        self.last_response_format = params.response_format
        self.last_tools = tools
        self.last_tool_choice = tool_choice
        return ChatResponse(
            message=ChatMessage(role="assistant", content=self.content),
            model=params.model or "missing-model",
            usage={},
            tool_calls=list(self.tool_calls),
        )


def _provider_defs(*, shared_model: bool = False):
    openai_models = ("shared-model",) if shared_model else ("gpt-policy", "gpt-explicit")
    deepseek_models = ("shared-model",) if shared_model else ("deepseek-v4-pro",)
    return (
        ProviderDefinition(
            name="openai",
            type="openai_compat",
            base_url="http://openai.test",
            require_api_key=False,
            default_model="",
            models=openai_models,
            capabilities=ProviderCapabilities(),
            client_controls=ProviderClientControls(temperature=True, max_tokens=True, context_length=True),
        ),
        ProviderDefinition(
            name="deepseek",
            type="openai_compat",
            base_url="http://deepseek.test",
            require_api_key=False,
            default_model="",
            models=deepseek_models,
            capabilities=ProviderCapabilities(),
            client_controls=ProviderClientControls(temperature=False, max_tokens=False, context_length=False),
        ),
    )


def _provider_defs_with_openai_tools_enabled(enabled: bool):
    openai_capabilities = ProviderCapabilities(
        tools=enabled,
        json_schema=False,
        streaming=True,
        max_context_window=8192,
        default_context_window=8192,
    )
    return (
        ProviderDefinition(
            name="openai",
            type="openai_compat",
            base_url="http://openai.test",
            require_api_key=False,
            default_model="",
            models=("gpt-explicit",),
            capabilities=openai_capabilities,
            client_controls=ProviderClientControls(temperature=True, max_tokens=True),
        ),
    )


def _configure_runtime(monkeypatch, *, shared_model: bool = False):
    openai = DummyProvider("openai")
    deepseek = DummyProvider("deepseek")
    monkeypatch.setattr(main, "providers", {"openai": openai, "deepseek": deepseek})
    monkeypatch.setattr(
        main,
        "settings",
        replace(
            main.settings,
            service_api_key="test-token",
            default_provider=None,
            providers=_provider_defs(shared_model=shared_model),
        ),
    )
    monkeypatch.setattr(main, "auth_registry", AuthRegistry(entries=[], legacy_key="test-token"))
    return openai, deepseek


def _enable_deepseek_chunking(monkeypatch):
    providers = list(main.settings.providers)
    updated = []
    for provider in providers:
        if provider.name == "deepseek":
            updated.append(
                replace(
                    provider,
                    capabilities=ProviderCapabilities(
                        tools=provider.capabilities.tools,
                        json_schema=provider.capabilities.json_schema,
                        streaming=provider.capabilities.streaming,
                        chunking=True,
                        max_context_window=provider.capabilities.max_context_window,
                        default_context_window=provider.capabilities.default_context_window,
                    ),
                )
            )
        else:
            updated.append(provider)
    monkeypatch.setattr(main, "settings", replace(main.settings, providers=tuple(updated)))


def _configure_openai_only_runtime(monkeypatch, *, tools_enabled: bool):
    openai = DummyProvider("openai")
    monkeypatch.setattr(main, "providers", {"openai": openai})
    monkeypatch.setattr(
        main,
        "settings",
        replace(
            main.settings,
            service_api_key="test-token",
            default_provider=None,
            providers=_provider_defs_with_openai_tools_enabled(tools_enabled),
            enable_server_tools=True,
        ),
    )
    monkeypatch.setattr(main, "auth_registry", AuthRegistry(entries=[], legacy_key="test-token"))
    monkeypatch.setattr(
        main, "pipeline_registry", PipelineRegistry(policies=(), default_corpus_id="legacy_default", default_filters={})
    )
    return openai


def _set_pipeline_registry(monkeypatch, payload):
    monkeypatch.setenv("ORCHESTRATOR_PIPELINE_REGISTRY_JSON", json.dumps(payload))
    monkeypatch.delenv("ORCHESTRATOR_PIPELINE_REGISTRY_PATH", raising=False)
    monkeypatch.setattr(main, "pipeline_registry", PipelineRegistry.load("legacy_default"))


def test_chat_completions_fail_without_policy_or_explicit_model(monkeypatch):
    _configure_runtime(monkeypatch)
    monkeypatch.setattr(
        main, "pipeline_registry", PipelineRegistry(policies=(), default_corpus_id="legacy_default", default_filters={})
    )

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "", "messages": [{"role": "user", "content": "hello"}]},
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "provider_resolution_failed"


def test_chat_completions_use_policy_default_provider_and_model(monkeypatch):
    openai, _ = _configure_runtime(monkeypatch)
    _set_pipeline_registry(
        monkeypatch,
        {
            "policy_default": {
                "default_corpus_id": "risk",
                "allowed_corpus_ids": ["risk"],
                "default_filters": {},
                "allowed_tools": [],
                "allowed_providers": ["openai"],
                "allowed_models": ["openai:gpt-policy", "gpt-policy"],
                "default_provider": "openai",
                "default_model": "gpt-policy",
            }
        },
    )

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "", "pipeline_id": "policy_default", "messages": [{"role": "user", "content": "hello"}]},
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert openai.last_model == "gpt-policy"


def test_chat_completions_allow_explicit_request_within_policy(monkeypatch):
    openai, _ = _configure_runtime(monkeypatch)
    _set_pipeline_registry(
        monkeypatch,
        {
            "policy_default": {
                "default_corpus_id": "risk",
                "allowed_corpus_ids": ["risk"],
                "default_filters": {},
                "allowed_tools": [],
                "allowed_providers": ["openai"],
                "allowed_models": ["openai:gpt-policy", "gpt-policy", "openai:gpt-explicit", "gpt-explicit"],
                "default_provider": "openai",
                "default_model": "gpt-policy",
            }
        },
    )

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "openai:gpt-explicit",
            "pipeline_id": "policy_default",
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert openai.last_model == "gpt-explicit"


def test_chat_completions_reject_provider_outside_policy(monkeypatch):
    _, _ = _configure_runtime(monkeypatch)
    _set_pipeline_registry(
        monkeypatch,
        {
            "policy_default": {
                "default_corpus_id": "risk",
                "allowed_corpus_ids": ["risk"],
                "default_filters": {},
                "allowed_tools": [],
                "allowed_providers": ["openai"],
                "allowed_models": ["openai:gpt-policy", "gpt-policy"],
                "default_provider": "openai",
                "default_model": "gpt-policy",
            }
        },
    )

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "deepseek:deepseek-v4-pro",
            "pipeline_id": "policy_default",
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden_provider"


def test_chunking_task_uses_chunking_policy_defaults(monkeypatch):
    _, deepseek = _configure_runtime(monkeypatch)
    _enable_deepseek_chunking(monkeypatch)
    _set_pipeline_registry(
        monkeypatch,
        {
            "policy_default": {
                "default_corpus_id": "risk",
                "allowed_corpus_ids": ["risk"],
                "default_filters": {},
                "allowed_tools": [],
                "allowed_providers": ["openai"],
                "allowed_models": ["openai:gpt-policy", "gpt-policy"],
                "default_provider": "openai",
                "default_model": "gpt-policy",
                "chunking": {
                    "enabled": True,
                    "default_provider": "deepseek",
                    "default_model": "deepseek-v4-pro",
                    "allowed_providers": ["deepseek"],
                    "allowed_models": ["deepseek:deepseek-v4-pro"],
                },
            }
        },
    )

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "",
            "pipeline_id": "policy_default",
            "task": "chunking",
            "messages": [{"role": "user", "content": "chunk this"}],
            "tool_choice": "none",
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert deepseek.last_model == "deepseek-v4-pro"


def test_chunking_task_without_pipeline_uses_default_policy(monkeypatch):
    _, deepseek = _configure_runtime(monkeypatch)
    _enable_deepseek_chunking(monkeypatch)
    _set_pipeline_registry(
        monkeypatch,
        {
            "default": {
                "default_corpus_id": "risk",
                "allowed_corpus_ids": ["risk"],
                "default_filters": {},
                "allowed_tools": [],
                "chunking": {
                    "enabled": True,
                    "default_provider": "deepseek",
                    "default_model": "deepseek-v4-pro",
                    "allowed_providers": ["deepseek"],
                    "allowed_models": ["deepseek:deepseek-v4-pro"],
                },
            }
        },
    )

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "",
            "task": "chunking",
            "messages": [{"role": "user", "content": "chunk this"}],
            "tool_choice": "none",
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert deepseek.last_model == "deepseek-v4-pro"


def test_chunking_task_response_format_is_not_proxy_validated(monkeypatch):
    _, deepseek = _configure_runtime(monkeypatch)
    deepseek.content = 'Here is the JSON:\n{"chunks":[{"start":0,"end":4}]}'
    _enable_deepseek_chunking(monkeypatch)
    providers = list(main.settings.providers)
    monkeypatch.setattr(
        main,
        "settings",
        replace(
            main.settings,
            providers=tuple(
                replace(provider, capabilities=replace(provider.capabilities, json_schema=True))
                if provider.name == "deepseek"
                else provider
                for provider in providers
            ),
        ),
    )
    _set_pipeline_registry(
        monkeypatch,
        {
            "default": {
                "default_corpus_id": "risk",
                "allowed_corpus_ids": ["risk"],
                "default_filters": {},
                "allowed_tools": [],
                "chunking": {
                    "enabled": True,
                    "default_provider": "deepseek",
                    "default_model": "deepseek-v4-pro",
                    "allowed_providers": ["deepseek"],
                    "allowed_models": ["deepseek:deepseek-v4-pro"],
                },
            }
        },
    )

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "",
            "task": "chunking",
            "messages": [{"role": "user", "content": "chunk this"}],
            "tool_choice": "none",
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "chunk_offsets",
                    "schema": {
                        "type": "object",
                        "properties": {"chunks": {"type": "array"}},
                        "required": ["chunks"],
                    },
                },
            },
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert deepseek.last_response_format["type"] == "json_schema"
    assert response.json()["choices"][0]["message"]["content"].startswith("Here is the JSON:")


def test_chunking_task_passes_client_tool_and_returns_arguments(monkeypatch):
    _, deepseek = _configure_runtime(monkeypatch)
    deepseek.content = ""
    deepseek.tool_calls = [
        ToolCall(
            id="call_1",
            name="emit_chunk_offsets",
            arguments={"chunks": [{"start": 0, "end": 4}]},
        )
    ]
    _enable_deepseek_chunking(monkeypatch)
    providers = list(main.settings.providers)
    monkeypatch.setattr(
        main,
        "settings",
        replace(
            main.settings,
            providers=tuple(
                replace(provider, capabilities=replace(provider.capabilities, tools=True))
                if provider.name == "deepseek"
                else provider
                for provider in providers
            ),
        ),
    )
    _set_pipeline_registry(
        monkeypatch,
        {
            "default": {
                "default_corpus_id": "risk",
                "allowed_corpus_ids": ["risk"],
                "default_filters": {},
                "allowed_tools": [],
                "chunking": {
                    "enabled": True,
                    "default_provider": "deepseek",
                    "default_model": "deepseek-v4-pro",
                    "allowed_providers": ["deepseek"],
                    "allowed_models": ["deepseek:deepseek-v4-pro"],
                },
            }
        },
    )

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "",
            "task": "chunking",
            "messages": [{"role": "user", "content": "chunk this"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "emit_chunk_offsets",
                        "description": "Return document chunk boundaries.",
                        "parameters": {
                            "type": "object",
                            "properties": {"chunks": {"type": "array"}},
                            "required": ["chunks"],
                        },
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "emit_chunk_offsets"}},
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert deepseek.last_tools[0].name == "emit_chunk_offsets"
    assert deepseek.last_tool_choice.name == "emit_chunk_offsets"
    content = response.json()["choices"][0]["message"]["content"]
    assert json.loads(content) == {"chunks": [{"start": 0, "end": 4}]}


def test_chunking_task_rejects_provider_without_chunking_capability(monkeypatch):
    _configure_runtime(monkeypatch)
    _set_pipeline_registry(
        monkeypatch,
        {
            "policy_default": {
                "default_corpus_id": "risk",
                "allowed_corpus_ids": ["risk"],
                "default_filters": {},
                "allowed_tools": [],
                "chunking": {
                    "enabled": True,
                    "default_provider": "deepseek",
                    "default_model": "deepseek-v4-pro",
                    "allowed_providers": ["deepseek"],
                    "allowed_models": ["deepseek:deepseek-v4-pro"],
                },
            }
        },
    )

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "",
            "pipeline_id": "policy_default",
            "task": "chunking",
            "messages": [{"role": "user", "content": "chunk this"}],
            "tool_choice": "none",
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "provider_chunking_disabled"


def test_chat_completions_reject_ambiguous_model_only_selection(monkeypatch):
    _configure_runtime(monkeypatch, shared_model=True)
    monkeypatch.setattr(
        main, "pipeline_registry", PipelineRegistry(policies=(), default_corpus_id="legacy_default", default_filters={})
    )

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "shared-model", "messages": [{"role": "user", "content": "hello"}]},
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ambiguous_provider"


def test_models_endpoint_returns_empty_list_without_fallback(monkeypatch):
    monkeypatch.setattr(main, "providers", {})
    monkeypatch.setattr(
        main,
        "settings",
        replace(main.settings, service_api_key="test-token", default_provider=None, providers=()),
    )
    monkeypatch.setattr(main, "auth_registry", AuthRegistry(entries=[], legacy_key="test-token"))

    client = TestClient(main.app)
    response = client.get("/v1/models", headers={"authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json()["data"] == []


def test_models_endpoint_filters_by_requested_pipeline_policy(monkeypatch):
    _configure_runtime(monkeypatch)
    _set_pipeline_registry(
        monkeypatch,
        {
            "writer": {
                "default_corpus_id": "docs",
                "allowed_corpus_ids": ["docs"],
                "default_filters": {},
                "allowed_tools": ["rag"],
                "allowed_providers": ["openai", "deepseek"],
                "allowed_models": ["openai:gpt-policy", "deepseek-v4-pro"],
                "default_provider": "openai",
                "default_model": "gpt-policy",
            }
        },
    )

    client = TestClient(main.app)
    response = client.get("/v1/models?pipeline_id=writer", headers={"authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == [
        "deepseek:deepseek-v4-pro",
        "openai:gpt-policy",
    ]


def test_models_endpoint_rejects_unknown_requested_pipeline(monkeypatch):
    _configure_runtime(monkeypatch)
    _set_pipeline_registry(
        monkeypatch,
        {
            "writer": {
                "default_corpus_id": "docs",
                "allowed_corpus_ids": ["docs"],
                "default_filters": {},
                "allowed_tools": ["rag"],
                "allowed_providers": ["openai"],
                "allowed_models": ["openai:gpt-policy"],
            }
        },
    )

    client = TestClient(main.app)
    response = client.get("/v1/models?pipeline_id=other", headers={"authorization": "Bearer test-token"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_pipeline"


def test_internal_reload_reports_loaded_provider_capabilities(monkeypatch):
    _configure_runtime(monkeypatch)
    _enable_deepseek_chunking(monkeypatch)
    monkeypatch.setattr(main, "reload_token", "reload-token")
    monkeypatch.setattr(main, "reload_runtime_state", lambda: None)

    client = TestClient(main.app)
    response = client.post("/v1/internal/reload", headers={"x-config-auth-token": "reload-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["providers"]["openai"]["chunking"] is False
    assert payload["providers"]["deepseek"]["chunking"] is True


def test_chat_completions_use_default_context_when_client_override_not_allowed(monkeypatch):
    _, deepseek = _configure_runtime(monkeypatch)
    monkeypatch.setattr(
        main, "pipeline_registry", PipelineRegistry(policies=(), default_corpus_id="legacy_default", default_filters={})
    )

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "deepseek:deepseek-v4-pro",
            "messages": [{"role": "user", "content": "hello"}],
            "context_length": 4096,
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert deepseek.last_context_length is None


def test_chat_completions_forward_allowed_client_controls(monkeypatch):
    openai, _ = _configure_runtime(monkeypatch)
    monkeypatch.setattr(
        main, "pipeline_registry", PipelineRegistry(policies=(), default_corpus_id="legacy_default", default_filters={})
    )

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "openai:gpt-explicit",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.55,
            "max_tokens": 321,
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert openai.last_temperature == 0.55
    assert openai.last_max_tokens == 321
    assert openai.last_context_length == 8192


def test_chat_completions_forward_allowed_context_length_within_provider_max(monkeypatch):
    openai, _ = _configure_runtime(monkeypatch)
    monkeypatch.setattr(
        main, "pipeline_registry", PipelineRegistry(policies=(), default_corpus_id="legacy_default", default_filters={})
    )

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "openai:gpt-explicit",
            "messages": [{"role": "user", "content": "hello"}],
            "context_length": 4096,
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert openai.last_context_length == 4096


def test_chat_completions_clamp_context_length_to_provider_max(monkeypatch):
    openai, _ = _configure_runtime(monkeypatch)
    monkeypatch.setattr(
        main, "pipeline_registry", PipelineRegistry(policies=(), default_corpus_id="legacy_default", default_filters={})
    )

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "openai:gpt-explicit",
            "messages": [{"role": "user", "content": "hello"}],
            "context_length": 200000,
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert openai.last_context_length == 8192


def test_chat_completions_skip_server_tools_when_provider_disables_tools(monkeypatch):
    openai = _configure_openai_only_runtime(monkeypatch, tools_enabled=False)

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "openai:gpt-explicit",
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert openai.last_tools == []
    assert openai.last_tool_choice is None


def test_chat_completions_include_server_tools_when_provider_enables_tools(monkeypatch):
    openai = _configure_openai_only_runtime(monkeypatch, tools_enabled=True)

    client = TestClient(main.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "openai:gpt-explicit",
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert openai.last_tools
    assert openai.last_tools[0].name == "rag__query"
    assert openai.last_tool_choice == "auto"
