export type ViewId =
  | "dashboard"
  | "providers"
  | "policies"
  | "keys"
  | "users"
  | "corpora"
  | "corpora/details"
  | "rag"
  | "mcp";

export type ToastKind = "info" | "success" | "error";

export type Toast = {
  message: string;
  kind: ToastKind;
};

export type Session = {
  username: string;
  roles?: string[];
  permissions?: string[];
  must_rotate_password?: boolean;
};

export type ViewProps = {
  refreshNonce: number;
  notify: (message: string, kind?: ToastKind) => void;
};

export type CorpusDetail = {
  corpus_id?: string;
  title?: string | null;
  description?: string | null;
  environment?: string | null;
  tenant_id?: string | null;
  chunking?: unknown;
  index?: unknown;
  metadata?: unknown;
  sources?: CorpusSource[];
};

export type CorpusSource = {
  id: string;
  type?: string;
  format?: string;
  title?: string | null;
  url?: string | null;
  object_uri?: string | null;
  content_hash?: string | null;
  size_bytes?: number | null;
  content_type?: string | null;
  tags?: string[];
  configuration?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
};

export type CorpusRegistryBundle = {
  schema_version?: string;
  exported_at?: string | null;
  corpus: CorpusDetail;
  notes?: string[];
};

export type CorpusRegistryImportResult = {
  status: string;
  corpus_id: string;
  sources_imported?: number;
  conflict_strategy?: string;
  notes?: string[];
};

export type IngestionJob = {
  job_id?: string;
  corpus_id?: string;
  status?: string;
  request?: Record<string, unknown>;
  plan?: Record<string, unknown>;
  stats?: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
  error?: string | null;
};

export type ProviderRecord = {
  name: string;
  default_model?: string;
  models?: string[];
  capabilities?: {
    chunking?: boolean;
    json_schema?: boolean;
    tools?: boolean;
    streaming?: boolean;
  };
};

export type PolicyRecord = {
  pipeline_id: string;
  chunking?: {
    enabled?: boolean;
    default_provider?: string | null;
    default_model?: string | null;
    allowed_providers?: string[] | null;
    allowed_models?: string[] | null;
  } & Record<string, unknown>;
};

export type ModelOption = {
  id: string;
  provider: string;
  model: string;
};
