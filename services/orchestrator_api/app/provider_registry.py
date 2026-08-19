from __future__ import annotations

from typing import Dict

from common.llm.providers.anthropic import AnthropicProvider
from common.llm.providers.openai_compat import OpenAICompatibleProvider
from common.llm.providers.ollama import OllamaNativeProvider
from common.llm.types import LLMProvider

from .config import Settings


def build_provider_registry(settings: Settings) -> Dict[str, LLMProvider]:
    """
    Build the provider registry for orchestrator-api.

    We intentionally keep this lightweight and reuse the shared provider adapters
    from `common.llm` (OpenAI-compatible + Anthropic).
    """

    providers: Dict[str, LLMProvider] = {}

    for pdef in settings.providers:
        if pdef.type == "openai_compat":
            use_max_completion = pdef.name == "openai"  # gpt-5.x and newer require this
            providers[pdef.name] = OpenAICompatibleProvider(
                name=pdef.name,
                api_key=pdef.api_key,
                base_url=pdef.base_url,
                default_model=pdef.default_model,
                default_temperature=settings.default_temperature,
                default_max_tokens=settings.default_max_tokens,
                require_api_key=pdef.require_api_key,
                timeout_s=settings.llm_timeout_s,
                max_retries=settings.llm_max_retries,
                retry_backoff_s=settings.llm_retry_backoff_s,
                use_max_completion_tokens=use_max_completion,
                context_length_param=pdef.client_controls.context_length_param,
            )
        elif pdef.type == "anthropic":
            providers[pdef.name] = AnthropicProvider(
                api_key=pdef.api_key,
                base_url=pdef.base_url,
                default_model=pdef.default_model,
                default_temperature=settings.default_temperature,
                default_max_tokens=settings.default_max_tokens,
                timeout_s=settings.llm_timeout_s,
                context_length_param=pdef.client_controls.context_length_param,
            )
        elif pdef.type == "ollama":
            providers[pdef.name] = OllamaNativeProvider(
                name=pdef.name,
                api_key=pdef.api_key,
                base_url=pdef.base_url,
                default_model=pdef.default_model,
                default_temperature=settings.default_temperature,
                default_max_tokens=settings.default_max_tokens,
                require_api_key=pdef.require_api_key,
                timeout_s=settings.llm_timeout_s,
                max_retries=settings.llm_max_retries,
                retry_backoff_s=settings.llm_retry_backoff_s,
            )
        else:
            raise ValueError(f"Unknown provider type '{pdef.type}' for provider '{pdef.name}'")

    return providers
