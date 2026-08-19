# Ingestion Worker

## Purpose
`ingestion-worker` is the service that turns raw corpus content into retrieval-ready data. It owns the full ingest pipeline: fetching content, parsing it, chunking it, generating embeddings, and writing the resulting indexes used by retrieval.

## Responsibilities
- Poll the registry for pending ingestion jobs.
- Load corpus configuration and source definitions from the registry.
- Fetch source material such as HTML and spreadsheets.
- Parse source material into structured blocks.
- Normalize and chunk parsed content.
- Generate embeddings for chunks.
- Write dense vectors to Qdrant.
- Write lexical and graph-aware retrieval data to SQLite-backed local storage.

## Key Entry Points
- `services/ingestion_worker/worker/server.py` implements the job polling loop.
- `services/ingestion_worker/worker/main.py` contains the ingest orchestration flow.
- `services/ingestion_worker/worker/fetchers.py` handles source acquisition.
- `services/ingestion_worker/worker/parsers.py` handles source-specific parsing.
- `services/ingestion_worker/worker/normalize.py` handles chunk generation.
- `services/ingestion_worker/worker/embed.py` handles embedding requests.
- `services/ingestion_worker/worker/indexers.py` writes data to Qdrant and SQLite.

## Inputs and Outputs
The main input is a pending ingestion job in the registry. The worker claims jobs, loads corpus and source definitions from the registry, and processes the selected sources. Browser administrators and service clients create those jobs through the management API; service clients authenticate with machine keys whose scopes include `corpora:write`.

Its outputs are the retrieval artifacts that the rest of the system depends on:
- vector data in Qdrant
- lexical index data in SQLite
- graph-related retrieval metadata in SQLite
- raw fetched source files under the data directory

These outputs are what make later retrieval requests possible.

## Dependencies
`ingestion-worker` depends on:
- Qdrant
- the embedding service
- local runtime storage under `data`
- selected modules from `common`
- optionally `orchestrator-api` for LLM-assisted chunking

This service is where the retrieval data plane is created. Without it, `retrieval-api` has nothing useful to query.

Processor configuration belongs to this service. The worker loads top-level processor records from a mounted snapshot such as `/runtime/processors.json` when present, or from the configured processor registry API as a fallback. Processor records are declarative: `type` selects a worker-owned adapter, and `config` customizes it. Client applications should publish processor config records, not host runtime callbacks required by ingestion.

## Runtime Behavior
The worker polls the registry for pending ingestion jobs. Browser and API callers create those jobs through the management API hosted by `dev-ui`; the worker does not own the human-facing corpus management surface.

Automation clients can poll `GET /v1/management/corpora/{corpus_id}/readiness` after creating an ingestion job. The readiness response is derived from registry state and flips to ready once the corpus has active sources and a completed ingestion job newer than the latest source update.

During execution, the pipeline typically follows this order:
1. Claim the pending job and load the target corpus configuration.
2. Select the relevant sources or documents (based on the load plan or selective filters).
3. Fetch and parse the source material.
4. Convert parsed blocks into chunks, stamping them with freshness metadata.
5. Generate embeddings for those chunks.
6. Delete old artifacts for the updated documents.
7. Upsert the new chunk data into Qdrant and SQLite-backed retrieval stores.

The worker also supports graph-oriented retrieval preparation by storing aliases, links, and lightweight structural relationships alongside the lexical data.

## Configuration
The most important settings are:
- `REGISTRY_INTERNAL_URL` and `CONFIG_AUTH_INTERNAL_TOKEN` for registry access
- `PROCESSOR_REGISTRY_PATH` for mounted processor records, normally `/runtime/processors.json`
- `PROCESSOR_REGISTRY_URL` for the API fallback, normally `http://dev-ui:8010/internal/processors`
- `DATA_DIR` for raw files and local retrieval data
- `LEXICAL_INDEX_DIR` for lexical and graph SQLite storage
- `QDRANT_URL` for vector upserts
- `EMBEDDER_URL` for embedding generation
- `ORCHESTRATOR_API_URL` and related auth settings when LLM chunking is enabled

These settings make the worker one of the most environment-sensitive services in the stack.

## Failure Modes and Operational Notes
The main operational caveat is that background job failures are mainly visible in logs. A caller can receive a successful acceptance response even if the later ingest work fails.

Partial ingest is also possible. Some sources may fail while others succeed, which means the worker can still produce usable retrieval data even when the ingest was not perfect.

Another important point is that the worker is responsible for producing both lexical and dense retrieval artifacts. If the worker is misconfigured, retrieval quality degrades across the whole ecosystem rather than in only one service.

## Related Services
- [`service_retrieval_api.md`](service_retrieval_api.md)
- [`service_orchestrator_api.md`](service_orchestrator_api.md)
- [`service_common.md`](service_common.md)
- [`retrieval_graph_rag.md`](retrieval_graph_rag.md)
- [`services_overview.md`](services_overview.md)
