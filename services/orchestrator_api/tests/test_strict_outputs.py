import json
from dataclasses import replace

import app.main as main_module
from fastapi.testclient import TestClient
from app.main import app
from app.auth import AuthRegistry
from app.validation import normalize_response_format_for_provider

client = TestClient(app)


def _enable_chat_auth(monkeypatch):
    monkeypatch.setattr(main_module, "settings", replace(main_module.settings, service_api_key="test-token"))
    monkeypatch.setattr(main_module, "auth_registry", AuthRegistry(entries=[], legacy_key="test-token"))
    return {"Authorization": "Bearer test-token"}


def test_chat_completions_json_schema_validation_success(monkeypatch):
    headers = _enable_chat_auth(monkeypatch)

    async def mock_chat(*args, **kwargs):
        from common.llm.types import ChatMessage, ChatResponse

        return ChatResponse(
            message=ChatMessage(role="assistant", content='{"name": "test", "value": 123}'),
            model="test-model",
            usage={},
        )

    monkeypatch.setattr(main_module.providers["openai"], "chat", mock_chat)

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "test_schema",
            "schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "integer"},
                },
                "required": ["name", "value"],
            },
            "strict": True,
        },
    }

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "openai:gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": response_format,
        },
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["choices"][0]["message"]["parsed"] == {"name": "test", "value": 123}


def test_chat_completions_json_schema_validation_failure(monkeypatch):
    headers = _enable_chat_auth(monkeypatch)

    async def mock_chat(*args, **kwargs):
        from common.llm.types import ChatMessage, ChatResponse

        return ChatResponse(
            message=ChatMessage(role="assistant", content='{"name": "test"}'),
            model="test-model",
            usage={},
        )

    monkeypatch.setattr(main_module.providers["openai"], "chat", mock_chat)

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "test_schema",
            "schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "integer"},
                },
                "required": ["name", "value"],
            },
            "strict": True,
        },
    }

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "openai:gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": response_format,
        },
        headers=headers,
    )

    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == "response_schema_validation_failed"


def test_chat_completions_unsupported_provider_strict_schema(monkeypatch):
    headers = _enable_chat_auth(monkeypatch)

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "test",
            "schema": {"type": "object"},
        },
    }

    client.post(
        "/v1/chat/completions",
        json={
            "model": "anthropic:claude-3-5-sonnet-latest",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": response_format,
        },
        headers=headers,
    )

    response_fake = client.post(
        "/v1/chat/completions",
        json={
            "model": "local:model",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": response_format,
        },
        headers=headers,
    )
    assert response_fake.status_code == 400


def test_normalize_response_format_closes_nested_object_schemas():
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "establish_context_output",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "canonical_model": {
                        "type": "object",
                        "properties": {
                            "actors": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "metadata": {
                                            "type": "object",
                                            "additionalProperties": True,
                                        }
                                    },
                                },
                            }
                        },
                    }
                },
            },
        },
    }

    normalized = normalize_response_format_for_provider(response_format, provider_name="openai")
    schema = normalized["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["canonical_model"]["additionalProperties"] is False
    assert schema["properties"]["canonical_model"]["properties"]["actors"]["items"]["additionalProperties"] is False
    assert (
        schema["properties"]["canonical_model"]["properties"]["actors"]["items"]["properties"]["metadata"][
            "additionalProperties"
        ]
        is False
    )


def test_chat_completions_send_normalized_schema_to_provider(monkeypatch):
    headers = _enable_chat_auth(monkeypatch)
    captured = {}

    async def mock_chat(*args, **kwargs):
        from common.llm.types import ChatMessage, ChatResponse

        captured["response_format"] = kwargs["params"].response_format
        return ChatResponse(
            message=ChatMessage(
                role="assistant", content=json.dumps({"canonical_model": {"actors": [{"metadata": {}}]}})
            ),
            model="test-model",
            usage={},
        )

    monkeypatch.setattr(main_module.providers["openai"], "chat", mock_chat)

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "establish_context_output",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "canonical_model": {
                        "type": "object",
                        "properties": {
                            "actors": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "metadata": {
                                            "type": "object",
                                            "additionalProperties": True,
                                        }
                                    },
                                },
                            }
                        },
                    }
                },
            },
        },
    }

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "openai:gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": response_format,
        },
        headers=headers,
    )

    assert response.status_code == 200
    sent_schema = captured["response_format"]["json_schema"]["schema"]
    assert sent_schema["properties"]["canonical_model"]["additionalProperties"] is False
    assert (
        sent_schema["properties"]["canonical_model"]["properties"]["actors"]["items"]["properties"]["metadata"][
            "additionalProperties"
        ]
        is False
    )
