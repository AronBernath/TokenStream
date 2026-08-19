import time
import logging
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from common.models import ErrorResponse, LookupRequest, QueryRequest, QueryResponse
from .hybrid_retrieval import (
    hybrid_query_with_metrics,
    lexical_lookup_with_metrics,
    CorpusNotFoundError,
    InvalidFiltersError,
    RetrievalConfigurationError,
)
from .logging_config import configure_logging

configure_logging()

app = FastAPI(
    title="RAG Retrieval API",
    version="1.0.0",
    description=(
        "Versioning policy: additive changes only within /v1. "
        "Any breaking change must be introduced under /v2 (or a later major path)."
    ),
)
logger = logging.getLogger("retrieval-api")


@app.on_event("startup")
async def _configure_logging() -> None:
    configure_logging()


def _error_payload(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {"error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    return payload


@app.get("/health", summary="Liveness probe")
def health():
    return {"ok": True}


@app.post(
    "/v1/query",
    response_model=QueryResponse,
    response_model_exclude_none=True,
    summary="Versioned retrieval endpoint",
    responses={
        404: {"model": ErrorResponse, "description": "Unknown corpus_id"},
        422: {"description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Unhandled server error"},
    },
)
async def query_v1(req: QueryRequest):
    started = time.perf_counter()
    try:
        response, counts = await hybrid_query_with_metrics(req)
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "query corpus_id=%s top_k=%s filters=%s vector_hits=%s lexical_hits=%s exact_hits=%s graph_hits=%s reranked_candidates=%s returned_chunks=%s latency_ms=%s",
            req.corpus_id,
            req.top_k,
            sorted((req.filters or {}).keys()),
            counts["vector_hits"],
            counts["lexical_hits"],
            counts.get("exact_hits", 0),
            counts.get("graph_hits", 0),
            counts.get("reranked_candidates", 0),
            counts["returned_chunks"],
            latency_ms,
        )
        return response
    except CorpusNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=_error_payload(
                code="corpus_not_found",
                message="Unknown corpus_id",
                details={"corpus_id": exc.corpus_id},
            ),
        )
    except InvalidFiltersError as exc:
        raise HTTPException(
            status_code=422,
            detail=_error_payload(
                code="invalid_filters",
                message="Unsupported filter fields for this corpus",
                details={"fields": exc.fields},
            ),
        )
    except RetrievalConfigurationError as exc:
        raise HTTPException(
            status_code=500,
            detail=_error_payload(
                code="retrieval_configuration_error",
                message=str(exc),
            ),
        )


@app.post(
    "/v1/lookup",
    response_model=QueryResponse,
    response_model_exclude_none=True,
    summary="Versioned lexical/exact lookup endpoint",
    responses={
        404: {"model": ErrorResponse, "description": "Unknown corpus_id or missing lexical index"},
        422: {"description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Unhandled server error"},
    },
)
async def lookup_v1(req: LookupRequest):
    started = time.perf_counter()
    try:
        response, counts = await lexical_lookup_with_metrics(req)
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "lookup corpus_id=%s terms=%s top_k=%s max_results=%s filters=%s lexical_hits=%s field_hits=%s exact_hits=%s returned_chunks=%s latency_ms=%s",
            req.corpus_id,
            len(req.terms),
            req.top_k,
            req.max_results,
            sorted((req.filters or {}).keys()),
            counts["lexical_hits"],
            counts.get("field_hits", 0),
            counts.get("exact_hits", 0),
            counts["returned_chunks"],
            latency_ms,
        )
        return response
    except CorpusNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=_error_payload(
                code="corpus_not_found",
                message="Unknown corpus_id or missing lexical index",
                details={"corpus_id": exc.corpus_id},
            ),
        )
    except InvalidFiltersError as exc:
        raise HTTPException(
            status_code=422,
            detail=_error_payload(
                code="invalid_filters",
                message="Unsupported filter fields for this corpus",
                details={"fields": exc.fields},
            ),
        )
    except RetrievalConfigurationError as exc:
        raise HTTPException(
            status_code=500,
            detail=_error_payload(
                code="retrieval_configuration_error",
                message=str(exc),
            ),
        )


@app.post(
    "/query",
    response_model=QueryResponse,
    response_model_exclude_none=True,
    deprecated=True,
    summary="Legacy alias of /v1/query",
)
async def query_legacy(req: QueryRequest):
    return await query_v1(req)


from .registry_client import list_corpora


def _list_corpora() -> list[str]:
    try:
        return list_corpora()
    except Exception as e:
        logger.warning(f"Failed to list corpora from registry: {e}")
        return []


@app.get("/corpora")
def corpora():
    return {"corpora": _list_corpora()}


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(
            code="http_error",
            message=str(exc.detail),
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_, exc: Exception):
    logger.exception("Unhandled exception during request")
    return JSONResponse(
        status_code=500,
        content=_error_payload(
            code="internal_error",
            message="Internal server error",
        ),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.errors(),
        },
    )
