# Dev UI

## Purpose
`dev-ui` is the browser-facing admin interface for TokenStream. It provides a thin browser shell for human login, RBAC-aware administration, and configuration editing without exposing backend service topology directly to the browser.

## Responsibilities
- Serve the built React web interface.
- Host the embedded registry/auth package for human login, RBAC, and admin CRUD.
- Proxy browser API calls to `orchestrator-api` and `ingestion-api`.
- Keep browser-facing routing simple by hiding backend URL details.
- Provide the admin surface for providers, policies, machine API keys, users, RAG settings, and corpus resource management.

## Key Entry Points
- `services/dev-ui/main.py` defines the FastAPI application, proxy routes, and static-file behavior.
- `services/dev-ui/frontend` contains the React application. The runtime service serves only the built `frontend/dist` assets.

## Inputs and Outputs
The main inputs are browser requests to the UI itself and browser requests to `/api/*`.

The outputs are:
- HTML, JavaScript, and other static assets for the UI
- embedded auth/admin API responses and proxied responses from other internal services

This service does not create application-specific logic or retrieval logic on its own. It presents and forwards.

## Dependencies
`dev-ui` depends directly on:
- the embedded `config_auth` Python package (for human auth, RBAC, and admin CRUD)
- `orchestrator-api` (for runtime orchestration)
- `ingestion-api` (for ingestion configuration)
- FastAPI, Starlette static-file support, and `httpx`

It does not talk directly to `retrieval-api` or Qdrant.

## Runtime Behavior
The service serves the UI shell at `/`.

The browser authenticates as a human user against registry/auth routes hosted directly by `dev-ui` (`/v1/auth/*` and `/v1/management/*`). The resulting session cookie stays same-origin and does not need a separate service hop.

## Configuration
The most important settings are:
- `CONFIG_AUTH_DB_PATH`
- `CONFIG_AUTH_RUNTIME_DIR`
- `CONFIG_AUTH_DEV_BOOTSTRAP_ADMIN`
- `ORCHESTRATOR_API_URL`
- `ORCHESTRATOR_RELOAD_URL`
- `ORCHESTRATOR_RELOAD_TOKEN`

These settings define where the UI forwards requests and how patient it should be when waiting for backend responses.

## Failure Modes and Operational Notes
If the React build is missing, `dev-ui` fails startup with a clear error instead of falling back to the old prototype HTML shell.

If the embedded registry/auth layer fails to initialize, startup fails because the admin UI cannot operate without it.

Another useful detail is that the UI is still intentionally thin at the browser layer. Most behavior that looks like UI functionality is powered by the embedded registry/auth package and `orchestrator-api`. That means backend documentation is often more important than frontend documentation when diagnosing user-facing issues.

Corpus resource management in the UI currently assumes a trusted admin environment. It can add URL-backed resources and upload local files into the mounted corpora tree, but it does not yet enforce deeper security controls such as malware scanning, domain allowlisting, or content provenance checks.

## Related Services
- [`package_config_auth.md`](package_config_auth.md)
- [`service_orchestrator_api.md`](service_orchestrator_api.md)
- [`services_overview.md`](services_overview.md)
