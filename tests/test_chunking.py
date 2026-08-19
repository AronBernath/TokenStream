"""
Unit tests for common.chunking module.
"""

import sys
from pathlib import Path

import pytest

# Add services/common to path so "from common.chunking" resolves
_COMMON_ROOT = Path(__file__).resolve().parent.parent / "services" / "common"
sys.path.insert(0, str(_COMMON_ROOT))

from common.chunking import (
    ChunkingError,
    chunk_text,
    make_chat_fn_from_orchestrator,
)


def test_chunk_text_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_text_llm_requires_chat_fn():
    text = "A" * 300
    with pytest.raises(ValueError, match="chat_fn is required"):
        chunk_text(text, strategy="llm", chat_fn=None)


def test_chunk_text_llm_prompt_demands_json_only_offsets():
    captured = {}

    def mock_chat(system: str, user: str) -> str:
        captured["system"] = system
        captured["user"] = user
        return '{"chunks":[{"start":0,"end":11}]}'

    chunk_text("First part. Second part.", strategy="llm", chat_fn=mock_chat, use_cache=False)

    assert "Return only one valid JSON object" in captured["system"]
    assert "Do not explain" in captured["system"]
    assert "Offsets only" in captured["user"]
    assert "<document>" in captured["user"]


def test_chunk_text_rejects_non_llm_strategy():
    with pytest.raises(ValueError, match="only 'llm' is allowed"):
        chunk_text("Some document text.", strategy="character", chat_fn=lambda system, user: "[]")


def test_chunk_text_llm_with_mock_chat_fn():
    """LLM strategy with mock chat_fn returns parsed chunks."""

    def mock_chat(system: str, user: str) -> str:
        return '{"chunks":[{"start":0,"end":11},{"start":12,"end":24}]}'

    text = "First chunk. Second chunk."
    result = chunk_text(text, strategy="llm", chat_fn=mock_chat, use_cache=False)
    assert result == ["First chunk.", "Second chunk."]


def test_chunk_text_llm_accepts_chunk_offsets_object():
    def mock_chat(system: str, user: str) -> str:
        return '{"chunks":[{"start":0,"end":11},{"start":12,"end":24}]}'

    text = "First part. Second part."
    result = chunk_text(text, strategy="llm", chat_fn=mock_chat, use_cache=False)

    assert result == ["First part.", "Second part."]


@pytest.mark.parametrize(
    "payload", ['{"offsets":[{"start":0,"end":11}]}', '{"result":{"chunks":[{"start":0,"end":11}]}}']
)
def test_chunk_text_llm_rejects_wrapped_offset_payload_variants(payload):
    def mock_chat(system: str, user: str) -> str:
        return payload

    with pytest.raises(ChunkingError, match="missing chunks list"):
        chunk_text("First part. Second part.", strategy="llm", chat_fn=mock_chat, use_cache=False)


def test_chunk_text_llm_rejects_prose_wrapped_json_object():
    def mock_chat(system: str, user: str) -> str:
        return 'Here is the JSON:\n```json\n{"chunks":[{"start":0,"end":11}]}\n```'

    text = "First part. Second part."
    with pytest.raises(ChunkingError, match="invalid JSON"):
        chunk_text(text, strategy="llm", chat_fn=mock_chat, use_cache=False)


def test_chunk_text_llm_invalid_json_raises():
    def mock_chat(system: str, user: str) -> str:
        return "not valid json"

    text = "A" * 2500
    with pytest.raises(ChunkingError, match="invalid JSON"):
        chunk_text(text, strategy="llm", chat_fn=mock_chat, use_cache=False, target_chars=500)


def test_chunk_text_llm_large_input_uses_windowed_llm_calls(monkeypatch):
    calls = 0
    monkeypatch.setattr("common.chunking.LLM_CHUNKING_WINDOW_CHARS", 1200)
    monkeypatch.setattr("common.chunking.LLM_CHUNKING_WINDOW_OVERLAP_CHARS", 100)

    def mock_chat(system: str, user: str) -> str:
        nonlocal calls
        calls += 1
        return '{"chunks":[{"start": 0, "end": 300}]}'

    text = "1. Huge Section\n" + ("Word " * 5000)
    result = chunk_text(text, strategy="llm", chat_fn=mock_chat, use_cache=False, target_chars=500)
    assert calls > 1
    assert len(result) >= calls
    assert all(chunk for chunk in result)


def test_chunk_text_llm_large_input_invalid_window_response_fails(monkeypatch):
    monkeypatch.setattr("common.chunking.LLM_CHUNKING_WINDOW_CHARS", 1000)

    def mock_chat(system: str, user: str) -> str:
        return "[]"

    with pytest.raises(ChunkingError, match="non-object JSON"):
        chunk_text("Plain text. " * 300, strategy="llm", chat_fn=mock_chat, use_cache=False, target_chars=300)


def test_chunk_text_cache():
    """Same input returns cached result (no duplicate LLM call)."""
    call_count = 0

    def mock_chat(system: str, user: str) -> str:
        nonlocal call_count
        call_count += 1
        return '{"chunks":[{"start":0,"end":16}]}'

    text = "Document to cache."
    r1 = chunk_text(text, strategy="llm", chat_fn=mock_chat, use_cache=True)
    r2 = chunk_text(text, strategy="llm", chat_fn=mock_chat, use_cache=True)
    assert r1 == r2 == ["Document to cache."]
    assert call_count == 1


def test_make_chat_fn_returns_none_when_no_url():
    assert make_chat_fn_from_orchestrator(orchestrator_api_url="") is None
    assert make_chat_fn_from_orchestrator(orchestrator_api_url="   ") is None


def test_make_chat_fn_returns_callable_when_url_provided():
    fn = make_chat_fn_from_orchestrator(orchestrator_api_url="http://localhost:8004")
    assert fn is not None
    assert callable(fn)


def test_make_chat_fn_sends_chunking_response_format(monkeypatch):
    import httpx

    captured = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": '{"chunks":[{"start":0,"end":4}]}'}}]}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, endpoint, json, headers):
            captured["endpoint"] = endpoint
            captured["json"] = json
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)

    fn = make_chat_fn_from_orchestrator(
        orchestrator_api_url="http://localhost:8004",
        pipeline_id="default",
        task="chunking",
    )

    assert fn is not None
    assert fn("system", "user") == '{"chunks":[{"start":0,"end":4}]}'
    assert captured["json"]["response_format"]["type"] == "json_object"
    assert captured["json"]["max_tokens"] == 3000
