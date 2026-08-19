# Registry/Auth Package

## Purpose
`config_auth` is the embedded registry/auth package for TokenStream administration. It is hosted inside the `dev-ui` service, separates human users from machine API keys, persists admin-managed configuration in SQLite, manages corpus resource metadata, and exports runtime snapshots for `orchestrator-api`.

## Responsibilities
- Authenticate human admin users with username/password.
- Enforce role-based access control for admin operations.
- Manage machine API keys separately from human users.
- Authenticate machine API keys on `/v1/management` routes and enforce their scopes as management permissions.
- Store providers, policies, processors, retrieval profiles, users, API keys, and RAG settings in SQLite.
- Validate and mutate corpus resource lists, storing uploaded source bytes in S3-compatible object storage.
- Provide an idempotent corpus lifecycle path for service clients to ensure corpora, register or upload sources, create ingestion jobs, and poll readiness.
- Export and import config-auth corpus registry bundles for migration between application instances.
- Export runtime snapshot files consumed by runtime services.
- Trigger TokenStream runtime reloads after configuration changes.

## Key Entry Points
- `packages/config_auth/app/main.py` defines the FastAPI app, `/v1/management` endpoints, and `/internal` endpoints.
- `packages/config_auth/app/db.py` contains the SQLite schema and repository logic.
- `packages/config_auth/app/security.py` provides password hashing, session token handling, and machine key hashing.
- `packages/config_auth/app/models.py` defines the request/response models and RBAC permissions.

## Inputs and Outputs
The main inputs are:
- browser login and management requests from `dev-ui` via `/v1/management`
- service-client management requests authenticated with `Authorization: Bearer <machine-key>`
- internal service-to-service requests via `/internal`
- internal configuration mutations for providers, policies, users, API keys, RAG settings, corpora, corpus sources, and ingestion jobs

The outputs are:
- authenticated session cookies for human admins
- RBAC-filtered management API responses
- internal API responses for worker job claims and registry reads
- config-auth corpus registry export/import bundles
- runtime snapshot JSON files for `orchestrator-api`, `ingestion-worker`, and `retrieval-api`
- best-effort reload notifications to `orchestrator-api`

## Dependencies
`config_auth` depends directly on:
- SQLite for lightweight persistence
- `argon2-cffi` for password hashing
- `httpx` for runtime reload notifications
- MinIO/S3-compatible object storage for uploaded source bytes

It is not deployed as a separate container; `dev-ui` imports and runs the package in-process.

It is designed so provider secrets can move cleanly to Vault later by storing `secret_ref` values rather than plaintext credentials.

## Runtime Behavior
At startup, the service ensures its schema, seeds RBAC roles and permissions, optionally bootstraps the dev-only `admin` account, imports any configured bootstrap provider, policy, processor, and retrieval profile files into the admin store, exports runtime snapshots, and attempts to notify `orchestrator-api` to reload.

At request time, it authenticates the session cookie, enforces permissions such as `providers:write` or `users:read`, writes changes to SQLite, refreshes the exported runtime snapshot files, and then triggers a runtime reload.

Machine API keys created by the management API can also call `/v1/management` endpoints with a Bearer token. The key scopes are treated as permissions for those requests. For example, an external corpus manager needs `corpora:write` to call `PUT /v1/management/corpora/{corpus_id}/ensure`, create or update sources, upload source bytes, and create ingestion jobs. It needs `corpora:read` to list corpora, inspect corpus detail, poll ingestion jobs, and call `GET /v1/management/corpora/{corpus_id}/readiness`. A key without the required scope receives `403` with the missing permission named in the error.

`PUT /v1/management/corpora/{corpus_id}/ensure` is the idempotent corpus creation path for automation. If the corpus already exists, the existing active record is returned unchanged. If the corpus is missing, it is created from the request body.

`GET /v1/management/corpora/{corpus_id}/readiness` reports whether retrieval should be available from registry state. A corpus is ready when it has at least one active source and the latest completed ingestion job is newer than the latest source update. Pending or running jobs keep the readiness status in `pending` or `running`; missing completed jobs, no sources, failed jobs, and source changes after completion are reported as reasons.

Corpus registry export/import is intentionally registry-scoped. It moves corpus metadata and active source records, including `s3://` object URIs and content hashes for uploaded sources. It does not copy object bytes, Qdrant vectors, lexical SQLite indexes, or generated chunk artifacts; after import, the target instance should be able to reach the referenced object storage and should re-ingest the corpus.

## Configuration
The most important settings are:
- `CONFIG_AUTH_DB_PATH`
- `CONFIG_AUTH_RUNTIME_DIR`
- `CONFIG_AUTH_BOOTSTRAP_PROVIDERS_PATH`
- `CONFIG_AUTH_BOOTSTRAP_POLICIES_PATH`
- `CONFIG_AUTH_BOOTSTRAP_PROCESSORS_PATH`
- `CONFIG_AUTH_BOOTSTRAP_RETRIEVAL_PROFILES_PATH`
- `RAG_OBJECT_STORAGE_ENDPOINT`
- `RAG_OBJECT_STORAGE_ACCESS_KEY`
- `RAG_OBJECT_STORAGE_SECRET_KEY`
- `RAG_OBJECT_STORAGE_BUCKET`
- `RAG_OBJECT_STORAGE_SECURE`
- `CONFIG_AUTH_SESSION_COOKIE_NAME`
- `CONFIG_AUTH_SESSION_COOKIE_SECURE`
- `CONFIG_AUTH_DEV_BOOTSTRAP_ADMIN`
- `ORCHESTRATOR_RELOAD_URL`
- `ORCHESTRATOR_RELOAD_TOKEN`

## Security Notes
- Human passwords are hashed with Argon2id.
- Machine API keys are hashed with `scrypt`.
- User password hashes are never returned by the admin API.
- Provider secrets are represented as `secret_ref` values like `env://...`, `docker://...`, or future `vault://...`.
- The bootstrap `admin:admin` account is intended for local/dev only and should be rotated immediately.
- Corpus resource management currently performs only basic reachability and format-shape validation for uploaded files and URLs. Future hardening should add stronger checks such as antivirus scanning, MIME sniffing defenses, URL allowlists/denylists, content-size limits, and tighter filesystem isolation before this feature is considered production-grade.

## Related Services
- [`service_dev_ui.md`](service_dev_ui.md)
- [`service_orchestrator_api.md`](service_orchestrator_api.md)
- [`services_overview.md`](services_overview.md)
