from types import SimpleNamespace

import app.main as main_module
from app.config import load_settings
from app.provider_registry import build_provider_registry
from app.provider_settings import ProviderClientControls, ProviderDefinition, ProviderCapabilities


def test_load_settings_collects_openai_and_deepseek_model_lists(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.5-medium")
    monkeypatch.setenv("OPENAI_MODELS", "gpt-5.4-medium,gpt-5.5-medium")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("DEEPSEEK_MODELS", "deepseek-v4-pro,deepseek-v4-flash")

    settings = load_settings()

    openai_pdef = next(p for p in settings.providers if p.name == "openai")
    assert openai_pdef.default_model == "gpt-5.5-medium"
    assert openai_pdef.models == ("gpt-5.4-medium", "gpt-5.5-medium")
    assert openai_pdef.client_controls.max_tokens is True

    deepseek_pdef = next(p for p in settings.providers if p.name == "deepseek")
    assert deepseek_pdef.default_model == "deepseek-v4-pro"
    assert deepseek_pdef.models == ("deepseek-v4-pro", "deepseek-v4-flash")


def test_listed_models_for_provider_uses_configured_model_list(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(
            providers=(
                ProviderDefinition(
                    name="openai",
                    type="openai_compat",
                    base_url="",
                    require_api_key=False,
                    default_model="gpt-5.5-medium",
                    models=("gpt-5.4-medium", "gpt-5.5-medium"),
                    capabilities=ProviderCapabilities(),
                ),
                ProviderDefinition(
                    name="deepseek",
                    type="openai_compat",
                    base_url="",
                    require_api_key=False,
                    default_model="deepseek-v4-pro",
                    models=("deepseek-v4-pro", "deepseek-v4-flash"),
                    capabilities=ProviderCapabilities(),
                ),
            )
        ),
    )

    assert main_module._listed_models_for_provider("openai") == [
        "gpt-5.4-medium",
        "gpt-5.5-medium",
    ]
    assert main_module._listed_models_for_provider("deepseek") == [
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    ]


def test_provider_registry_uses_configured_context_param_verbatim():
    settings = SimpleNamespace(
        default_temperature=0.1,
        default_max_tokens=3000,
        llm_timeout_s=30,
        llm_max_retries=0,
        llm_retry_backoff_s=0,
        providers=(
            ProviderDefinition(
                name="openai",
                type="openai_compat",
                base_url="https://api.openai.com/v1",
                require_api_key=False,
                default_model="gpt-test",
                models=("gpt-test",),
                capabilities=ProviderCapabilities(json_schema=True, chunking=True),
                client_controls=ProviderClientControls(context_length=True, context_length_param="num_ctx"),
            ),
        ),
    )

    providers = build_provider_registry(settings)

    assert providers["openai"]._context_length_param == "num_ctx"


def test_load_settings_falls_back_to_legacy_providers_when_snapshot_missing(monkeypatch, tmp_path):
    missing_snapshot = tmp_path / "providers.json"
    monkeypatch.delenv("LLM_PROVIDERS_JSON", raising=False)
    monkeypatch.setenv("LLM_PROVIDERS_PATH", str(missing_snapshot))
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.5-medium")
    monkeypatch.setenv("OPENAI_MODELS", "gpt-5.4-medium,gpt-5.5-medium")

    settings = load_settings()

    openai_pdef = next(p for p in settings.providers if p.name == "openai")
    assert openai_pdef.default_model == "gpt-5.5-medium"


def test_load_settings_prefers_runtime_provider_snapshot_over_json_env(monkeypatch, tmp_path):
    providers_path = tmp_path / "providers.json"
    providers_path.write_text(
        """
        [
          {
            "name": "openai",
            "type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "require_api_key": true,
            "default_model": "gpt-runtime",
            "models": ["gpt-runtime"],
            "capabilities": {"json_schema": true, "chunking": true}
          }
        ]
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "LLM_PROVIDERS_JSON",
        """
        [
          {
            "name": "openai",
            "type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "require_api_key": true,
            "default_model": "gpt-stale",
            "models": ["gpt-stale"],
            "capabilities": {"json_schema": true, "chunking": false}
          }
        ]
        """,
    )
    monkeypatch.setenv("LLM_PROVIDERS_PATH", str(providers_path))

    settings = load_settings()

    openai_pdef = next(p for p in settings.providers if p.name == "openai")
    assert openai_pdef.default_model == "gpt-runtime"
    assert openai_pdef.capabilities.chunking is True


def test_load_settings_reads_mcp_runtime_snapshots(monkeypatch, tmp_path):
    mcp_servers_path = tmp_path / "mcp_servers.json"
    mcp_settings_path = tmp_path / "mcp_settings.json"
    mcp_servers_path.write_text(
        '[{"name":"viz","transport":"streamable_http","url":"http://viz:8101/mcp"}]',
        encoding="utf-8",
    )
    mcp_settings_path.write_text(
        '{"timeout_s": 30, "strict": true, "max_tool_rounds": 12}',
        encoding="utf-8",
    )
    monkeypatch.setenv("MCP_SERVERS_PATH", str(mcp_servers_path))
    monkeypatch.setenv("MCP_SETTINGS_PATH", str(mcp_settings_path))
    monkeypatch.setenv("MCP_TIMEOUT_S", "45")
    monkeypatch.setenv("MCP_STRICT", "false")
    monkeypatch.setenv("MCP_MAX_TOOL_ROUNDS", "6")

    settings = load_settings()

    assert '"name":"viz"' in settings.mcp_servers_json.replace(" ", "")
    assert settings.mcp_timeout_s == 30
    assert settings.mcp_strict is True
    assert settings.mcp_max_tool_rounds == 12


def test_load_settings_has_no_default_provider_without_env(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    settings = load_settings()

    assert settings.default_provider is None
