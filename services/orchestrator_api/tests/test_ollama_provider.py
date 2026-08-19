import pytest
from common.llm.errors import LLMError
from common.llm.providers.ollama import OllamaNativeProvider
from common.llm.types import ChatMessage, GenerationParams


@pytest.mark.asyncio
async def test_ollama_provider_forwards_options_correctly(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        content = b'{"model": "test-model", "message": {"role": "assistant", "content": "ok"}, "done": true}'

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

    monkeypatch.setattr("common.llm.providers.ollama.httpx.AsyncClient", lambda **kw: FakeClient())

    provider = OllamaNativeProvider(
        name="test_ollama", api_key="test", base_url="http://test", default_model="test-model"
    )

    await provider.chat(
        messages=[ChatMessage(role="user", content="hello")],
        params=GenerationParams(model="test-model", context_length=4096, max_tokens=100, temperature=0.5),
    )

    assert len(calls) == 1
    req = calls[0]
    assert req["model"] == "test-model"
    assert req["messages"] == [{"role": "user", "content": "hello"}]
    assert "options" in req
    assert req["options"]["num_ctx"] == 4096
    assert req["options"]["num_predict"] == 100
    assert req["options"]["temperature"] == 0.5
    assert req["stream"] is False


@pytest.mark.asyncio
async def test_ollama_provider_response_format_json_object(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        content = b'{"model": "test-model", "message": {"role": "assistant", "content": "{}"}, "done": true}'

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

    monkeypatch.setattr("common.llm.providers.ollama.httpx.AsyncClient", lambda **kw: FakeClient())

    provider = OllamaNativeProvider(
        name="test_ollama", api_key="test", base_url="http://test", default_model="test-model"
    )

    await provider.chat(
        messages=[ChatMessage(role="user", content="hello")],
        params=GenerationParams(model="test-model", response_format={"type": "json_object"}),
    )

    assert len(calls) == 1
    assert calls[0]["format"] == "json"


@pytest.mark.asyncio
async def test_ollama_provider_response_format_json_schema(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        content = b'{"model": "test-model", "message": {"role": "assistant", "content": "{}"}, "done": true}'

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

    monkeypatch.setattr("common.llm.providers.ollama.httpx.AsyncClient", lambda **kw: FakeClient())

    provider = OllamaNativeProvider(
        name="test_ollama", api_key="test", base_url="http://test", default_model="test-model"
    )

    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    await provider.chat(
        messages=[ChatMessage(role="user", content="hello")],
        params=GenerationParams(
            model="test-model", response_format={"type": "json_schema", "json_schema": {"schema": schema}}
        ),
    )

    assert len(calls) == 1
    assert calls[0]["format"] == schema


@pytest.mark.asyncio
async def test_ollama_provider_requires_explicit_or_default_model():
    provider = OllamaNativeProvider(
        name="test_ollama",
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
async def test_ollama_provider_strips_v1_from_base_url(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        content = b'{"model": "test-model", "message": {"role": "assistant", "content": "ok"}, "done": true}'

        def json(self):
            import json

            return json.loads(self.content)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def post(self, url, json, headers):
            calls.append(url)
            return FakeResponse()

    monkeypatch.setattr("common.llm.providers.ollama.httpx.AsyncClient", lambda **kw: FakeClient())

    provider = OllamaNativeProvider(
        name="test_ollama", api_key="test", base_url="http://test/v1", default_model="test-model"
    )

    await provider.chat(
        messages=[ChatMessage(role="user", content="hello")], params=GenerationParams(model="test-model")
    )

    assert len(calls) == 1
    assert calls[0] == "http://test/api/chat"
