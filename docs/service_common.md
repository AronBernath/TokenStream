# Common

## Purpose

`common` is a shared Python package used by the runtime services. It is not a standalone service. Its role is to keep contracts and helper behavior consistent across ingestion, retrieval, and orchestration.

## Responsibilities

- Define shared retrieval request and response models.
- Provide LLM-backed chunking helpers.
- Provide bearer-token and tenant helper functions.
- Provide shared LLM provider abstractions.
- Provide object-storage helpers.
- Provide retrieval graph normalization helpers.

## Key Entry Points

- `services/common/common/models.py` contains retrieval contracts such as `QueryRequest`, `QueryResponse`, and `RetrievedChunk`.
- `services/common/common/chunking.py` contains LLM-assisted chunking support.
- `services/common/common/auth.py` contains bearer-token and tenant helpers.
- `services/common/common/object_storage.py` contains S3-compatible object-storage helpers.
- `services/common/common/retrieval_graph.py` contains graph-oriented retrieval helpers such as alias extraction.
- `services/common/common/llm/` contains shared provider types, errors, and provider implementations.

## Inputs and Outputs

This package does not expose HTTP endpoints. Its inputs and outputs are Python functions, types, and models consumed in-process by other services.

The most important output is a shared contract boundary. Retrieval callers and retrieval providers rely on the same `QueryRequest` and `QueryResponse` models instead of maintaining local copies.

## Dependencies

`common` depends on the Python runtime and the libraries required by whichever module is imported. In practice, its real dependency boundary is the caller: services import only the pieces they need.

It is used most directly by:

- `ingestion-worker`
- `retrieval-api`
- `orchestrator-api`
- `dev-ui` through the embedded registry/auth package

## Runtime Behavior

`common` has no lifecycle of its own. It runs inside the process of the service that imports it.

That means its behavior is only visible through the services that use it:

- chunking behavior appears during ingestion
- retrieval contracts appear at API boundaries
- LLM provider abstractions appear inside `orchestrator-api`

## Related Services

- [`service_ingestion_worker.md`](service_ingestion_worker.md)
- [`service_retrieval_api.md`](service_retrieval_api.md)
- [`service_orchestrator_api.md`](service_orchestrator_api.md)
- [`services_overview.md`](services_overview.md)
