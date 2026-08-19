import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

logger = logging.getLogger("orchestrator-api.provider_settings")


@dataclass(frozen=True)
class ProviderCapabilities:
    tools: bool = True
    json_schema: bool = False
    streaming: bool = True
    chunking: bool = False
    max_context_window: int = 8192
    default_context_window: int = 8192

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProviderCapabilities":
        legacy_context_window = data.get("context_window", 8192)
        max_context_window = int(data.get("max_context_window", legacy_context_window))
        default_context_window = int(data.get("default_context_window", max_context_window))
        if max_context_window <= 0:
            max_context_window = 8192
        if default_context_window <= 0:
            default_context_window = max_context_window
        default_context_window = min(default_context_window, max_context_window)
        return cls(
            tools=bool(data.get("tools", True)),
            json_schema=bool(data.get("json_schema", False)),
            streaming=bool(data.get("streaming", True)),
            chunking=bool(data.get("chunking", False)),
            max_context_window=max_context_window,
            default_context_window=default_context_window,
        )


@dataclass(frozen=True)
class ProviderClientControls:
    temperature: bool = True
    max_tokens: bool = True
    context_length: bool = False
    context_length_param: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProviderClientControls":
        return cls(
            temperature=bool(data.get("temperature", True)),
            max_tokens=bool(data.get("max_tokens", True)),
            context_length=bool(data.get("context_length", False)),
            context_length_param=(
                str(data.get("context_length_param")).strip()
                if isinstance(data.get("context_length_param"), str) and str(data.get("context_length_param")).strip()
                else None
            ),
        )


@dataclass(frozen=True)
class ProviderDefinition:
    name: str
    type: str
    base_url: str
    require_api_key: bool
    default_model: str
    models: Tuple[str, ...]
    capabilities: ProviderCapabilities
    client_controls: ProviderClientControls = ProviderClientControls()
    api_key_env: Optional[str] = None
    secret_ref: Optional[str] = None
    api_key: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProviderDefinition":
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("Provider definition missing 'name'")
        type_ = str(data.get("type", "")).strip()
        if not type_:
            raise ValueError(f"Provider '{name}' missing 'type'")

        base_url = str(data.get("base_url", "")).strip()

        models_raw = data.get("models", [])
        if not isinstance(models_raw, list):
            models_raw = []

        default_model = str(data.get("default_model", "")).strip()
        models = [str(m).strip() for m in models_raw if str(m).strip()]
        if default_model and default_model not in models:
            models.append(default_model)

        capabilities_data = data.get("capabilities", {})
        if not isinstance(capabilities_data, dict):
            capabilities_data = {}
        client_controls_data = data.get("client_controls", {})
        if not isinstance(client_controls_data, dict):
            client_controls_data = {}

        api_key_env = data.get("api_key_env")
        secret_ref = data.get("secret_ref")
        api_key = data.get("api_key")
        if secret_ref and not api_key:
            api_key = _resolve_secret_ref(str(secret_ref))
        if api_key_env and not api_key:
            api_key = os.environ.get(api_key_env)

        return cls(
            name=name,
            type=type_,
            base_url=base_url,
            require_api_key=bool(data.get("require_api_key", True)),
            default_model=default_model,
            models=tuple(models),
            capabilities=ProviderCapabilities.from_dict(capabilities_data),
            client_controls=ProviderClientControls.from_dict(client_controls_data),
            api_key_env=api_key_env if isinstance(api_key_env, str) else None,
            secret_ref=secret_ref if isinstance(secret_ref, str) else None,
            api_key=api_key if isinstance(api_key, str) else None,
        )


def _resolve_secret_ref(secret_ref: str) -> Optional[str]:
    ref = (secret_ref or "").strip()
    if not ref:
        return None
    if ref.startswith("env://"):
        env_name = ref[len("env://") :].strip()
        return os.environ.get(env_name) if env_name else None
    if ref.startswith("docker://"):
        secret_name = ref[len("docker://") :].strip()
        if not secret_name:
            return None
        path = Path("/run/secrets") / secret_name
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
    if ref.startswith("file://"):
        file_path = ref[len("file://") :].strip()
        if not file_path:
            return None
        try:
            return Path(file_path).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
    if ref.startswith("vault://"):
        # Future-compatible reference form. Actual Vault resolution is handled outside orchestrator-api.
        return None
    return None


def parse_llm_providers(json_str: str, path_str: str) -> List[ProviderDefinition]:
    payload: List[Any] | None = None
    if json_str:
        try:
            parsed = json.loads(json_str)
            if not isinstance(parsed, list):
                raise ValueError("LLM_PROVIDERS_JSON must be a JSON array")
            payload = parsed
        except Exception as exc:
            raise ValueError(f"Invalid LLM_PROVIDERS_JSON: {exc}") from exc
    elif path_str:
        try:
            with open(path_str, "r", encoding="utf-8") as fp:
                parsed = json.load(fp)
            if not isinstance(parsed, list):
                raise ValueError("LLM_PROVIDERS_PATH file must contain a JSON array")
            payload = parsed
        except FileNotFoundError as exc:
            raise ValueError(f"Provider registry file not found: {path_str}") from exc
        except Exception as exc:
            raise ValueError(f"Invalid provider registry file '{path_str}': {exc}") from exc

    if payload is not None:
        return [ProviderDefinition.from_dict(item) for item in payload if isinstance(item, dict)]
    return []


def get_legacy_providers() -> List[ProviderDefinition]:
    providers = []

    # OpenAI
    openai_default_model = os.environ.get("OPENAI_MODEL", "gpt-5.1").strip()
    openai_models_raw = os.environ.get("OPENAI_MODELS", "")
    openai_models = [m.strip() for m in openai_models_raw.split(",") if m.strip()]
    if openai_default_model and openai_default_model not in openai_models:
        openai_models.append(openai_default_model)

    providers.append(
        ProviderDefinition(
            name="openai",
            type="openai_compat",
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            require_api_key=True,
            default_model=openai_default_model,
            models=tuple(openai_models),
            capabilities=ProviderCapabilities(
                tools=True, json_schema=True, streaming=True, max_context_window=128000, default_context_window=128000
            ),
            client_controls=ProviderClientControls(temperature=True, max_tokens=True),
            api_key_env="OPENAI_API_KEY",
            api_key=os.environ.get("OPENAI_API_KEY"),
        )
    )

    # DeepSeek
    deepseek_default_model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro").strip()
    deepseek_models_raw = os.environ.get("DEEPSEEK_MODELS", "")
    deepseek_models = [m.strip() for m in deepseek_models_raw.split(",") if m.strip()]
    if deepseek_default_model and deepseek_default_model not in deepseek_models:
        deepseek_models.append(deepseek_default_model)

    providers.append(
        ProviderDefinition(
            name="deepseek",
            type="openai_compat",
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            require_api_key=True,
            default_model=deepseek_default_model,
            models=tuple(deepseek_models),
            capabilities=ProviderCapabilities(
                tools=True, json_schema=True, streaming=True, max_context_window=64000, default_context_window=64000
            ),
            client_controls=ProviderClientControls(temperature=True, max_tokens=True),
            api_key_env="DEEPSEEK_API_KEY",
            api_key=os.environ.get("DEEPSEEK_API_KEY"),
        )
    )

    # Anthropic
    providers.append(
        ProviderDefinition(
            name="anthropic",
            type="anthropic",
            base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/"),
            require_api_key=True,
            default_model=os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest").strip(),
            models=(os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest").strip(),),
            capabilities=ProviderCapabilities(
                tools=True, json_schema=False, streaming=True, max_context_window=200000, default_context_window=200000
            ),
            client_controls=ProviderClientControls(temperature=True, max_tokens=True),
            api_key_env="ANTHROPIC_API_KEY",
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
        )
    )

    # Local
    local_model = os.environ.get("LOCAL_MODEL", "llama3").strip()
    providers.append(
        ProviderDefinition(
            name="local",
            type="ollama",
            base_url=os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434").rstrip("/"),
            require_api_key=False,
            default_model=local_model,
            models=(local_model,),
            capabilities=ProviderCapabilities(
                tools=False, json_schema=False, streaming=False, max_context_window=8192, default_context_window=8192
            ),
            client_controls=ProviderClientControls(
                temperature=True, max_tokens=True, context_length=True, context_length_param="num_ctx"
            ),
            api_key_env=None,
            api_key=None,
        )
    )

    return providers


def load_providers() -> List[ProviderDefinition]:
    json_str = os.environ.get("LLM_PROVIDERS_JSON", "").strip()
    path_str = os.environ.get("LLM_PROVIDERS_PATH", "").strip()

    if path_str:
        if os.path.exists(path_str):
            return parse_llm_providers("", path_str)
        logger.warning(
            "provider_registry_snapshot_missing path=%s; falling back to legacy provider env vars",
            path_str,
        )

    if json_str:
        return parse_llm_providers(json_str, path_str)

    return get_legacy_providers()
