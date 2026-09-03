"""Build release image signature evidence for published TokenStream images."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DIGEST_PATTERN = re.compile(r"^sha256:[a-fA-F0-9]{64}$")


def _validate_digest(image_digest: str) -> None:
    if not DIGEST_PATTERN.fullmatch(image_digest):
        raise ValueError("image digest must be a sha256 digest in the form sha256:<64 hex characters>")


def _context() -> dict[str, str]:
    return {
        "release": os.environ.get("GITHUB_REF_NAME", ""),
        "repository": os.environ.get("GITHUB_REPOSITORY", ""),
        "commit": os.environ.get("GITHUB_SHA", ""),
        "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "event_name": os.environ.get("GITHUB_EVENT_NAME", ""),
    }


def build_entry(
    *,
    service_name: str,
    image_name: str,
    image_digest: str,
    signature_tool: str,
    signature_mode: str,
) -> dict[str, Any]:
    _validate_digest(image_digest)
    return {
        "product": "TokenStream",
        "inventory_type": "release-image-signature",
        "generated_at": datetime.now(UTC).isoformat(),
        **_context(),
        "image": {
            "service": service_name,
            "name": image_name,
            "digest": image_digest,
            "reference": f"{image_name}@{image_digest}",
        },
        "signature": {
            "tool": signature_tool,
            "mode": signature_mode,
            "registry_attached": True,
            "oidc_issuer": "https://token.actions.githubusercontent.com",
        },
    }


def build_inventory(entries: list[dict[str, Any]]) -> dict[str, Any]:
    signatures = sorted(
        (
            {
                "image": entry["image"],
                "signature": entry["signature"],
            }
            for entry in entries
            if isinstance(entry.get("image"), dict) and isinstance(entry.get("signature"), dict)
        ),
        key=lambda item: str(item["image"].get("service") or ""),
    )
    return {
        "product": "TokenStream",
        "inventory_type": "release-image-signatures",
        "generated_at": datetime.now(UTC).isoformat(),
        **_context(),
        "signature_count": len(signatures),
        "signatures": signatures,
    }


def _read_entries(input_dir: Path) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(input_dir.rglob("release-image-signature.*.json"))
    ]


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write release image signature evidence.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    entry = subparsers.add_parser("entry", help="Write one service image signature entry.")
    entry.add_argument("--service-name", required=True)
    entry.add_argument("--image-name", required=True)
    entry.add_argument("--image-digest", required=True)
    entry.add_argument("--signature-tool", default="cosign")
    entry.add_argument("--signature-mode", default="keyless")
    entry.add_argument("--output", required=True, type=Path)

    aggregate = subparsers.add_parser("aggregate", help="Aggregate service image signature entries.")
    aggregate.add_argument("--input-dir", required=True, type=Path)
    aggregate.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "entry":
        evidence = build_entry(
            service_name=args.service_name,
            image_name=args.image_name,
            image_digest=args.image_digest,
            signature_tool=args.signature_tool,
            signature_mode=args.signature_mode,
        )
    else:
        evidence = build_inventory(_read_entries(args.input_dir))
    _write_json(args.output, evidence)


if __name__ == "__main__":
    main()
