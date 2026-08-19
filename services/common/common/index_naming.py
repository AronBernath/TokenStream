import os
from pathlib import Path


def canonical_corpus_scope(environment: str, tenant_id: str, corpus_id: str) -> str:
    """Returns a canonical scope string for a corpus."""
    env = (environment or os.environ.get("DEFAULT_ENVIRONMENT", "default-env")).strip()
    tenant = (tenant_id or os.environ.get("DEFAULT_TENANT_ID", "default-tenant")).strip()
    return f"corp_{env}_{tenant}_{corpus_id}"


def qdrant_collection_name(environment: str, tenant_id: str, corpus_id: str) -> str:
    """Returns the Qdrant collection name for a corpus."""
    return canonical_corpus_scope(environment, tenant_id, corpus_id)


def lexical_index_path(environment: str, tenant_id: str, corpus_id: str, data_dir: str = "/data") -> str:
    """Returns the path to the SQLite lexical index for a corpus."""
    scope = canonical_corpus_scope(environment, tenant_id, corpus_id)
    return str(Path(data_dir) / "lexical" / f"{scope}.db")
