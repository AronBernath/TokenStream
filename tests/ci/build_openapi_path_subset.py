"""Build a reduced OpenAPI document for scoped ZAP scans."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


TOKENSTREAM_CI_EXAMPLES: dict[str, dict[str, Any]] = {
    "/v1/chat/completions": {
        "model": "ci-mock-model",
        "messages": [{"role": "user", "content": "TokenStream active DAST mock-provider check"}],
        "pipeline_id": "ci",
        "temperature": 0,
        "max_tokens": 32,
    },
    "/v1/rag/query": {
        "query": "TokenStream active DAST retrieval check",
        "pipeline_id": "ci",
        "corpus_id": "ci_docs",
        "top_k": 1,
    },
    "/v1/rag/lookup": {
        "terms": ["TokenStream", "/v1/chat/completions"],
        "pipeline_id": "ci",
        "corpus_id": "ci_docs",
        "top_k": 1,
        "max_results": 3,
    },
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _request_body(operation: dict[str, Any]) -> dict[str, Any] | None:
    request_body = operation.get("requestBody")
    if isinstance(request_body, dict):
        return request_body
    return None


def _json_media_type(operation: dict[str, Any]) -> dict[str, Any] | None:
    request_body = _request_body(operation)
    if request_body is None:
        return None
    content = request_body.get("content")
    if not isinstance(content, dict):
        return None
    media_type = content.get("application/json")
    if isinstance(media_type, dict):
        return media_type
    return None


def _set_single_example(operation: dict[str, Any], value: dict[str, Any]) -> None:
    media_type = _json_media_type(operation)
    if media_type is None:
        return
    media_type.pop("example", None)
    media_type["examples"] = {"ciMock": {"value": value}}


def _apply_tokenstream_ci_examples(openapi: dict[str, Any]) -> None:
    paths = openapi.get("paths")
    if not isinstance(paths, dict):
        return
    for path, example in TOKENSTREAM_CI_EXAMPLES.items():
        path_item = paths.get(path)
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if isinstance(operation, dict):
                _set_single_example(operation, example)


def build_subset(
    openapi: dict[str, Any],
    *,
    paths: list[str],
    server_url: str | None,
    tokenstream_ci_examples: bool,
) -> dict[str, Any]:
    source_paths = openapi.get("paths")
    if not isinstance(source_paths, dict):
        raise ValueError("OpenAPI document does not contain a paths object")

    missing = [path for path in paths if path not in source_paths]
    if missing:
        raise ValueError(f"OpenAPI document is missing required path(s): {', '.join(missing)}")

    subset = deepcopy(openapi)
    subset["paths"] = {path: deepcopy(source_paths[path]) for path in paths}
    if server_url:
        subset["servers"] = [{"url": server_url}]
    if tokenstream_ci_examples:
        _apply_tokenstream_ci_examples(subset)
    subset.setdefault("info", {})["description"] = (
        str(subset.get("info", {}).get("description") or "").rstrip()
        + "\n\nReduced active DAST scope generated for TokenStream CI."
    ).strip()
    return subset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an OpenAPI path subset for scoped scanners.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--path", action="append", required=True, dest="paths")
    parser.add_argument("--server-url")
    parser.add_argument("--tokenstream-ci-examples", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    subset = build_subset(
        _load_json(args.input),
        paths=args.paths,
        server_url=args.server_url,
        tokenstream_ci_examples=args.tokenstream_ci_examples,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(subset, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
