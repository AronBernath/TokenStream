import sys
from pathlib import Path


COMMON_ROOT = Path(__file__).resolve().parent.parent / "services" / "common"
if str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

from common.retrieval_config import (  # noqa: E402
    build_citation,
    invalid_filter_fields,
    lexical_field_values,
    merge_default_filters,
)


def test_retrieval_config_merges_default_filters_and_validates_when_strict():
    corpus = {
        "retrieval_profile": {
            "retrieval_profile_id": "code.docs.v1",
            "config": {
                "default_filters": {"repo": "orchestrator", "commit_sha": "profile-sha"},
                "filterable_fields": ["repo", "commit_sha"],
                "citation_fields": ["path"],
                "strict_filters": False,
            },
        },
        "retrieval_config": {
            "default_filters": {"commit_sha": "abc123"},
            "filterable_fields": ["repo", "commit_sha", "source_kind"],
            "citation_fields": ["path", "start_line"],
            "strict_filters": True,
        },
    }

    filters = merge_default_filters(corpus, {"source_kind": "code"})

    assert filters == {"repo": "orchestrator", "commit_sha": "abc123", "source_kind": "code"}
    assert invalid_filter_fields(corpus, filters) == []
    assert invalid_filter_fields(corpus, {**filters, "path": "services/api"}) == ["path"]


def test_retrieval_config_enriches_citations_and_lexical_values_from_metadata():
    corpus = {
        "retrieval_config": {
            "citation_fields": ["path", "start_line", "end_line"],
            "lexical_fields": ["symbol", "path"],
        }
    }
    chunk = {
        "title": "create_ingestion_job",
        "section_id": "function:create_ingestion_job",
        "version_date": None,
        "source_url": "repo://orchestrator/abc/services/app/main.py#L10-L20",
        "metadata": {
            "path": "services/app/main.py",
            "symbol": "create_ingestion_job",
            "start_line": 10,
            "end_line": 20,
        },
    }

    citation = build_citation(chunk, corpus)

    assert citation["path"] == "services/app/main.py"
    assert citation["start_line"] == 10
    assert citation["end_line"] == 20
    assert lexical_field_values(corpus, chunk) == ["create_ingestion_job", "services/app/main.py"]
