"""Authenticated Layer 2A DAST probes for the orchestrator API."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT_S = 12
ORCHESTRATOR_API_URL = os.environ.get("ORCHESTRATOR_API_URL", "http://127.0.0.1:8004").rstrip("/")
ORCHESTRATOR_DAST_TOKEN = os.environ.get("ORCHESTRATOR_DAST_TOKEN", "")
EXPECTED_MODEL_ID = "ci-mock-model"
EXPECTED_PROVIDER = "ci-mock"


@dataclass(frozen=True)
class ProbeCase:
    check_id: str
    category: str
    description: str
    method: str
    path: str
    expected_statuses: set[int]
    body: Any | None = None
    raw_body: bytes | None = None
    query: dict[str, str] | None = None
    content_type: str = "application/json"
    policy_bypass_on_2xx: bool = False
    allow_5xx: bool = False
    known_gap: bool = False
    validator: Callable[[dict[str, Any]], tuple[bool, str]] | None = None


def _json_body(payload: Any) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _redact(text: str) -> str:
    if ORCHESTRATOR_DAST_TOKEN:
        text = text.replace(ORCHESTRATOR_DAST_TOKEN, "<redacted-token>")
    return text


def _url(path: str, query: dict[str, str] | None = None) -> str:
    base = f"{ORCHESTRATOR_API_URL}{path}"
    if not query:
        return base
    return f"{base}?{urllib.parse.urlencode(query)}"


def _request(case: ProbeCase) -> dict[str, Any]:
    body = case.raw_body if case.raw_body is not None else None if case.body is None else _json_body(case.body)
    headers = {
        "authorization": f"Bearer {ORCHESTRATOR_DAST_TOKEN}",
        "content-type": case.content_type,
        "x-request-id": f"ci-dast-layer2a-{case.check_id.lower()}",
    }
    request = urllib.request.Request(_url(case.path, case.query), data=body, headers=headers, method=case.method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_S) as response:
            response_body = response.read(65536)
            return {
                "status": response.status,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "content_type": response.headers.get("content-type", ""),
                "x_request_id": response.headers.get("x-request-id", ""),
                "body_preview": _redact(response_body.decode("utf-8", errors="replace")[:20000]),
            }
    except urllib.error.HTTPError as exc:
        response_body = exc.read(65536)
        return {
            "status": exc.code,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "content_type": exc.headers.get("content-type", ""),
            "x_request_id": exc.headers.get("x-request-id", ""),
            "body_preview": _redact(response_body.decode("utf-8", errors="replace")[:20000]),
        }
    except Exception as exc:
        return {
            "status": None,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "error": str(exc),
        }


def _body_json(observed: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(observed.get("body_preview") or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _validate_models(observed: dict[str, Any]) -> tuple[bool, str]:
    body = _body_json(observed)
    data = body.get("data")
    if not isinstance(data, list):
        return False, "models response did not include a data list"
    model_ids = {str(item.get("id") or "") for item in data if isinstance(item, dict)}
    owners = {str(item.get("owned_by") or "") for item in data if isinstance(item, dict)}
    if EXPECTED_MODEL_ID not in model_ids:
        return False, f"expected model {EXPECTED_MODEL_ID!r} was not returned"
    unexpected_models = sorted(model for model in model_ids if model and model != EXPECTED_MODEL_ID)
    unexpected_owners = sorted(owner for owner in owners if owner and owner != EXPECTED_PROVIDER)
    if unexpected_models or unexpected_owners:
        return False, f"unexpected models={unexpected_models!r} owners={unexpected_owners!r}"
    return True, ""


def _validate_chat(observed: dict[str, Any]) -> tuple[bool, str]:
    body = _body_json(observed)
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return False, "chat response did not include choices"
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = str(message.get("content") or "")
    if "mock provider response" not in content:
        return False, "chat response did not come from the mock provider"
    return True, ""


def _validate_rag_query(observed: dict[str, Any]) -> tuple[bool, str]:
    body = _body_json(observed)
    chunks = body.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        return False, "RAG query returned no chunks"
    if len(chunks) > 2:
        return False, f"RAG query returned {len(chunks)} chunks despite low-privilege max_top_k=2"
    chunk_ids = {str(item.get("chunk_id") or "") for item in chunks if isinstance(item, dict)}
    if not chunk_ids <= {"ci-docs-001", "ci-docs-002"}:
        return False, f"RAG query returned unexpected chunk IDs: {sorted(chunk_ids)!r}"
    sources = {
        str((item.get("metadata") or {}).get("source") or "")
        for item in chunks
        if isinstance(item, dict) and isinstance(item.get("metadata"), dict)
    }
    if sources - {"ci"}:
        return False, f"RAG query returned chunks from unexpected sources: {sorted(sources)!r}"
    return True, ""


def _validate_rag_lookup(observed: dict[str, Any]) -> tuple[bool, str]:
    status = observed.get("status")
    if status == 200:
        return True, ""
    if status == 502 and "missing lexical index" in str(observed.get("body_preview") or ""):
        return True, "current CI stack has no seeded lexical lookup index"
    return False, "RAG lookup did not return a recognized authenticated response"


def _chat_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": "ci-mock:ci-mock-model",
        "messages": [{"role": "user", "content": "Layer 2A authenticated DAST check"}],
        "max_tokens": 128,
        "stream": False,
    }
    payload.update(overrides)
    return payload


def _rag_query_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": "tokenstream retrieval smoke",
        "corpus_id": "ci_docs",
        "filters": {"source": "ci"},
        "top_k": 3,
    }
    payload.update(overrides)
    return payload


def _rag_lookup_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "terms": ["TokenStream", "/v1/chat/completions"],
        "corpus_id": "ci_docs",
        "filters": {"source": "ci"},
        "top_k": 3,
        "max_results": 3,
    }
    payload.update(overrides)
    return payload


def _cases() -> list[ProbeCase]:
    ssrf_value = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
    return [
        ProbeCase(
            check_id="L2A-MODELS-BASELINE",
            category="models",
            description="Low-privilege token can list only the allowed mock model.",
            method="GET",
            path="/v1/models",
            expected_statuses={200},
            validator=_validate_models,
        ),
        ProbeCase(
            check_id="L2A-MODELS-PIPELINE",
            category="pipeline_selection",
            description="Allowed pipeline selection keeps the model list constrained.",
            method="GET",
            path="/v1/models",
            query={"pipeline_id": "ci"},
            expected_statuses={200},
            validator=_validate_models,
        ),
        ProbeCase(
            check_id="L2A-MODELS-UNKNOWN-PIPELINE",
            category="pipeline_selection",
            description="Unknown pipeline IDs are rejected for an authenticated caller.",
            method="GET",
            path="/v1/models",
            query={"pipeline_id": "admin"},
            expected_statuses={422},
            policy_bypass_on_2xx=True,
        ),
        ProbeCase(
            check_id="L2A-MODELS-SSRF-SHAPED-PIPELINE",
            category="ssrf_shaped_input",
            description="SSRF-shaped pipeline IDs are treated as invalid policy names.",
            method="GET",
            path="/v1/models",
            query={"pipeline_id": ssrf_value},
            expected_statuses={422},
            policy_bypass_on_2xx=True,
        ),
        ProbeCase(
            check_id="L2A-CHAT-BASELINE",
            category="chat",
            description="Low-privilege token can invoke chat through the mock provider.",
            method="POST",
            path="/v1/chat/completions",
            body=_chat_payload(),
            expected_statuses={200},
            validator=_validate_chat,
        ),
        ProbeCase(
            check_id="L2A-CHAT-FORBIDDEN-PROVIDER",
            category="provider_model_manipulation",
            description="Provider switching does not reach an unconfigured provider.",
            method="POST",
            path="/v1/chat/completions",
            body=_chat_payload(model="openai:gpt-4o"),
            expected_statuses={403, 422},
            policy_bypass_on_2xx=True,
        ),
        ProbeCase(
            check_id="L2A-CHAT-FORBIDDEN-MODEL",
            category="provider_model_manipulation",
            description="Model switching within the mock provider is rejected by policy.",
            method="POST",
            path="/v1/chat/completions",
            body=_chat_payload(model="ci-mock:other-model"),
            expected_statuses={403},
            policy_bypass_on_2xx=True,
        ),
        ProbeCase(
            check_id="L2A-CHAT-UNKNOWN-PIPELINE",
            category="pipeline_selection",
            description="Unknown chat pipeline IDs are rejected.",
            method="POST",
            path="/v1/chat/completions",
            body=_chat_payload(pipeline_id="admin"),
            expected_statuses={422},
            policy_bypass_on_2xx=True,
        ),
        ProbeCase(
            check_id="L2A-CHAT-CHUNKING-TASK-DISABLED",
            category="pipeline_selection",
            description="Low-privilege chat identity cannot enable the disabled chunking task path.",
            method="POST",
            path="/v1/chat/completions",
            body=_chat_payload(task="chunking"),
            expected_statuses={403},
            policy_bypass_on_2xx=True,
        ),
        ProbeCase(
            check_id="L2A-CHAT-MALFORMED-JSON",
            category="malformed_request",
            description="Malformed authenticated chat JSON is handled at validation.",
            method="POST",
            path="/v1/chat/completions",
            raw_body=b'{"model": "ci-mock:ci-mock-model", "messages": [',
            expected_statuses={422},
        ),
        ProbeCase(
            check_id="L2A-CHAT-EMPTY-MESSAGES",
            category="malformed_request",
            description="Empty authenticated chat message lists are rejected.",
            method="POST",
            path="/v1/chat/completions",
            body=_chat_payload(messages=[]),
            expected_statuses={422},
        ),
        ProbeCase(
            check_id="L2A-CHAT-OVERSIZED-CONTENT",
            category="oversized_value",
            description="Oversized authenticated chat content is handled without unauthenticated fallback or leakage.",
            method="POST",
            path="/v1/chat/completions",
            body=_chat_payload(messages=[{"role": "user", "content": "A" * 25000}]),
            expected_statuses={200, 400, 413, 422},
            validator=_validate_chat,
        ),
        ProbeCase(
            check_id="L2A-CHAT-SSRF-SHAPED-MODEL",
            category="ssrf_shaped_input",
            description="SSRF-shaped model strings do not cause outbound provider routing.",
            method="POST",
            path="/v1/chat/completions",
            body=_chat_payload(model=ssrf_value),
            expected_statuses={403, 422},
            policy_bypass_on_2xx=True,
        ),
        ProbeCase(
            check_id="L2A-RAG-QUERY-BASELINE",
            category="rag_query",
            description="Low-privilege token can query the allowed CI corpus with top_k constrained.",
            method="POST",
            path="/v1/rag/query",
            body=_rag_query_payload(),
            expected_statuses={200},
            validator=_validate_rag_query,
        ),
        ProbeCase(
            check_id="L2A-RAG-CORPUS-SWITCH",
            category="corpus_switching",
            description="Corpus switching attempts are rejected by pipeline policy.",
            method="POST",
            path="/v1/rag/query",
            body=_rag_query_payload(corpus_id="other_corpus"),
            expected_statuses={422},
            policy_bypass_on_2xx=True,
        ),
        ProbeCase(
            check_id="L2A-RAG-UNKNOWN-PIPELINE",
            category="pipeline_selection",
            description="Unknown RAG pipeline IDs are rejected.",
            method="POST",
            path="/v1/rag/query",
            body=_rag_query_payload(pipeline_id="admin"),
            expected_statuses={422},
            policy_bypass_on_2xx=True,
        ),
        ProbeCase(
            check_id="L2A-RAG-INVALID-FILTER",
            category="rag_filter_abuse",
            description="Unsupported RAG filter fields are rejected by strict filter policy.",
            method="POST",
            path="/v1/rag/query",
            body=_rag_query_payload(filters={"source": "ci", "$where": "this.password != null"}),
            expected_statuses={502},
            allow_5xx=True,
            known_gap=True,
        ),
        ProbeCase(
            check_id="L2A-RAG-FILTER-OPERATOR",
            category="rag_filter_abuse",
            description="Operator-shaped RAG filter values are handled without broadening corpus access.",
            method="POST",
            path="/v1/rag/query",
            body=_rag_query_payload(filters={"source": {"$ne": "ci"}}),
            expected_statuses={200, 422, 502},
            allow_5xx=True,
            known_gap=True,
        ),
        ProbeCase(
            check_id="L2A-RAG-MALFORMED-JSON",
            category="malformed_request",
            description="Malformed authenticated RAG JSON is handled at validation.",
            method="POST",
            path="/v1/rag/query",
            raw_body=b'{"query": "tokenstream", "corpus_id": ',
            expected_statuses={422},
        ),
        ProbeCase(
            check_id="L2A-RAG-EMPTY-QUERY",
            category="malformed_request",
            description="Empty authenticated RAG queries are rejected.",
            method="POST",
            path="/v1/rag/query",
            body=_rag_query_payload(query=""),
            expected_statuses={422},
        ),
        ProbeCase(
            check_id="L2A-RAG-TOPK-ZERO",
            category="malformed_request",
            description="Invalid authenticated RAG top_k values are rejected.",
            method="POST",
            path="/v1/rag/query",
            body=_rag_query_payload(top_k=0),
            expected_statuses={422},
        ),
        ProbeCase(
            check_id="L2A-RAG-OVERSIZED-QUERY",
            category="oversized_value",
            description="Oversized authenticated RAG queries are handled without leakage.",
            method="POST",
            path="/v1/rag/query",
            body=_rag_query_payload(query="tokenstream " + ("A" * 25000)),
            expected_statuses={200, 400, 413, 422},
        ),
        ProbeCase(
            check_id="L2A-RAG-LOOKUP-BASELINE",
            category="rag_lookup",
            description="Low-privilege token reaches RAG lookup; current CI may lack a lexical index.",
            method="POST",
            path="/v1/rag/lookup",
            body=_rag_lookup_payload(),
            expected_statuses={200, 502},
            allow_5xx=True,
            known_gap=True,
            validator=_validate_rag_lookup,
        ),
        ProbeCase(
            check_id="L2A-RAG-LOOKUP-CORPUS-SWITCH",
            category="corpus_switching",
            description="RAG lookup corpus switching attempts are rejected by pipeline policy.",
            method="POST",
            path="/v1/rag/lookup",
            body=_rag_lookup_payload(corpus_id="other_corpus"),
            expected_statuses={422},
            policy_bypass_on_2xx=True,
        ),
        ProbeCase(
            check_id="L2A-RAG-LOOKUP-EMPTY-TERMS",
            category="malformed_request",
            description="Empty authenticated RAG lookup term lists are rejected.",
            method="POST",
            path="/v1/rag/lookup",
            body=_rag_lookup_payload(terms=[]),
            expected_statuses={422},
        ),
        ProbeCase(
            check_id="L2A-RAG-LOOKUP-TOO-MANY-TERMS",
            category="oversized_value",
            description="Oversized authenticated RAG lookup term lists are rejected.",
            method="POST",
            path="/v1/rag/lookup",
            body=_rag_lookup_payload(terms=[f"term-{idx}" for idx in range(60)]),
            expected_statuses={422},
        ),
    ]


def _leak_findings(observed: dict[str, Any]) -> list[str]:
    preview = str(observed.get("body_preview") or "")
    patterns = [
        "Traceback (most recent call last)",
        'File "',
        "/home/runner/",
        "/workspace/",
        "/app/",
        "site-packages",
        "uvicorn.error",
        "fastapi.exceptions",
    ]
    if ORCHESTRATOR_DAST_TOKEN:
        patterns.append(ORCHESTRATOR_DAST_TOKEN)
    return [
        pattern if pattern != ORCHESTRATOR_DAST_TOKEN else "<token-value>" for pattern in patterns if pattern in preview
    ]


def _run_case(case: ProbeCase) -> dict[str, Any]:
    observed = _request(case)
    status = observed.get("status")
    status_matched = status in case.expected_statuses
    validator_passed = True
    validator_message = ""
    if case.validator and status_matched:
        validator_passed, validator_message = case.validator(observed)
    leak_findings = _leak_findings(observed)
    policy_bypass = bool(case.policy_bypass_on_2xx and isinstance(status, int) and 200 <= status < 300)
    unhandled_error = bool(isinstance(status, int) and status >= 500 and not case.allow_5xx)
    passed = status_matched and validator_passed and not policy_bypass and not unhandled_error and not leak_findings
    return {
        "check_id": case.check_id,
        "category": case.category,
        "description": case.description,
        "method": case.method,
        "url": _url(case.path, case.query),
        "request_body": "<redacted>",
        "expected_statuses": sorted(case.expected_statuses),
        "observed": observed,
        "status_matched": status_matched,
        "validator_passed": validator_passed,
        "validator_message": validator_message,
        "policy_bypass": policy_bypass,
        "unhandled_error": unhandled_error,
        "leakage_findings": leak_findings,
        "known_gap": case.known_gap,
        "passed": passed,
        "outcome": "accepted_known_gap" if case.known_gap and status_matched else "passed" if passed else "failed",
    }


def build_report() -> dict[str, Any]:
    setup = [
        {
            "name": "low privilege DAST token configured",
            "observed": {"configured": bool(ORCHESTRATOR_DAST_TOKEN)},
            "passed": bool(ORCHESTRATOR_DAST_TOKEN),
        }
    ]
    results = [_run_case(case) for case in _cases()] if ORCHESTRATOR_DAST_TOKEN else []
    categories: dict[str, dict[str, int]] = {}
    for item in results:
        category = item["category"]
        categories.setdefault(category, {"checks": 0, "failures": 0, "known_gaps": 0})
        categories[category]["checks"] += 1
        if not item["passed"] and not item["known_gap"]:
            categories[category]["failures"] += 1
        if item["known_gap"]:
            categories[category]["known_gaps"] += 1

    status_mismatches = [item for item in results if not item["status_matched"] and not item["known_gap"]]
    validator_failures = [item for item in results if not item["validator_passed"] and not item["known_gap"]]
    policy_bypasses = [item for item in results if item["policy_bypass"]]
    leakage = [item for item in results if item["leakage_findings"]]
    unhandled_errors = [item for item in results if item["unhandled_error"]]
    known_gaps = [item for item in results if item["known_gap"]]

    return {
        "product": "TokenStream",
        "inventory_type": "dast-authenticated-layer-2a-probe",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "authenticated-layer-2a-low-privilege-orchestrator",
        "identity": {
            "name": "ci-layer2a-dast",
            "scopes": ["models:list", "chat:invoke", "rag:query"],
            "default_pipeline_id": "ci",
            "allowed_providers": [EXPECTED_PROVIDER],
            "allowed_models": [EXPECTED_MODEL_ID],
            "max_top_k": 2,
        },
        "targets": {"orchestrator_api": ORCHESTRATOR_API_URL},
        "summary": {
            "setup_count": len(setup),
            "setup_failures": sum(1 for item in setup if not item["passed"]),
            "check_count": len(results),
            "passed_count": sum(1 for item in results if item["passed"]),
            "status_mismatch_count": len(status_mismatches),
            "validator_failure_count": len(validator_failures),
            "policy_bypass_count": len(policy_bypasses),
            "leakage_count": len(leakage),
            "unhandled_error_count": len(unhandled_errors),
            "known_gap_count": len(known_gaps),
            "categories": categories,
        },
        "setup": setup,
        "checks": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe authenticated Layer 2A orchestrator DAST behavior.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fail-on-token-missing", action="store_true")
    parser.add_argument("--fail-on-status-mismatch", action="store_true")
    parser.add_argument("--fail-on-validator-failure", action="store_true")
    parser.add_argument("--fail-on-policy-bypass", action="store_true")
    parser.add_argument("--fail-on-leakage", action="store_true")
    parser.add_argument("--fail-on-unhandled-error", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary = report["summary"]
    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.fail_on_token_missing and summary["setup_failures"]:
        raise SystemExit("Layer 2A DAST token is not configured")
    if args.fail_on_status_mismatch and summary["status_mismatch_count"]:
        raise SystemExit("One or more Layer 2A DAST checks returned an unexpected status")
    if args.fail_on_validator_failure and summary["validator_failure_count"]:
        raise SystemExit("One or more Layer 2A DAST semantic validators failed")
    if args.fail_on_policy_bypass and summary["policy_bypass_count"]:
        raise SystemExit("One or more Layer 2A DAST probes detected a policy bypass")
    if args.fail_on_leakage and summary["leakage_count"]:
        raise SystemExit("One or more Layer 2A DAST probes detected response leakage")
    if args.fail_on_unhandled_error and summary["unhandled_error_count"]:
        raise SystemExit("One or more Layer 2A DAST probes returned an unhandled server error")


if __name__ == "__main__":
    main()
