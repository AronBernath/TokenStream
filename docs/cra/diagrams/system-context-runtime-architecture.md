# Diagram 1: System Context / Runtime Architecture

This diagram is a high-level system context and runtime architecture view for the TokenStream CRA risk assessment. Its purpose is to identify what the system is, who interacts with it, which major runtime components exist inside the TokenStream boundary, and which external systems are relevant to cybersecurity risk.

```mermaid
flowchart LR
  subgraph U["External / user-controlled context"]
    EndUser["Client application end user<br/>(no direct TokenStream access expected)"]
    Client["Client application backend<br/>(machine API consumer)"]
    Admin["Human administrator / operator"]
    SourceOwner["Corpus / source owner"]
    SourceSystems["Corpus source systems<br/>(URLs, files, repositories, archives)"]
    OpenWebUI["Optional local chat UI<br/>(for example Open WebUI)"]
  end

  subgraph TS["TokenStream self-hosted runtime boundary"]
    UI["dev-ui<br/>(browser admin surface)"]
    Auth["config_auth<br/>(users, RBAC, keys, registries)"]
    Orch["orchestrator-api<br/>(policy-aware LLM and tool router)"]
    Ret["retrieval-api<br/>(corpus-scoped retrieval)"]
    Worker["ingestion-worker<br/>(source processing and indexing)"]
    Snap[("runtime snapshots<br/>(providers, policies, API keys, RAG, MCP)")]
    ConfigDB[("config_auth SQLite<br/>(users, sessions, registry, audit)")]
    Obj[("object storage / MinIO<br/>(uploaded and fetched sources)")]
    Qdrant[("Qdrant vector store")]
    Lex[("SQLite lexical / graph indexes")]
    Embed["TEI embedder<br/>(embedding service)"]
  end

  subgraph EXT["External providers / tools"]
    LLM["LLM providers<br/>(OpenAI-compatible, DeepSeek, Anthropic, local)"]
    MCP["MCP servers / tools"]
  end

  EndUser -. "uses client product UI" .-> Client
  Client -->|"chat, model, RAG requests<br/>Authorization: Bearer machine key"| Orch
  OpenWebUI -->|"local chat API calls"| Orch
  Admin -->|"admin session<br/>management actions"| UI
  SourceOwner -->|"register / upload sources"| UI
  SourceSystems -->|"source bytes / URLs / archives"| Worker

  UI -->|"auth and management API"| Auth
  Auth -->|"persist users, keys, corpora, jobs, audit"| ConfigDB
  Auth -->|"export runtime configuration"| Snap
  Orch -->|"load / reload runtime configuration"| Snap
  Worker -->|"read processors, jobs, corpora"| Auth
  Ret -->|"read corpus and retrieval profiles"| Auth

  Orch -->|"structured retrieval / retrieval tool"| Ret
  Ret -->|"query vectors"| Qdrant
  Ret -->|"query lexical and graph indexes"| Lex

  Worker -->|"store source objects"| Obj
  Worker -->|"read source objects"| Obj
  Worker -->|"embed chunks"| Embed
  Worker -->|"write vectors"| Qdrant
  Worker -->|"write lexical and graph indexes"| Lex

  Orch -->|"provider request / response"| LLM
  Orch -->|"allowed tool calls"| MCP
```

## Interpretation

This diagram supports the product and environment description in the CRA risk assessment. It identifies:

- external actors and user-controlled systems;
- the TokenStream self-hosted runtime boundary;
- main TokenStream runtime services;
- security-relevant data stores and generated runtime configuration;
- external LLM providers and MCP/tool integrations;
- high-level runtime, administration, retrieval, and ingestion flows.

## Scope Notes

This diagram is intentionally not the detailed STRIDE threat model. It does not attempt to enumerate individual threats, protocol-level mitigations, vulnerability handling controls, or every attack surface entry. Those should be covered by the STRIDE Data Flow / Trust Boundary diagram and by the modelled attack-surface register.

The optional local chat UI is shown as an external or adjacent client surface because it is not the administrative control plane. In the CRA risk assessment, direct user access to `dev-ui` should still be treated as unintended outside trusted administration.
