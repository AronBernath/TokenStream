# Planned Code Normalizations

To reduce drift between OpenAPI specifications and runtime behavior, the following changes are recommended.

## 1. TokenStream API Streaming Consistency

- **Issue**: The SSE streaming implementation in `orchestrator-api` should remain compatible with standard OpenAI client libraries.
- **Fix**: Ensure `finish_reason`, chunk deltas, tool-call chunks, and terminal `[DONE]` behavior match the OpenAI-compatible contract.

## 2. Management API Response Models

- **Issue**: Some management endpoints return large registry objects whose schemas can drift as fields are added.
- **Fix**: Keep explicit Pydantic response models for providers, policies, corpora, sources, jobs, machine keys, and RAG settings. Avoid `dict`-shaped responses where the UI or clients need stable structure.

## 3. Retrieval Error Envelopes

- **Issue**: Retrieval failures can come from registry lookup, missing indexes, Qdrant, SQLite, or embedder calls.
- **Fix**: Normalize retrieval errors into structured envelopes with stable codes such as `corpus_not_found`, `index_unavailable`, `embedder_error`, and `retrieval_backend_error`.

## 4. Ingestion Job Telemetry

- **Issue**: Ingestion jobs report useful stats, but the payload can grow organically.
- **Fix**: Define stable job-stat and chunk-preview schemas so the UI can display ingestion quality without relying on ad hoc JSON inspection.

## 5. Runtime Reload Reporting

- **Issue**: Runtime reloads can succeed partially if snapshots are missing or malformed.
- **Fix**: Return structured reload results for providers, policies, API keys, RAG settings, and MCP settings so operators can see exactly what changed.
