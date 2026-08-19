# TokenStream API

## Purpose
`orchestrator-api` is the LLM-facing coordination service inside TokenStream. It presents an OpenAI-compatible interface, routes requests to configured model providers, and can combine model calls with retrieval and tool execution.

## Responsibilities
- Expose OpenAI-compatible chat and model endpoints.
- Route requests to the selected LLM provider.
- Enforce pipeline rules such as default corpora, filters, and tool allowlists.
- Call `retrieval-api` when RAG-backed behavior is needed.
- Coordinate MCP-backed tools when they are enabled.
- Validate structured response formats for clients that need stricter output.

Client-owned prompts, provider choices, and model choices stay with the calling application. This service should enforce generic request policy, not inject application-specific behavior into requests.

## Key Entry Points
- `services/orchestrator_api/app/main.py` defines the FastAPI app and the main request flow.
- `services/orchestrator_api/app/config.py` defines runtime settings.
- `services/orchestrator_api/app/pipeline.py` contains pipeline registry and policy logic.
- `services/orchestrator_api/app/rag_tools.py` contains retrieval tool integration.
- `services/orchestrator_api/app/provider_registry.py` defines provider registration and selection.
- `services/orchestrator_api/app/mcp/registry.py` contains MCP tool integration.

## Inputs and Outputs
The main input is OpenAI-style chat-completion traffic, plus a dedicated RAG query route used when callers want structured retrieval behavior.

Its outputs are:
- OpenAI-compatible chat responses
- streaming chat responses when requested
- structured retrieval responses forwarded from `retrieval-api`
- tool-driven model outputs when retrieval or MCP tools are used inside a chat flow

This makes `orchestrator-api` the service that translates between general LLM interaction and the rest of the RAG ecosystem.

## Dependencies
`orchestrator-api` depends on:
- external or internal LLM providers such as OpenAI-compatible backends, DeepSeek, or Anthropic
- `retrieval-api` for RAG-backed behavior
- the embedded registry/auth package in `dev-ui` for runtime snapshots of providers, machine API keys, policies, and RAG defaults
- `common` for shared LLM types, provider abstractions, and retrieval contracts
- optional MCP servers for external tool integration

It does not store indexes and does not own application-specific reasoning rules.

## Runtime Behavior
At startup, the service loads settings, builds its provider registry, loads any configured pipeline registry, and initializes MCP tooling if present.

In the current segmented design, the runtime configuration is supplied through snapshot files exported by the registry/auth package hosted in `dev-ui` rather than through the old JSON admin store. That package can trigger `/v1/internal/reload` so the service refreshes its in-memory registries without a full restart.

At request time, it determines which provider and model to use, resolves pipeline context, optionally calls retrieval or tools, and then returns an OpenAI-compatible response. Provider/model resolution is intentionally fail-closed: the request must provide them explicitly, or a selected policy must provide both `default_provider` and `default_model`. In other words, it is the service that makes retrieval and tool usage feel like part of one model interaction rather than separate backend calls.

## Configuration
The most important settings are:
- `LLM_PROVIDERS_JSON` or `LLM_PROVIDERS_PATH` for declarative provider definitions. In the stack compose, this now points at the shared runtime snapshot exported by `dev-ui`; legacy environment variables are still supported as fallbacks.
- `ORCHESTRATOR_API_KEYS_JSON` or `ORCHESTRATOR_API_KEYS_PATH` for scoped API-key registries.
- `ORCHESTRATOR_PIPELINE_REGISTRY_PATH`
- `RAG_SETTINGS_PATH`
- `ORCHESTRATOR_RELOAD_TOKEN` for authenticated reload calls from the embedded config/auth admin surface
- `RETRIEVAL_API_URL`
- `DEFAULT_CORPUS_ID` and `DEFAULT_TOP_K`
- pipeline-registry settings for policy control (corpora, tools, models, token limits)
- MCP-related settings such as `MCP_SERVERS`, `MCP_TIMEOUT_S`, `MCP_STRICT`, and `MCP_MAX_TOOL_ROUNDS`

Because it sits between clients and upstream providers, `orchestrator-api` has one of the broadest configuration surfaces in the stack.

The runtime no longer relies on a stack-wide default provider fallback for request routing. Provider definitions describe what is available; selected policies may describe what is allowed and what defaults apply; otherwise requests must be explicit.

## Admin Ownership
`orchestrator-api` is no longer the system of record for human users, provider secret management, or admin CRUD.

- **Human auth and RBAC** live in the embedded registry/auth package within `dev-ui`.
- **Machine API keys** are still enforced here at runtime, but they are provisioned by that admin component.
- **Provider secrets** are referenced indirectly via `secret_ref` fields in runtime snapshots rather than stored here as plaintext admin config.
- **Dynamic reload** is available through `/v1/internal/reload`, guarded by `ORCHESTRATOR_RELOAD_TOKEN`.

## Failure Modes and Operational Notes
Provider misconfiguration, invalid pipeline definitions, or strict MCP startup errors can prevent the service from starting correctly.

At runtime, failures can come from the upstream model provider, the retrieval service, or tool execution. The service maps those failures into API-friendly responses, but operators should still think of it as an integration boundary where several upstream systems can fail independently.

This service is also the main place where request policy is applied. A documentation reader should understand that allowed corpora, filters, and tools may be shaped here rather than in the UI or in `retrieval-api`.

## Related Services
- [`service_retrieval_api.md`](service_retrieval_api.md)
- [`package_config_auth.md`](package_config_auth.md)
- [`service_ingestion_worker.md`](service_ingestion_worker.md)
- [`service_common.md`](service_common.md)
- [`services_overview.md`](services_overview.md)
