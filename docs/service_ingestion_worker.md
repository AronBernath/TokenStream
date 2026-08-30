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

### Ingestion, Chunking, And Embedding Configuration Ownership

Ingestion behavior is intentionally split across job request fields, registry records, pipeline policy, and environment variables. There is no single config object that controls the entire ingest path.

#### Ingestion Job Request

An ingestion job controls the current run. The management API accepts fields such as:

- `pipeline_id`: policy id used when the worker asks `orchestrator-api` to perform LLM chunking. If omitted, the worker uses `INGESTION_PIPELINE_ID`, defaulting to `default`.
- `source_ids`: optional selective ingest list. If omitted, the job can process the whole corpus.
- `force_reembed`: bypasses unchanged-source skipping and regenerates chunks and embeddings.
- `processor_id`: optional run-level processor override.
- `processor_config`: optional run-level processor configuration override.
- `configuration.chunking_model`: optional selected LLM model for chunking. The browser UI stores the selected chunking model here when submitting a corpus load.

The worker receives those fields from the claimed job and passes them into the ingest pipeline.

#### Processor Selection And Processor Config

Processor configuration defines how a source is parsed or converted before embeddings are generated. Processor records are top-level registry objects with a stable `processor_id`, adapter `type`, and reusable `config`.

Processor selection is resolved in this order:

1. Ingestion job `processor_id`
2. Source `processor_id`
3. Corpus `processor_id`
4. `default`

Processor configuration is merged in this order, with later layers overriding earlier layers:

1. Top-level processor record `config`
2. Corpus `processor_config`
3. Source `processor_config`
4. Ingestion job `processor_config`

The built-in `default` or `generic` processor uses the generic parser followed by LLM chunking. The built-in `structured_archive` processor uses `processor_config` values such as `include`, `exclude`, `max_file_bytes`, `max_files`, `max_chunk_chars`, and `metadata_defaults`.

#### Corpus Chunking Config

Corpus `chunking` controls chunk shape for the generic processor. The active fields are:

- `strategy`: currently only `llm` is supported.
- `target_chars`: target chunk size, defaulting to `2200`.
- `overlap_chars`: overlap guidance, defaulting to `250`.
- `model`: optional fallback chunking model when no job-level `configuration.chunking_model` and no `pipeline_id` are supplied.

This config determines when parsed structural blocks are packed directly and when oversized blocks are sent to the LLM chunking helper.

#### Pipeline Chunking Policy

Pipeline policy `chunking` is different from corpus `chunking`. It does not define source parsing or embedding. It defines whether `orchestrator-api` may serve internal `task=chunking` requests and which providers/models are allowed or default for those requests.

Typical policy fields are:

- `enabled`
- `default_provider`
- `default_model`
- `allowed_providers`
- `allowed_models`

When the worker calls `orchestrator-api` for LLM chunking, the request includes `task=chunking` and the selected `pipeline_id`. The orchestrator then enforces the matching pipeline chunking policy and provider chunking capability.

#### Embedding Config

Embedding is configured through the embedding service and worker environment, not through processor config or corpus chunking.

The worker uses:

- `EMBEDDER_URL`: embedding service endpoint.
- `EMBED_BATCH_SIZE`: number of chunk texts sent per embedding batch, defaulting to `6`.
- `EMBED_TIMEOUT_SECONDS`: HTTP timeout for embedding calls, defaulting to `600`.
- `EMBED_MAX_CHARS`: maximum characters per text sent to the embedder, defaulting to `8000`.

In the local compose stack, `EMBEDDER_URL` points to `tei-embedder`, and the embedding model is selected by the TEI container's `MODEL_ID`, for example `intfloat/multilingual-e5-small`.

In short:

- Job request fields decide what to ingest now and which run-level overrides apply.
- Processor config decides how source material is parsed or adapted.
- Corpus `chunking` decides chunk shape for the generic processor.
- Pipeline `chunking` decides which LLM route may be used for chunking.
- Embedder environment decides how chunk text becomes vectors.

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
