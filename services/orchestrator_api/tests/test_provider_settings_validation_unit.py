import json
import sys
from pathlib import Path

import pytest


SERVICES_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_ROOT = SERVICES_ROOT / "orchestrator_api"
COMMON_ROOT = SERVICES_ROOT / "common"
if str(ORCHESTRATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_ROOT))
if str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

from app.errors import ServiceError
from app.logging_utils import bounded_log_payload, bounded_response_payload
from app.provider_settings import (
    ProviderCapabilities,
    ProviderClientControls,
    ProviderDefinition,
    parse_llm_providers,
)
from app.validation import normalize_response_format_for_provider, validate_response_format


def test_provider_capabilities_clamp_invalid_context_windows():
    capabilities = ProviderCapabilities.from_dict({"max_context_window": -1, "default_context_window": 200000})

    assert capabilities.max_context_window == 8192
    assert capabilities.default_context_window == 8192


def test_provider_definition_appends_default_model_and_resolves_api_key_env(monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "secret-value")

    provider = ProviderDefinition.from_dict(
        {
            "name": "openai",
            "type": "openai_compat",
            "base_url": "https://api.example.test/v1",
            "models": ["gpt-a"],
            "default_model": "gpt-b",
            "api_key_env": "TEST_PROVIDER_KEY",
            "client_controls": {"context_length": True, "context_length_param": "num_ctx"},
        }
    )

    assert provider.models == ("gpt-a", "gpt-b")
    assert provider.api_key == "secret-value"
    assert provider.client_controls == ProviderClientControls(
        temperature=True,
        max_tokens=True,
        context_length=True,
        context_length_param="num_ctx",
    )


def test_parse_llm_providers_rejects_non_array_json():
    with pytest.raises(ValueError, match="JSON array"):
        parse_llm_providers(json.dumps({"name": "not-a-list"}), "")


def test_response_format_normalization_closes_nested_object_schemas_without_mutating_input():
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "schema": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string"},
                    "meta": {
                        "type": "object",
                        "properties": {"confidence": {"type": "number"}},
                    },
                },
            },
        },
    }

    normalized = normalize_response_format_for_provider(response_format, provider_name="openai")

    assert response_format["json_schema"]["schema"].get("additionalProperties") is None
    schema = normalized["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["meta"]["additionalProperties"] is False


def test_validate_response_format_rejects_invalid_json_and_schema_mismatch():
    with pytest.raises(ServiceError) as invalid_json:
        validate_response_format({"type": "json_object"}, "{not json")
    assert invalid_json.value.code == "response_json_decode_failed"

    with pytest.raises(ServiceError) as schema_error:
        validate_response_format(
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "answer",
                    "schema": {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}},
                },
            },
            '{"answer": 12}',
        )
    assert schema_error.value.code == "response_schema_validation_failed"


def test_bounded_log_helpers_truncate_only_string_fields():
    assert bounded_log_payload(max_chars=4, text="abcdef", count=7) == {"text": "abcd\u2026", "count": 7}
    assert bounded_response_payload(max_chars=4, content="abcdef", id="x") == {"id": "x", "content": "abcd\u2026"}
