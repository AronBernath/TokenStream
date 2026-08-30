# TokenStream

TokenStream is an open-source AI orchestration runtime for applications that need governed model access, MCP tool use, retrieval-augmented generation, corpus ingestion, and source-backed context behind a single API.

It is designed to sit behind your application, not replace it. Your product owns the user experience and domain logic; TokenStream provides the shared runtime for model routing, tool orchestration, policy enforcement, ingestion, retrieval, and operational configuration.

## TokenStream Overview

Modern AI applications often need more than a direct call to one model provider. They need approved tools, retrieval over private knowledge, provider failover or routing, scoped API keys, auditable policies, and a way to keep source-backed context fresh.

TokenStream brings those pieces into one local-first stack:

- OpenAI-compatible chat and model endpoints
- MCP-backed tool orchestration
- retrieval as a governed tool in model workflows
- provider and model routing
- policy enforcement for models, tools, corpora, filters, and retrieval limits
- machine API keys for service-to-service access
- corpus creation, source registration, upload, ingestion, and readiness checks
- hybrid lexical, vector, and graph-aware retrieval
- source metadata and citation-ready retrieval results
- browser admin UI for runtime configuration
- GitHub Actions release and image publishing workflows

The public product name is TokenStream. Some internal service names, environment variables, and paths still use `orchestrator` as an implementation identifier.

## Use Cases

### Tool Use And Workflow Orchestration

Use TokenStream when your application needs a controlled backend for model access, approved MCP tools, retrieval tools, and policy-bound workflows.

Example scenarios:

- an internal operations assistant that can use Grafana, n8n, and retrieval tools
- a support workflow that can search knowledge and call approved actions
- a developer assistant that can combine repository context with internal automation
- a product assistant that routes different workflows to different providers and models

TokenStream lets the application call one OpenAI-compatible API while the runtime handles provider selection, policy checks, enabled tools, retrieval access, and MCP integration.

### Knowledge Creation And Retrieval

Use TokenStream when your application needs to attach source-backed knowledge to AI workflows.

Example scenarios:

- customer support knowledge retrieval
- product documentation and API reference assistants
- codebase and architecture assistants
- policy, procedure, and compliance search
- tenant-scoped or workflow-scoped knowledge bases

TokenStream can ingest sources, chunk and embed content, build retrieval indexes, and return context with metadata that your application can display as citations or use in prompts.

## Quick Start

### Prerequisites

- Docker and Docker Compose
- At least one model provider API key, unless using only local providers
- A Hugging Face token only when using gated embedding models

### Configure Environment

Copy the example environment file:

```bash
cp .env.example .env
```

Then replace every `replace-me` value with local credentials. Do not commit `.env` or any `.env.*` file.

Generate local service secrets with a cryptographically strong generator:

```bash
openssl rand -hex 32
```

Common values to review:

```env
OPENAI_API_KEY=replace-me
DEEPSEEK_API_KEY=replace-me
ORCHESTRATOR_API_KEY=replace-me-generated-secret
CONFIG_AUTH_INTERNAL_TOKEN=replace-me-generated-secret
ORCHESTRATOR_RELOAD_TOKEN=replace-me-generated-secret
MINIO_ROOT_PASSWORD=replace-me-generated-secret
WEBUI_ADMIN_PASSWORD=replace-me-generated-secret
```

### Start TokenStream

```bash
docker compose up -d
```

The first startup may take a few minutes while images and embedding models are downloaded.

Check the main services:

```bash
curl http://localhost:8004/health
curl http://localhost:8000/health
```

Open the admin UI:

```text
http://localhost:8010
```

### Main Local Services

| Service | Port | Role |
| --- | --- | --- |
| `dev-ui` | `8010` | Browser admin UI and management API |
| `orchestrator-api` | `8004` | OpenAI-compatible orchestration API |
| `retrieval-api` | `8000` | Corpus-scoped retrieval API |
| `ingestion-worker` | internal | Background source processing and indexing |
| `qdrant` | `6333` | Vector storage |
| `minio` | `9000`, `9001` | Object storage for uploaded sources |
| `open-webui` | `30000` | Optional local chat UI |

## Features

### AI Orchestration

TokenStream exposes an OpenAI-compatible API for application-facing model requests. It can resolve providers and models from request data or policy defaults, enforce allowed providers and models, and coordinate retrieval or MCP tools when enabled.

### MCP Tool Support

MCP servers can be configured as runtime tools. Policies decide which MCP tools are available to a workflow, so client applications do not need direct access to every tool server.

### Retrieval As A Tool

Retrieval can be used directly through structured RAG endpoints or indirectly as a tool inside an orchestration workflow. This lets an application combine model reasoning, source-backed context, and approved tools behind one runtime boundary.

### Providers And Policies

Provider records describe upstream model backends, base URLs, capabilities, models, and secret references. Policy records define what a workflow can use: providers, models, tools, corpora, filters, token limits, and retrieval limits.

### Corpora And Sources

A corpus is a retrieval boundary for one application, tenant, workflow, or knowledge domain. Sources can be registered or uploaded under a corpus, then processed by ingestion jobs.

### Ingestion

The ingestion worker fetches sources, parses content, chunks documents, generates embeddings, and writes retrieval artifacts. Processor configuration can customize how source snapshots are interpreted.

### Hybrid Retrieval

Retrieval uses a combination of lexical search, vector search, and graph-aware expansion. This supports both semantic questions and exact lookups over paths, symbols, routes, configuration keys, or other metadata.

### Citation-Ready Metadata

Retrieval results include source metadata that applications can use for citations, review flows, or grounded answer displays. Citation quality depends on the metadata emitted during ingestion.

### Admin UI

The browser admin UI manages providers, policies, corpora, sources, ingestion jobs, users, machine keys, MCP servers, and runtime settings.

### Release Automation

The repository includes GitHub Actions for CI, secret scanning, release preparation, and container image publishing. Releases are driven by Conventional Commits and Release Please.

## Usage Examples

The examples below show the shape of common integrations. Replace API keys, provider names, model names, corpus IDs, and tool names with values from your deployment.

### Example 1: Tool Use And Workflow Orchestration

Create or configure a policy that allows the tools and models your workflow can use:

```json
{
  "ops-assistant": {
    "default_corpus_id": "operations",
    "allowed_corpus_ids": ["operations"],
    "default_filters": {},
    "allowed_tools": ["rag", "mcp__grafana", "mcp__n8n-mcp"],
    "allowed_providers": ["openai", "deepseek"],
    "allowed_models": ["openai:gpt-5.5", "deepseek:deepseek-v4-pro"],
    "default_provider": "openai",
    "default_model": "gpt-5.5",
    "max_top_k": 8
  }
}
```

Then call TokenStream from your application through the OpenAI-compatible API:

```bash
export TOKENSTREAM_API_KEY="replace-me"

curl http://localhost:8004/v1/chat/completions \
  -H "Authorization: Bearer $TOKENSTREAM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai:gpt-5.5",
    "pipeline_id": "ops-assistant",
    "messages": [
      {
        "role": "user",
        "content": "Check the current incident context and summarize the safest next action."
      }
    ]
  }'
```

In this pattern, your application does not need direct access to Grafana, n8n, retrieval infrastructure, or provider credentials. TokenStream becomes the policy-controlled runtime boundary.

### Example 2: Knowledge Creation And Retrieval

Create a corpus for one application knowledge boundary:

```bash
export CONFIG_AUTH_TOKEN="replace-me"

curl -X PUT http://localhost:8010/v1/management/corpora/product_docs/ensure \
  -H "Authorization: Bearer $CONFIG_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Product Documentation"
  }'
```

Register a source:

```bash
curl -X POST http://localhost:8010/v1/management/corpora/product_docs/sources \
  -H "Authorization: Bearer $CONFIG_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "docs-home",
    "type": "url",
    "format": "html",
    "url": "https://example.com/docs"
  }'
```

Start ingestion:

```bash
curl -X POST http://localhost:8010/v1/management/corpora/product_docs/ingestion-jobs \
  -H "Authorization: Bearer $CONFIG_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "force_reembed": false
  }'
```

Wait for readiness:

```bash
curl http://localhost:8010/v1/management/corpora/product_docs/readiness \
  -H "Authorization: Bearer $CONFIG_AUTH_TOKEN"
```

Query the corpus through TokenStream:

```bash
export TOKENSTREAM_API_KEY="replace-me"

curl http://localhost:8004/v1/rag/query \
  -H "Authorization: Bearer $TOKENSTREAM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How should a client application configure authentication?",
    "pipeline_id": "default",
    "corpus_id": "product_docs",
    "top_k": 6
  }'
```

The response includes ranked context and metadata that your application can use for prompts, citations, search results, or review workflows.

## Documentation

Full user-facing documentation is served with Mintlify:

```text
https://tokenstream.mintlify.site
```

The Mintlify source lives under `docs/`. The docs entry point is `docs/docs.json`, with pages organized under:

- `docs/overview/`
- `docs/quickstart/`
- `docs/concepts/`
- `docs/guides/`
- `docs/reference/`
- `docs/troubleshooting/`
- `docs/review/`

Internal service documentation, OpenAPI contracts, CRA evidence files, and example corpus material also live under `docs/`.

To connect hosting, create or open the TokenStream project in the Mintlify dashboard, install the Mintlify GitHub App from the dashboard, and point the project at this repository with `docs/` as the documentation directory. Mintlify deploys automatically after pushes to the connected branch and provides pull request previews when the GitHub App is installed.

## Development

Run Python checks:

```bash
python -m ruff check .
python -m ruff format --check .
```

Run Python tests:

```bash
PYTHONPATH=services/common:services/ingestion_worker:services/retrieval_api \
  python -m pytest -m "not integration" \
  tests \
  packages/config_auth/tests \
  services/ingestion_worker/tests \
  services/dev-ui/tests \
  services/retrieval_api/tests

PYTHONPATH=services/common:services/orchestrator_api \
  python -m pytest -m "not integration" services/orchestrator_api/tests
```

Run frontend checks:

```bash
npm --prefix services/dev-ui/frontend ci
npm --prefix services/dev-ui/frontend run lint
npm --prefix services/dev-ui/frontend run typecheck
npm --prefix services/dev-ui/frontend run test
npm --prefix services/dev-ui/frontend audit --audit-level=high --omit=dev
```

## Versioning And Releases

TokenStream uses Semantic Versioning with release automation driven by Conventional Commits.

- `fix:` commits produce patch releases.
- `feat:` commits produce minor releases.
- `feat!:`, `fix!:`, or a `BREAKING CHANGE:` footer produce major releases.

When releasable commits land on `main`, the Release Please workflow opens or updates a release pull request. Merging that release PR updates `CHANGELOG.md`, updates `version.txt`, creates a `vMAJOR.MINOR.PATCH` tag, and publishes a GitHub Release.

Published releases build service images and push them to container registries with SemVer tags such as `1.2.3`, `1.2`, `1`, `latest`, and `sha-<commit>`.

## License

This project is licensed under the terms in `LICENSE`.
