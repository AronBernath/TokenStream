import pytest
from common.llm.errors import LLMError
from common.llm.providers.openai_compat import OpenAICompatibleProvider
from common.llm.providers.anthropic import AnthropicProvider
from common.llm.types import ChatMessage, GenerationParams


@pytest.mark.asyncio
async def test_openai_provider_forwards_response_format(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        content = b'{"choices": [{"message": {"content": "ok"}}]}'

        def json(self):
            import json

            return json.loads(self.content)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def post(self, url, json, headers):
            calls.append(json)
            return FakeResponse()

    monkeypatch.setattr("common.llm.providers.openai_compat.httpx.AsyncClient", lambda **kw: FakeClient())

    provider = OpenAICompatibleProvider(name="test", api_key="test", base_url="http://test", default_model="test-model")

    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "test_schema", "schema": {"type": "object"}, "strict": True},
    }

    await provider.chat(
        messages=[ChatMessage(role="user", content="hello")],
        params=GenerationParams(model="test-model", response_format=response_format),
    )

    assert len(calls) == 1
    assert calls[0]["response_format"] == response_format


@pytest.mark.asyncio
async def test_openai_provider_forwards_context_length_with_custom_param(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        content = b'{"choices": [{"message": {"content": "ok"}}]}'

        def json(self):
            import json

            return json.loads(self.content)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def post(self, url, json, headers):
            calls.append(json)
            return FakeResponse()

    monkeypatch.setattr("common.llm.providers.openai_compat.httpx.AsyncClient", lambda **kw: FakeClient())

    provider = OpenAICompatibleProvider(
        name="test",
        api_key="test",
        base_url="http://test",
        default_model="test-model",
        context_length_param="num_ctx",
    )

    await provider.chat(
        messages=[ChatMessage(role="user", content="hello")],
        params=GenerationParams(model="test-model", context_length=4096),
    )

    assert len(calls) == 1
    assert calls[0]["num_ctx"] == 4096


@pytest.mark.asyncio
async def test_openai_provider_requires_explicit_or_default_model():
    provider = OpenAICompatibleProvider(
        name="test",
        api_key="test",
        base_url="http://test",
        default_model="",
    )

    with pytest.raises(LLMError) as exc:
        await provider.chat(
            messages=[ChatMessage(role="user", content="hello")],
            params=GenerationParams(model=None),
        )
    assert exc.value.code == "provider_error"


@pytest.mark.asyncio
async def test_anthropic_provider_requires_explicit_or_default_model():
    provider = AnthropicProvider(
        api_key="test",
        base_url="http://test",
        default_model="",
    )

    with pytest.raises(LLMError) as exc:
        await provider.chat(
            messages=[ChatMessage(role="user", content="hello")],
            params=GenerationParams(model=None),
        )
    assert exc.value.code == "provider_error"
