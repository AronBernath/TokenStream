export type AttackSurfaceRow = {
  interface: string;
  endpointOrPort: string;
  actor: string;
  auth: string;
  exposedByDefault: string;
  trustBoundaryCrossed: string;
  sensitiveAssets: string;
  notes: string;
};

export const attackSurfaceRegister: AttackSurfaceRow[] = [
  {
    interface: "Orchestrator client API",
    endpointOrPort: "Host port 8004; GET /health; GET /v1/health; GET /v1/models; POST /v1/chat/completions; POST /v1/rag/query; POST /v1/rag/lookup",
    actor: "Client application backend; optional local chat UI",
    auth: "Bearer machine key when ORCHESTRATOR_API_KEYS_JSON, ORCHESTRATOR_API_KEYS_PATH, or ORCHESTRATOR_API_KEY is configured; scopes include models:list, chat:invoke, rag:query, tools:use",
    exposedByDefault: "Yes, docker-compose publishes 8004:8004",
    trustBoundaryCrossed: "External / user-controlled -> TokenStream self-hosted runtime",
    sensitiveAssets: "Machine API keys, prompts, chat messages, retrieved context, model choices, policy identifiers, tool results, generated responses",
    notes: "Primary application-facing surface. Treat unauthenticated local development mode as unsuitable for shared or public networks.",
  },
  {
    interface: "Orchestrator internal reload API",
    endpointOrPort: "Host port 8004; POST /v1/internal/reload",
    actor: "dev-ui / config_auth administrative control plane",
    auth: "ORCHESTRATOR_RELOAD_TOKEN",
    exposedByDefault: "Yes, because 8004 is published; endpoint is intended for internal control use",
    trustBoundaryCrossed: "Administrative control plane -> TokenStream runtime",
    sensitiveAssets: "Runtime snapshots, provider registry, policy registry, API key registry, RAG settings, MCP settings",
    notes: "Do not expose reload token to client applications. Network filtering is recommended even when token auth is configured.",
  },
  {
    interface: "Browser admin UI",
    endpointOrPort: "Host port 8010; /; /index.html; /admin; /admin/{path}",
    actor: "Human administrator / operator",
    auth: "Browser session after /v1/auth/login; local development bootstrap admin may be enabled by CONFIG_AUTH_DEV_BOOTSTRAP_ADMIN",
    exposedByDefault: "Yes, docker-compose publishes 8010:8010",
    trustBoundaryCrossed: "External / user-controlled -> Administrative control plane",
    sensitiveAssets: "Session cookie, admin account state, provider records, policies, corpora, sources, jobs, users, machine key creation flow",
    notes: "Administrative surface, not an end-user UI. Do not expose publicly with default or bootstrap credentials.",
  },
  {
    interface: "Management API",
    endpointOrPort: "Host port 8010; /v1/auth/*; /v1/management/* including providers, policies, processors, retrieval profiles, API keys, users, RAG settings, corpora, sources, source upload, ingestion jobs, MCP settings",
    actor: "Human administrator; tightly controlled admin automation",
    auth: "config_auth_session cookie for human admin flows; management authorization enforced by config_auth role/session logic",
    exposedByDefault: "Yes, docker-compose publishes 8010:8010",
    trustBoundaryCrossed: "External / user-controlled -> Administrative control plane",
    sensitiveAssets: "Admin sessions, user records, API key hashes and one-time plaintext key creation responses, provider secret references, policy allowlists, source metadata, uploaded files, runtime configuration",
    notes: "Highest-value inbound control-plane surface. Source upload and key creation paths require special scrutiny.",
  },
  {
    interface: "config_auth internal registry API",
    endpointOrPort: "Host port 8010 path prefix /internal; /internal/health; /internal/reload; /internal/corpora*; /internal/processors; /internal/retrieval-profiles; /internal/ingestion-jobs*",
    actor: "retrieval-api; ingestion-worker; internal registry clients",
    auth: "CONFIG_AUTH_INTERNAL_TOKEN via internal service headers",
    exposedByDefault: "Reachable on the published dev-ui port unless network policy blocks /internal from untrusted clients",
    trustBoundaryCrossed: "TokenStream runtime services -> Administrative control plane",
    sensitiveAssets: "Corpus registry, source registry, processors, retrieval profiles, ingestion job state, worker claim and heartbeat state",
    notes: "Internal-only by design. If 8010 is exposed beyond localhost/trusted network, /internal becomes a meaningful attack surface.",
  },
  {
    interface: "Retrieval API",
    endpointOrPort: "Host port 8000; GET /health; POST /v1/query; POST /v1/lookup; POST /query legacy alias; GET /corpora",
    actor: "orchestrator-api; trusted internal callers",
    auth: "No direct endpoint authentication visible in current retrieval-api implementation; deployment relies on trusted network placement and upstream orchestration",
    exposedByDefault: "Yes, docker-compose publishes 8000:8000",
    trustBoundaryCrossed: "TokenStream runtime -> RAG / indexing data plane; optionally external caller -> RAG / indexing data plane if port is reachable",
    sensitiveAssets: "Corpus IDs, filters, queries, retrieved chunks, citations, source metadata, lexical/vector/graph index content",
    notes: "Direct access should be an explicit deployment choice. Consider binding to internal network only or adding service auth before non-local use.",
  },
  {
    interface: "Ingestion worker operational API",
    endpointOrPort: "Container internal port 8002; GET /health; POST /v1/dry-run/chunking; POST /v1/purge/source",
    actor: "dev-ui management API; internal operators; ingestion worker clients",
    auth: "/v1/purge/source requires CONFIG_AUTH_INTERNAL_TOKEN; /v1/dry-run/chunking has no visible token dependency in current implementation",
    exposedByDefault: "No host port is published in docker-compose",
    trustBoundaryCrossed: "Administrative control plane -> RAG / indexing data plane",
    sensitiveAssets: "Corpus/source IDs, chunk previews, processor config, purge targets, index deletion results",
    notes: "Keep internal. Chunking dry-run can reveal source-derived content and processing behavior.",
  },
  {
    interface: "Source upload/fetch ingress",
    endpointOrPort: "Management path POST /v1/management/corpora/{corpus_id}/sources/upload on 8010; source fetch by ingestion-worker from URLs/files/repositories/archives",
    actor: "Human admin; corpus/source owner; ingestion-worker fetching external source systems",
    auth: "Admin session for management upload/registration; external fetch depends on configured source access",
    exposedByDefault: "Upload route is exposed with 8010; fetch egress depends on configured sources",
    trustBoundaryCrossed: "External / user-controlled -> Administrative control plane -> RAG / indexing data plane",
    sensitiveAssets: "Uploaded files, source URLs, corpus metadata, parsed text, chunks, embeddings, source provenance",
    notes: "Main untrusted-content ingress. Assess SSRF, malicious documents, archive handling, parser failures, provenance integrity, and storage quota abuse.",
  },
  {
    interface: "Qdrant vector store",
    endpointOrPort: "Host port 6333; QDRANT_URL=http://qdrant:6333",
    actor: "retrieval-api; ingestion-worker; any host/network client if exposed",
    auth: "No Qdrant auth configured in docker-compose",
    exposedByDefault: "Yes, docker-compose publishes 6333:6333",
    trustBoundaryCrossed: "RAG / indexing data plane -> data store; external caller -> data store if host port is reachable",
    sensitiveAssets: "Dense embeddings, vector metadata, collection names, corpus partitioning, retrieval index contents",
    notes: "Publishing Qdrant directly increases index tampering and data disclosure risk. Prefer internal-only exposure.",
  },
  {
    interface: "Object storage API",
    endpointOrPort: "Host port 9000; RAG_OBJECT_STORAGE_ENDPOINT=minio:9000",
    actor: "dev-ui/config_auth; ingestion-worker; object-storage clients",
    auth: "MINIO_ROOT_USER and MINIO_ROOT_PASSWORD",
    exposedByDefault: "Yes, docker-compose publishes 9000:9000",
    trustBoundaryCrossed: "Administrative/RAG runtime -> object storage; external caller -> object storage if host port is reachable",
    sensitiveAssets: "Uploaded source objects, fetched source snapshots, bucket contents, object metadata, MinIO root credentials",
    notes: "Avoid root credentials in application paths for production-like deployments. Limit network exposure and use scoped credentials where feasible.",
  },
  {
    interface: "Object storage console",
    endpointOrPort: "Host port 9001; MinIO console",
    actor: "Human operator",
    auth: "MINIO_ROOT_USER and MINIO_ROOT_PASSWORD",
    exposedByDefault: "Yes, docker-compose publishes 9001:9001",
    trustBoundaryCrossed: "External / user-controlled -> object storage administration",
    sensitiveAssets: "All source objects, buckets, access credentials, storage configuration",
    notes: "Administrative storage console. Should not be publicly reachable.",
  },
  {
    interface: "TEI embedder service",
    endpointOrPort: "Host port 8080 mapped to container port 80; EMBEDDER_URL=http://tei-embedder:80",
    actor: "ingestion-worker; any host/network client if exposed",
    auth: "No local service auth configured; optional HF_TOKEN only for model download/access to Hugging Face",
    exposedByDefault: "Yes, docker-compose publishes 8080:80",
    trustBoundaryCrossed: "RAG / indexing data plane -> embedding service; external caller -> embedding service if host port is reachable",
    sensitiveAssets: "Text chunks sent for embedding, embedding model cache, HF token environment value if mishandled",
    notes: "Direct exposure can leak submitted text through logs/telemetry and enable resource exhaustion. Prefer internal-only exposure.",
  },
  {
    interface: "External LLM provider egress",
    endpointOrPort: "Configured provider base URLs, for example https://api.deepseek.com, OpenAI-compatible APIs, Anthropic APIs, or local Ollama-style endpoints",
    actor: "orchestrator-api",
    auth: "Provider-specific API keys via env:// secret references or provider settings",
    exposedByDefault: "Outbound only; enabled when provider credentials/configuration are present",
    trustBoundaryCrossed: "TokenStream runtime -> External providers / tools",
    sensitiveAssets: "Provider API keys, prompts, retrieved context, tool results, model outputs, provider routing metadata",
    notes: "Primary confidentiality boundary for prompt/context leakage. Provider allowlists and policy restrictions should be reviewed per deployment.",
  },
  {
    interface: "MCP server/tool egress",
    endpointOrPort: "Configured MCP_SERVERS entries; streamable HTTP URL often /mcp; legacy SSE uses /sse and /messages",
    actor: "orchestrator-api; configured MCP servers/tools",
    auth: "MCP-server-specific authentication if configured; TokenStream policy allowlists decide tool availability",
    exposedByDefault: "Outbound only; no MCP server is enabled by default when MCP_SERVERS=[]",
    trustBoundaryCrossed: "TokenStream runtime -> External providers / tools",
    sensitiveAssets: "Tool schemas, tool arguments, tool results, prompts/context passed to tools, MCP credentials or bearer tokens",
    notes: "Treat each MCP tool as a delegated capability. Review tool side effects, SSRF/file/network access, and policy namespace isolation.",
  },
  {
    interface: "Runtime snapshot files",
    endpointOrPort: "Local volume /runtime; providers.json, api_keys.json, policies.json, rag_settings.json, mcp_servers.json, mcp_settings.json, processors.json, retrieval_profiles.json",
    actor: "config_auth; orchestrator-api; retrieval-api; ingestion-worker; host/container operator",
    auth: "Filesystem/container access controls; generated from authenticated management operations",
    exposedByDefault: "Mounted into runtime containers; not a network port",
    trustBoundaryCrossed: "Administrative control plane -> TokenStream runtime via shared volume",
    sensitiveAssets: "Provider configuration, policy configuration, API key hashes/material references, RAG and MCP settings, processors and retrieval profiles",
    notes: "Integrity-critical. Tampering can change routing, policy, tools, retrieval, and credential references without calling public APIs.",
  },
  {
    interface: "Local persistent data stores",
    endpointOrPort: "Volumes/data paths: config_auth_data, qdrant_data, minio_data, ./data, /data/lex, config_auth SQLite",
    actor: "TokenStream services; host/container operator; backup/restore processes",
    auth: "Filesystem/container access controls; database/service credentials where applicable",
    exposedByDefault: "Not a network interface, but mounted/persisted by default",
    trustBoundaryCrossed: "TokenStream runtime -> persistent storage",
    sensitiveAssets: "Config/auth database, source objects, lexical/graph indexes, vector indexes, ingestion artifacts, logs or operational traces",
    notes: "Include in backup, retention, encryption-at-rest, permissions, and incident-response review. OSS local deployments may vary widely.",
  },
  {
    interface: "Optional Open WebUI",
    endpointOrPort: "Host port 30000 mapped to container port 8080; OPENAI_API_BASE_URL=http://orchestrator-api:8004/v1",
    actor: "Human user/admin of optional local chat UI",
    auth: "WEBUI_AUTH=true; WEBUI_ADMIN_EMAIL and WEBUI_ADMIN_PASSWORD; Open WebUI uses ORCHESTRATOR_API_KEY toward TokenStream",
    exposedByDefault: "Yes, docker-compose publishes 30000:8080 when open-webui service is started",
    trustBoundaryCrossed: "External / user-controlled -> optional local client -> TokenStream runtime",
    sensitiveAssets: "Open WebUI credentials, prompts, chat history in open_webui_data_bootstrap, TokenStream machine key configured for the UI",
    notes: "Adjacent client surface, not TokenStream admin. Disable or isolate if not required for the assessed deployment.",
  },
];

const columns: Array<{ key: keyof AttackSurfaceRow; label: string }> = [
  { key: "interface", label: "interface" },
  { key: "endpointOrPort", label: "endpoint/port" },
  { key: "actor", label: "actor" },
  { key: "auth", label: "auth" },
  { key: "exposedByDefault", label: "exposed by default" },
  { key: "trustBoundaryCrossed", label: "trust boundary crossed" },
  { key: "sensitiveAssets", label: "sensitive assets" },
  { key: "notes", label: "notes" },
];

export function AttackSurfaceRegisterTable() {
  return (
    <table>
      <thead>
        <tr>
          {columns.map((column) => (
            <th key={column.key}>{column.label}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {attackSurfaceRegister.map((row) => (
          <tr key={row.interface}>
            {columns.map((column) => (
              <td key={column.key}>{row[column.key]}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default AttackSurfaceRegisterTable;
