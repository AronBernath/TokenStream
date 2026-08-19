"""Minimal FastAPI app for dev-ui service."""

import os
import sys
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.staticfiles import StaticFiles

_SERVICES_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SERVICES_DIR.parent
_PACKAGES_ROOT = _REPO_ROOT / "packages"
if (_PACKAGES_ROOT / "config_auth").exists() and str(_PACKAGES_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGES_ROOT))
_COMMON_ROOT = _SERVICES_DIR / "common"
if (_COMMON_ROOT / "common").exists() and str(_COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(_COMMON_ROOT))

from common.logging_config import configure_logging

configure_logging("dev-ui")

from config_auth.app.main import (
    init_config_auth_runtime,
    internal_router as config_auth_internal_router,
    require_permission,
    router as config_auth_router,
)

app = FastAPI(
    title="Dev UI",
    version="0.1.0",
    description="Administration UI for the orchestrator application.",
)

ORCHESTRATOR_API_URL = os.environ.get("ORCHESTRATOR_API_URL", "http://orchestrator-api:8004").rstrip("/")
INGESTION_WORKER_URL = os.environ.get("INGESTION_WORKER_URL", "http://ingestion-worker:8002").rstrip("/")
app.include_router(config_auth_router)
app.include_router(config_auth_internal_router)


@app.post("/v1/management/corpora/{corpus_id}/chunking-dry-run")
async def proxy_chunking_dry_run(
    corpus_id: str,
    body: dict,
    _: object = Depends(require_permission("corpora:read")),
):
    payload = {**(body or {}), "corpus_id": corpus_id}
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(f"{INGESTION_WORKER_URL}/v1/dry-run/chunking", json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail={"error": f"Unable to reach ingestion worker: {exc}"}) from exc
    if response.status_code >= 400:
        try:
            detail = response.json()
        except ValueError:
            detail = {"error": response.text}
        raise HTTPException(status_code=response.status_code, detail=detail)
    return response.json()


@app.on_event("startup")
async def _startup_config_auth():
    _ensure_frontend_build()
    await init_config_auth_runtime()


@app.middleware("http")
async def _no_cache_root_html(request: Request, call_next):
    resp = await call_next(request)
    # Prevent sticky client/proxy caching of the single-page UI shell.
    if request.url.path in ("/", "/index.html", "/admin"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp


# Mount frontend assets last so API routes are handled by FastAPI first.
_SERVICE_DIR = Path(__file__).resolve().parent
_FRONTEND_DIST_DIR = Path(os.environ.get("DEV_UI_FRONTEND_DIST_DIR") or _SERVICE_DIR / "frontend" / "dist").resolve()
_INDEX_HTML = _FRONTEND_DIST_DIR / "index.html"


def _ensure_frontend_build() -> None:
    if not _INDEX_HTML.is_file():
        raise RuntimeError(
            "React frontend build not found. Expected index.html at "
            f"{_INDEX_HTML}. Build services/dev-ui/frontend before starting dev-ui."
        )


@app.get("/")
@app.get("/index.html")
@app.get("/admin")
@app.get("/admin/{path:path}")
async def serve_index_html():
    """
    Serve the SPA shell with strict no-cache semantics.

    Some clients/proxies aggressively cache `/` and keep serving stale HTML even when images are updated.
    Returning 200 with `no-store` avoids sticky 304 Not Modified behavior.
    """
    _ensure_frontend_build()
    content = _INDEX_HTML.read_text(encoding="utf-8")
    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST_DIR), html=True, check_dir=False), name="static")
