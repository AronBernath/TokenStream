import logging
import asyncio
import os
import httpx
import traceback
import sys
from pathlib import Path
from fastapi import Depends, FastAPI, Header, HTTPException
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field

_COMMON_ROOT = Path(__file__).resolve().parents[2] / "common"
if str(_COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(_COMMON_ROOT))

from common.logging_config import configure_logging

configure_logging("ingestion-worker")

from worker.main import dry_run_chunking, purge_source_artifacts, run_ingest
from worker.registry_client import _get_headers, REGISTRY_INTERNAL_URL

logger = logging.getLogger("ingestion-worker.api")

WORKER_ID = os.environ.get("HOSTNAME", "worker-1")
INTERNAL_API_TOKEN = os.environ.get("CONFIG_AUTH_INTERNAL_TOKEN", "").strip()


class ChunkingDryRunRequest(BaseModel):
    corpus_id: str
    source_id: str | None = None
    version_date: str | None = None
    pipeline_id: str | None = None
    chunking_model: str | None = None
    processor_id: str | None = None
    processor_config: dict = Field(default_factory=dict)
    max_preview_chunks: int = Field(default=5, ge=1, le=20)


class SourcePurgeRequest(BaseModel):
    corpus_id: str
    source_id: str


class SourcePurgeResponse(BaseModel):
    status: str
    corpus_id: str
    source_id: str
    deleted_chunks: int = 0
    deleted_qdrant_points: int = 0


def require_internal_token(authorization: str | None = Header(default=None)) -> str:
    if not INTERNAL_API_TOKEN:
        raise HTTPException(status_code=503, detail={"error": "internal authentication is not configured"})
    if not authorization:
        raise HTTPException(status_code=401, detail={"error": "missing bearer token"})
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != INTERNAL_API_TOKEN:
        raise HTTPException(status_code=403, detail={"error": "invalid bearer token"})
    return token


def _update_job_from_worker(job_id: str, payload: dict) -> None:
    headers = _get_headers()
    headers["x-worker-id"] = WORKER_ID
    url = f"{REGISTRY_INTERNAL_URL}/ingestion-jobs/{job_id}"
    with httpx.Client(timeout=10.0) as client:
        response = client.patch(url, json=payload, headers=headers)
        response.raise_for_status()


async def poll_jobs():
    logger.info(f"Starting ingestion job poller for worker {WORKER_ID}")
    while True:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"{REGISTRY_INTERNAL_URL}/ingestion-jobs"
                response = await client.get(url, params={"status": "pending"}, headers=_get_headers())
                response.raise_for_status()
                jobs = response.json()

                if not jobs:
                    await asyncio.sleep(5)
                    continue

                for job in jobs:
                    job_id = job["job_id"]
                    try:
                        claim_url = f"{REGISTRY_INTERNAL_URL}/ingestion-jobs/{job_id}/claim"
                        claim_res = await client.post(claim_url, json={"worker_id": WORKER_ID}, headers=_get_headers())
                        claim_res.raise_for_status()
                        logger.info(f"Claimed job {job_id}")

                        # Start heartbeat task
                        heartbeat_task = asyncio.create_task(heartbeat_job(job_id))

                        def check_cancelled():
                            try:
                                url = f"{REGISTRY_INTERNAL_URL}/ingestion-jobs/{job_id}"
                                with httpx.Client(timeout=5.0) as check_client:
                                    res = check_client.get(url, headers=_get_headers())
                                return res.status_code == 200 and res.json().get("status") == "cancelled"
                            except Exception:
                                logger.warning("Unable to check cancellation for job %s", job_id, exc_info=True)
                                return False

                        def report_progress(stats: dict) -> None:
                            try:
                                _update_job_from_worker(
                                    job_id,
                                    {"status": "running", "stats": stats},
                                )
                            except Exception:
                                # Keep the ingestion running if telemetry is temporarily unavailable.
                                logger.warning("Unable to report progress for job %s", job_id, exc_info=True)

                        try:
                            configuration = job["request"].get("configuration") or {}
                            chunking_model = (
                                configuration.get("chunking_model") if isinstance(configuration, dict) else None
                            )
                            # Run the job
                            stats = await asyncio.to_thread(
                                run_ingest,
                                corpus_id=job["corpus_id"],
                                version_date=job["request"].get("version_date"),
                                pipeline_id=job["request"].get("pipeline_id"),
                                source_ids=job["request"].get("source_ids"),
                                doc_ids=job["request"].get("doc_ids"),
                                force_reembed=job["request"].get("force_reembed", False),
                                chunking_model=chunking_model,
                                processor_id=job["request"].get("processor_id"),
                                processor_config=job["request"].get("processor_config"),
                                check_cancelled=check_cancelled,
                                report_progress=report_progress,
                            )

                            # Update job as completed
                            _update_job_from_worker(job_id, {"status": "completed", "stats": stats})
                            logger.info(f"Completed job {job_id}")
                        except InterruptedError:
                            logger.info("Job %s was cancelled", job_id)
                        except Exception as e:
                            logger.exception(f"Job {job_id} failed")
                            try:
                                _update_job_from_worker(
                                    job_id,
                                    {
                                        "status": "failed",
                                        "error": str(e) + "\n" + traceback.format_exc(),
                                    },
                                )
                            except Exception:
                                logger.exception("Unable to persist failure for job %s", job_id)
                        finally:
                            heartbeat_task.cancel()

                    except httpx.HTTPStatusError as e:
                        if e.response.status_code == 400:
                            # Job might have been claimed by another worker
                            continue
                        logger.warning(f"Failed to claim job {job_id}: {e}")
                    except Exception as e:
                        logger.warning(f"Error processing job {job_id}: {e}")

        except Exception as e:
            logger.error(f"Error in job poller: {e}")
            await asyncio.sleep(5)

        await asyncio.sleep(5)


async def heartbeat_job(job_id: str):
    while True:
        try:
            await asyncio.sleep(30)
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"{REGISTRY_INTERNAL_URL}/ingestion-jobs/{job_id}/heartbeat"
                headers = _get_headers()
                headers["x-worker-id"] = WORKER_ID
                response = await client.post(url, headers=headers)
                response.raise_for_status()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Failed to send heartbeat for job {job_id}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    poller_task = asyncio.create_task(poll_jobs())
    yield
    poller_task.cancel()


app = FastAPI(title="RAG Ingestion Worker", lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/v1/dry-run/chunking")
def chunking_dry_run(body: ChunkingDryRunRequest):
    return dry_run_chunking(
        corpus_id=body.corpus_id,
        source_id=body.source_id,
        version_date=body.version_date,
        pipeline_id=body.pipeline_id,
        chunking_model=body.chunking_model,
        processor_id=body.processor_id,
        processor_config=body.processor_config,
        max_preview_chunks=body.max_preview_chunks,
    )


@app.post("/v1/purge/source", response_model=SourcePurgeResponse)
def purge_source(body: SourcePurgeRequest, _: str = Depends(require_internal_token)) -> SourcePurgeResponse:
    try:
        return SourcePurgeResponse(**purge_source_artifacts(body.corpus_id, body.source_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
