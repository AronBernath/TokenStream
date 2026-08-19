import os
import json
import logging
from pathlib import Path
import httpx
from typing import Dict, Any, List

REGISTRY_INTERNAL_URL = (os.environ.get("REGISTRY_INTERNAL_URL") or "").strip().rstrip("/")
PROCESSOR_REGISTRY_PATH = (os.environ.get("PROCESSOR_REGISTRY_PATH") or "/runtime/processors.json").strip()
PROCESSOR_REGISTRY_URL = (os.environ.get("PROCESSOR_REGISTRY_URL") or "").strip().rstrip("/")
CONFIG_AUTH_INTERNAL_TOKEN = os.environ.get("CONFIG_AUTH_INTERNAL_TOKEN", "").strip()
logger = logging.getLogger("ingestion-worker.registry")


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


def _processor_registry_url() -> str:
    if PROCESSOR_REGISTRY_URL:
        return PROCESSOR_REGISTRY_URL
    if REGISTRY_INTERNAL_URL:
        return f"{REGISTRY_INTERNAL_URL}/processors"
    return ""


def get_corpus(corpus_id: str) -> Dict[str, Any]:
    url = f"{REGISTRY_INTERNAL_URL}/corpora/{corpus_id}"
    with httpx.Client(timeout=10.0) as client:
        response = client.get(url, headers=_get_headers())
        response.raise_for_status()
        return response.json()


def list_corpus_sources(corpus_id: str) -> List[Dict[str, Any]]:
    url = f"{REGISTRY_INTERNAL_URL}/corpora/{corpus_id}/sources"
    with httpx.Client(timeout=10.0) as client:
        response = client.get(url, headers=_get_headers())
        response.raise_for_status()
        return response.json()


def list_processors() -> List[Dict[str, Any]]:
    if PROCESSOR_REGISTRY_PATH:
        if os.path.exists(PROCESSOR_REGISTRY_PATH):
            return _load_records_from_path(PROCESSOR_REGISTRY_PATH, "processor_id")
        logger.warning(
            "processor_registry_snapshot_missing path=%s; falling back to registry API", PROCESSOR_REGISTRY_PATH
        )

    url = _processor_registry_url()
    if not url:
        return []
    with httpx.Client(timeout=10.0) as client:
        response = client.get(url, headers=_get_headers_for_url(url))
        response.raise_for_status()
        return response.json()
