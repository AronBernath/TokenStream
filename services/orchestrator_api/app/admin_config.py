import json
import os
import shutil
import tempfile
from typing import List, Optional, Any
from .admin_models import (
    ProviderDefinitionModel,
    PipelinePolicyModel,
    ApiKeyEntryModel,
    UserModel,
    RagSettingsModel,
)

ADMIN_DATA_DIR = os.environ.get("ADMIN_DATA_DIR", "/data/orchestrator-admin")


def _safe_write_json(path: str, data: Any):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix="tmp_cfg_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        shutil.move(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def _safe_read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


class AdminConfigStore:
    def __init__(self, data_dir: str = ADMIN_DATA_DIR):
        self.data_dir = data_dir
        self.providers_path = os.path.join(data_dir, "providers.json")
        self.policies_path = os.path.join(data_dir, "policies.json")
        self.api_keys_path = os.path.join(data_dir, "api_keys.json")
        self.users_path = os.path.join(data_dir, "users.json")
        self.rag_settings_path = os.path.join(data_dir, "rag_settings.json")

    def load_providers(self) -> List[ProviderDefinitionModel]:
        data = _safe_read_json(self.providers_path, [])
        return [ProviderDefinitionModel.model_validate(item) for item in data]

    def save_providers(self, providers: List[ProviderDefinitionModel]):
        _safe_write_json(self.providers_path, [p.model_dump() for p in providers])

    def load_policies(self) -> List[PipelinePolicyModel]:
        data = _safe_read_json(self.policies_path, [])
        return [PipelinePolicyModel.model_validate(item) for item in data]

    def save_policies(self, policies: List[PipelinePolicyModel]):
        _safe_write_json(self.policies_path, [p.model_dump() for p in policies])

    def load_api_keys(self) -> List[ApiKeyEntryModel]:
        data = _safe_read_json(self.api_keys_path, [])
        return [ApiKeyEntryModel.model_validate(item) for item in data]

    def save_api_keys(self, api_keys: List[ApiKeyEntryModel]):
        _safe_write_json(self.api_keys_path, [k.model_dump() for k in api_keys])

    def load_users(self) -> List[UserModel]:
        data = _safe_read_json(self.users_path, [])
        return [UserModel.model_validate(item) for item in data]

    def save_users(self, users: List[UserModel]):
        _safe_write_json(self.users_path, [u.model_dump() for u in users])

    def load_rag_settings(self) -> Optional[RagSettingsModel]:
        data = _safe_read_json(self.rag_settings_path, None)
        if data is None:
            return None
        return RagSettingsModel.model_validate(data)

    def save_rag_settings(self, settings: RagSettingsModel):
        _safe_write_json(self.rag_settings_path, settings.model_dump())


admin_config_store = AdminConfigStore()
