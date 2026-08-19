# Prioritized Endpoints for OpenAPI Hardening

Based on likely client and pipeline usage, the following endpoints are prioritized for OpenAPI hardening.

## Tier 1: Runtime Orchestration

- **`orchestrator-api`**
  - `POST /v1/chat/completions`: OpenAI-compatible chat interface, including policy resolution, structured output, tool use, and streaming behavior.
  - `GET /v1/models`: Model discovery for configured providers.
  - `POST /v1/rag/query`: Structured retrieval path for clients that want deterministic corpus retrieval through the TokenStream API boundary.

## Tier 2: Corpus Retrieval

- **`retrieval-api`**
  - `POST /v1/query`: Base corpus-scoped retrieval endpoint.
  - `GET /v1/corpora`: Corpus discovery for trusted internal clients.

## Tier 3: Management And Registry

- **`dev-ui` management API**
  - `GET /v1/management/providers`
  - `PUT /v1/management/providers`
  - `GET /v1/management/policies`
  - `PUT /v1/management/policies`
  - `PUT /v1/management/corpora/{corpus_id}/ensure`
  - `POST /v1/management/corpora`
  - `POST /v1/management/corpora/{corpus_id}/sources`
  - `POST /v1/management/corpora/{corpus_id}/ingestion-jobs`
  - `GET /v1/management/corpora/{corpus_id}/readiness`

## Tier 4: Background Worker APIs

- **`ingestion-worker`**
  - `GET /health`
  - `POST /v1/dry-run/chunking`

Worker polling and job-claim endpoints are internal registry routes and should remain documented as internal control-plane APIs, not public client APIs.
