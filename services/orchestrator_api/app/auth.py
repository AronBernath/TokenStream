import json
import os
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

from common.auth import verify_bearer_token_hash


@dataclass(frozen=True)
class AuthContext:
    key_id: str
    subject: str
    scopes: Set[str]
    default_pipeline_id: Optional[str] = None
    allowed_providers: Optional[Tuple[str, ...]] = None
    allowed_models: Optional[Tuple[str, ...]] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    max_total_tokens: Optional[int] = None
    max_top_k: Optional[int] = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes or "admin:*" in self.scopes


@dataclass(frozen=True)
class ApiKeyEntry:
    key_id: str
    key_hash: str
    key_salt: Optional[str]
    key_algorithm: str
    subject: str
    scopes: Tuple[str, ...]
    default_pipeline_id: Optional[str] = None
    allowed_providers: Optional[Tuple[str, ...]] = None
    allowed_models: Optional[Tuple[str, ...]] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    max_total_tokens: Optional[int] = None
    max_top_k: Optional[int] = None


class AuthRegistry:
    def __init__(self, entries: List[ApiKeyEntry], legacy_key: Optional[str] = None):
        self._entries = entries
        self._legacy_key = legacy_key

    @classmethod
    def load(cls, legacy_key: Optional[str] = None) -> "AuthRegistry":
        json_str = os.environ.get("ORCHESTRATOR_API_KEYS_JSON", "").strip()
        path_str = os.environ.get("ORCHESTRATOR_API_KEYS_PATH", "").strip()

        entries = []
        payload = None

        if payload is None:
            if json_str:
                try:
                    payload = json.loads(json_str)
                except Exception as exc:
                    raise ValueError(f"Invalid ORCHESTRATOR_API_KEYS_JSON: {exc}") from exc
            elif path_str:
                try:
                    with open(path_str, "r", encoding="utf-8") as fp:
                        payload = json.load(fp)
                except FileNotFoundError:
                    pass
                except Exception as exc:
                    raise ValueError(f"Invalid API keys file '{path_str}': {exc}") from exc

        if payload is not None:
            if not isinstance(payload, list):
                raise ValueError("API keys configuration must be a JSON array")
            for item in payload:
                if not isinstance(item, dict):
                    continue
                entries.append(
                    ApiKeyEntry(
                        key_id=item.get("key_id", "unknown"),
                        key_hash=item.get("key_hash", ""),
                        key_salt=item.get("key_salt"),
                        key_algorithm=str(item.get("key_algorithm", "sha256")),
                        subject=item.get("subject", "unknown"),
                        scopes=tuple(item.get("scopes", [])),
                        default_pipeline_id=item.get("default_pipeline_id"),
                        allowed_providers=tuple(item["allowed_providers"])
                        if item.get("allowed_providers") is not None
                        else None,
                        allowed_models=tuple(item["allowed_models"])
                        if item.get("allowed_models") is not None
                        else None,
                        max_input_tokens=item.get("max_input_tokens"),
                        max_output_tokens=item.get("max_output_tokens"),
                        max_total_tokens=item.get("max_total_tokens"),
                        max_top_k=item.get("max_top_k"),
                    )
                )

        return cls(entries=entries, legacy_key=legacy_key)

    def authenticate(self, bearer_token: str) -> Optional[AuthContext]:
        if not bearer_token:
            return None

        token = bearer_token.strip()
        if token.startswith("Bearer "):
            token = token[len("Bearer ") :].strip()

        # Check against legacy key if configured
        if self._legacy_key and token == self._legacy_key:
            return AuthContext(
                key_id="legacy", subject="legacy", scopes={"models:list", "chat:invoke", "rag:query", "tools:use"}
            )

        for entry in self._entries:
            if verify_bearer_token_hash(
                token,
                entry.key_hash,
                salt=entry.key_salt,
                algorithm=entry.key_algorithm,
            ):
                return AuthContext(
                    key_id=entry.key_id,
                    subject=entry.subject,
                    scopes=set(entry.scopes),
                    default_pipeline_id=entry.default_pipeline_id,
                    allowed_providers=entry.allowed_providers,
                    allowed_models=entry.allowed_models,
                    max_input_tokens=entry.max_input_tokens,
                    max_output_tokens=entry.max_output_tokens,
                    max_total_tokens=entry.max_total_tokens,
                    max_top_k=entry.max_top_k,
                )

        return None
