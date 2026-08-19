# OpenAPI Verification Steps

To confirm the OpenAPI contracts are ready for client and pipeline use, perform the following checks.

## 1. Schema Validation

- Use a standard OpenAPI linter, such as `spectral` or `swagger-cli`, to ensure all YAML files are structurally valid OpenAPI documents.
- Verify that there are no broken `$ref` pointers.

## 2. Pipeline Simulation

- **Scenario A: Routed model call**: Read `openapi_orchestrator_api.yaml`. Can a client discover models, select a provider/model, submit `POST /v1/chat/completions`, and understand streaming vs non-streaming responses?
- **Scenario B: Retrieval-backed call**: Read `openapi_orchestrator_api.yaml` and `openapi_retrieval_api.yaml`. Can a client understand when to call `/v1/rag/query` versus `/v1/chat/completions` with retrieval tools?
- **Scenario C: Corpus ingestion**: Read `openapi_management_api.yaml` and the ingestion worker service docs. Can an operator create a corpus, add a source, create an ingestion job, and inspect dry-run chunking output?

## 3. Runtime Alignment Check

- Review planned code normalizations in `openapi_code_drift_fixes.md`.
- Ensure examples for error responses match the intended structured error shape.
- Ensure management examples match the actual registry/auth package models.

## 4. Documentation Integration

- Verify that `services_overview.md` links to the active YAML files.
- Verify that removed or external demo applications are not listed as core runtime services.
