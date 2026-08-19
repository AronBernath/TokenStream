import os
import json
import logging
from pathlib import Path
import httpx
from typing import Dict, Any, List

REGISTRY_INTERNAL_URL = (os.environ.get("REGISTRY_INTERNAL_URL") or "").strip().rstrip("/")
RETRIEVAL_PROFILE_REGISTRY_PATH = (
    os.environ.get("RETRIEVAL_PROFILE_REGISTRY_PATH") or "/runtime/retrieval_profiles.json"
).strip()
RETRIEVAL_PROFILE_REGISTRY_URL = (os.environ.get("RETRIEVAL_PROFILE_REGISTRY_URL") or "").strip().rstrip("/")
CONFIG_AUTH_INTERNAL_TOKEN = os.environ.get("CONFIG_AUTH_INTERNAL_TOKEN", "").strip()
SUPPORTED_RETRIEVAL_TYPES = {"hybrid"}
logger = logging.getLogger("retrieval-api.registry")


def _get_headers() -> Dict[str, str]:
    if not REGISTRY_INTERNAL_URL:
        raise ValueError("REGISTRY_INTERNAL_URL is not set")
    if not CONFIG_AUTH_INTERNAL_TOKEN:
        raise ValueError("CONFIG_AUTH_INTERNAL_TOKEN is not set")
    return {"Authorization": f"Bearer {CONFIG_AUTH_INTERNAL_TOKEN}"}


def _get_headers_for_url(url: str) -> Dict[str, str]:
    if not url:
        raise ValueError("registry URL is not set")
    if not CONFIG_AUTH_INTERNAL_TOKEN:
        raise ValueError("CONFIG_AUTH_INTERNAL_TOKEN is not set")
    return {"Authorization": f"Bearer {CONFIG_AUTH_INTERNAL_TOKEN}"}


def _load_records_from_path(path: str, id_field: str) -> List[Dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8") or "[]")
    if isinstance(payload, dict):
        return [
            {id_field: key, **value}
            for key, value in payload.items()
            if isinstance(key, str) and isinstance(value, dict)
        ]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    raise ValueError(f"Registry file '{path}' must contain an object or array")


def _retrieval_profile_registry_url() -> str:
    if RETRIEVAL_PROFILE_REGISTRY_URL:
        return RETRIEVAL_PROFILE_REGISTRY_URL
    if REGISTRY_INTERNAL_URL:
        return f"{REGISTRY_INTERNAL_URL}/retrieval-profiles"
    return ""


def _validate_retrieval_profile(profile: Dict[str, Any]) -> None:
    profile_type = str(profile.get("type") or "hybrid").strip().lower()
    if profile_type not in SUPPORTED_RETRIEVAL_TYPES:
        profile_id = str(profile.get("retrieval_profile_id") or "<unknown>")
        raise ValueError(f"Retrieval profile '{profile_id}' uses unsupported type '{profile_type}'")


def get_corpus(corpus_id: str) -> Dict[str, Any]:
    url = f"{REGISTRY_INTERNAL_URL}/corpora/{corpus_id}"
    with httpx.Client(timeout=10.0) as client:
        response = client.get(url, headers=_get_headers())
        response.raise_for_status()
        corpus = response.json()
        profile_id = str(corpus.get("retrieval_profile_id") or "").strip()
        if profile_id:
            profiles = list_retrieval_profiles()
            retrieval_profile = next(
                (
                    profile
                    for profile in profiles
                    if isinstance(profile, dict)
                    and str(profile.get("retrieval_profile_id") or "").strip() == profile_id
                ),
                None,
            )
            if not retrieval_profile:
                raise ValueError(f"Corpus '{corpus_id}' references unknown retrieval profile '{profile_id}'")
            if not bool(retrieval_profile.get("enabled", True)):
                raise ValueError(f"Corpus '{corpus_id}' references disabled retrieval profile '{profile_id}'")
            _validate_retrieval_profile(retrieval_profile)
            corpus["retrieval_profile"] = retrieval_profile
        return corpus


def list_corpora() -> List[str]:
    url = f"{REGISTRY_INTERNAL_URL}/corpora"
    with httpx.Client(timeout=10.0) as client:
        response = client.get(url, headers=_get_headers())
        response.raise_for_status()
        return response.json().get("corpora", [])


def list_retrieval_profiles() -> List[Dict[str, Any]]:
    if RETRIEVAL_PROFILE_REGISTRY_PATH:
        if os.path.exists(RETRIEVAL_PROFILE_REGISTRY_PATH):
            return _load_records_from_path(RETRIEVAL_PROFILE_REGISTRY_PATH, "retrieval_profile_id")
        logger.warning(
            "retrieval_profile_registry_snapshot_missing path=%s; falling back to registry API",
            RETRIEVAL_PROFILE_REGISTRY_PATH,
        )

    url = _retrieval_profile_registry_url()
    if not url:
        return []
    with httpx.Client(timeout=10.0) as client:
        response = client.get(url, headers=_get_headers_for_url(url))
        response.raise_for_status()
        return response.json()
