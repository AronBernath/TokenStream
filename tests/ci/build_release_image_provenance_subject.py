"""Build release image provenance subject evidence for published images."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path


DIGEST_PATTERN = re.compile(r"^sha256:[a-fA-F0-9]{64}$")


def _validate_digest(image_digest: str) -> None:
    if not DIGEST_PATTERN.fullmatch(image_digest):
        raise ValueError("image digest must be a sha256 digest in the form sha256:<64 hex characters>")


def build_evidence(
    *,
    service_name: str,
    image_name: str,
    image_digest: str,
    attestation_action: str,
) -> dict[str, object]:
    _validate_digest(image_digest)
    return {
        "product": "TokenStream",
        "inventory_type": "release-image-provenance-subject",
        "generated_at": datetime.now(UTC).isoformat(),
        "release": os.environ.get("GITHUB_REF_NAME", ""),
        "repository": os.environ.get("GITHUB_REPOSITORY", ""),
        "commit": os.environ.get("GITHUB_SHA", ""),
        "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "event_name": os.environ.get("GITHUB_EVENT_NAME", ""),
        "image": {
            "service": service_name,
            "name": image_name,
            "digest": image_digest,
            "reference": f"{image_name}@{image_digest}",
        },
        "attestation": {
            "type": "slsa-build-provenance",
            "action": attestation_action,
            "pushed_to_registry": True,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write release image provenance subject evidence.")
    parser.add_argument("--service-name", required=True)
    parser.add_argument("--image-name", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--attestation-action", default="actions/attest@v4")
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    evidence = build_evidence(
        service_name=args.service_name,
        image_name=args.image_name,
        image_digest=args.image_digest,
        attestation_action=args.attestation_action,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
