# Retrieval API

## Purpose
`retrieval-api` is the service that answers corpus-scoped retrieval queries. It combines lexical search, dense vector search, graph-aware expansion, and optional reranking so downstream services can ask for grounded context instead of talking directly to the raw indexes.

## Responsibilities
- Expose a versioned retrieval contract.
- Validate retrieval requests against shared models.
- Search Qdrant for dense matches.
- Search SQLite-backed indexes for lexical and reference-style matches.
- Expand candidate sets with graph-aware retrieval logic.
- Optionally rerank candidate chunks before returning the final result set.
- Return portable retrieval results that other services can cite and reuse.

## Key Entry Points
- `services/retrieval_api/app/main.py` defines the FastAPI app and public endpoints.
- `services/retrieval_api/app/hybrid_retrieval.py` contains the core hybrid retrieval flow.
- `services/retrieval_api/app/qdrant_client.py` handles vector search behavior.
- `services/retrieval_api/app/sqlite_fts_client.py` handles lexical and SQLite-backed retrieval.
- `services/retrieval_api/app/reranker.py` handles optional reranking.
- `docs/retrieval_api_contract.md` documents the versioned API contract.

## Inputs and Outputs
The main input is `POST /v1/query` with the shared `QueryRequest` shape:
- `query`
- `corpus_id`
- optional `filters`
- optional `top_k`

The main output is a shared `QueryResponse` containing:
- `api_version`
- `answer`
- `citations`
- `chunks`

This response shape is important because it is meant to travel cleanly across service boundaries.

## Dependencies
`retrieval-api` depends on:
- Qdrant for dense retrieval
- local SQLite-backed lexical and graph data produced by `ingestion-worker`
- the embedding service for query embeddings
- shared models and retrieval helpers from `common`

It does not own ingestion and does not own application-specific reasoning logic. Its job is retrieval only.

Retrieval profile configuration belongs to this service. The API loads top-level retrieval profile records from a mounted snapshot such as `/runtime/retrieval_profiles.json` when present, or from the configured retrieval profile registry API as a fallback. Retrieval profiles are declarative: `type` selects a retrieval-api-owned retrieval behavior, and `config` customizes filters, citations, and profile-level retrieval defaults.

## Runtime Behavior
For each query, the service checks that the target corpus exists, builds a candidate pool from multiple retrieval signals, expands candidates with graph-aware logic, and optionally reranks the bounded result set before returning the final chunks.

This is why `retrieval-api` sits at the center of the retrieval pipeline. It hides the internal complexity of combining vector, lexical, and graph signals behind one stable HTTP contract.

The service also exposes a legacy `/query` alias, but the canonical path is `/v1/query`.

## Configuration
The most important settings are:
- `QDRANT_URL`
- `EMBEDDER_URL`
- `LEXICAL_INDEX_DIR`
- `RETRIEVAL_PROFILE_REGISTRY_PATH`, normally `/runtime/retrieval_profiles.json`
- `RETRIEVAL_PROFILE_REGISTRY_URL`, normally `http://dev-ui:8010/internal/retrieval-profiles`
- retrieval pool-size settings such as `RETRIEVAL_SEED_POOL_K`, `RETRIEVAL_GRAPH_POOL_K`, and `RETRIEVAL_RERANK_POOL_K`
- reranker-related settings when reranking is enabled. Reranking is disabled by default and its heavyweight dependencies are not part of the base image. Build the `reranker` Docker target if local reranking is needed.

The service also reads corpus-listing settings when serving the `/corpora` endpoint.

## Failure Modes and Operational Notes
The most important contract rule is that retrieval is corpus-scoped. If the requested corpus does not exist, the service returns a structured `404` instead of silently crossing corpus boundaries.

Reranking is a helpful but non-fundamental step. If reranking fails, the service can still fall back to the earlier candidate-ranking stages and return a usable response.

Because this service depends on data created by `ingestion-worker`, retrieval quality and even retrieval availability can be affected by ingest failures or stale indexes.

## Related Services
- [`service_ingestion_worker.md`](service_ingestion_worker.md)
- [`service_orchestrator_api.md`](service_orchestrator_api.md)
- [`service_common.md`](service_common.md)
- [`retrieval_graph_rag.md`](retrieval_graph_rag.md)
- [`services_overview.md`](services_overview.md)
