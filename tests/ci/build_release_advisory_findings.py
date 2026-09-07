"""Build deduplicated release advisory findings from normalized scanner output."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SEVERITY_ORDER = {
    "Unknown": 0,
    "Negligible": 1,
    "Low": 2,
    "Medium": 3,
    "High": 4,
    "Critical": 5,
}
SEVERITIES = ("Critical", "High", "Medium", "Low", "Negligible", "Unknown")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    return sorted({str(item) for item in _as_list(value) if item})


def _severity(value: Any) -> str:
    severity = str(value or "Unknown")
    return severity if severity in SEVERITY_ORDER else "Unknown"


def _max_severity(values: list[str]) -> str:
    return max(values or ["Unknown"], key=lambda severity: SEVERITY_ORDER.get(severity, 0))


def _component_key(component: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(component.get("purl") or ""),
        str(component.get("name") or ""),
        str(component.get("version") or ""),
    )


def _group_key(finding: dict[str, Any]) -> tuple[str, str, str, str]:
    component = _as_dict(finding.get("component"))
    return (str(finding.get("id") or ""), *_component_key(component))


def _target_sort_key(target: dict[str, Any]) -> tuple[int, str, str]:
    kind = str(target.get("kind") or "")
    return (0 if kind == "source-tree" else 1, kind, str(target.get("name") or ""))


def _read_findings(input_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    input_files = sorted(input_dir.rglob("release-normalized-findings*.json"))
    findings: list[dict[str, Any]] = []
    for path in input_files:
        report = json.loads(path.read_text(encoding="utf-8"))
        findings.extend(
            finding for finding in _as_list(report.get("findings")) if isinstance(finding, dict) and finding.get("id")
        )
    return findings, [str(path) for path in input_files]


def _release_images(digest_inventory: dict[str, Any]) -> list[dict[str, str]]:
    images = []
    for image in _as_list(digest_inventory.get("images")):
        if not isinstance(image, dict):
            continue
        images.append(
            {
                "service": str(image.get("service") or ""),
                "name": str(image.get("name") or ""),
                "digest": str(image.get("digest") or ""),
                "reference": str(image.get("reference") or ""),
            }
        )
    return sorted(images, key=lambda image: str(image.get("service") or ""))


def _release_context(digest_inventory: dict[str, Any], generated_at: str) -> dict[str, Any]:
    return {
        "release": str(digest_inventory.get("release") or os.environ.get("GITHUB_REF_NAME", "")),
        "commit": str(
            os.environ.get("RELEASE_COMMIT") or digest_inventory.get("commit") or os.environ.get("GITHUB_SHA", "")
        ),
        "repository": str(os.environ.get("GITHUB_REPOSITORY") or digest_inventory.get("repository") or ""),
        "workflow": str(os.environ.get("GITHUB_WORKFLOW") or digest_inventory.get("workflow") or ""),
        "workflow_run_id": str(os.environ.get("RELEASE_WORKFLOW_RUN_ID") or os.environ.get("GITHUB_RUN_ID", "")),
        "workflow_run_attempt": str(os.environ.get("GITHUB_RUN_ATTEMPT", "")),
        "upstream_workflow_run_id": str(
            os.environ.get("PUBLISH_IMAGES_WORKFLOW_RUN_ID") or digest_inventory.get("run_id") or ""
        ),
        "generated_at": generated_at,
        "images": _release_images(digest_inventory),
    }


def _merge_component(findings: list[dict[str, Any]]) -> dict[str, str]:
    components = [_as_dict(finding.get("component")) for finding in findings]
    selected = max(components, key=lambda component: len(str(component.get("purl") or "")), default={})
    return {
        "name": str(selected.get("name") or ""),
        "version": str(selected.get("version") or ""),
        "type": str(selected.get("type") or ""),
        "purl": str(selected.get("purl") or ""),
        "language": str(selected.get("language") or ""),
    }


def _merge_fix(findings: list[dict[str, Any]]) -> dict[str, Any]:
    fixes = [_as_dict(finding.get("fix")) for finding in findings]
    versions = sorted({str(version) for fix in fixes for version in _as_list(fix.get("versions")) if version})
    states = sorted({str(fix.get("state")) for fix in fixes if fix.get("state")})
    return {
        "available": bool(versions) or any(bool(fix.get("available")) for fix in fixes),
        "versions": versions,
        "states": states,
    }


def _merge_advisory(findings: list[dict[str, Any]]) -> dict[str, Any]:
    advisories = [_as_dict(finding.get("advisory")) for finding in findings]
    return {
        "namespaces": sorted({str(advisory.get("namespace")) for advisory in advisories if advisory.get("namespace")}),
        "data_sources": sorted(
            {str(advisory.get("data_source")) for advisory in advisories if advisory.get("data_source")}
        ),
        "urls": sorted({str(url) for advisory in advisories for url in _as_list(advisory.get("urls")) if url}),
        "related_ids": sorted(
            {str(item) for advisory in advisories for item in _as_list(advisory.get("related_ids")) if item}
        ),
    }


def _merge_target_evidence(target_findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence_by_artifact: dict[str, dict[str, Any]] = {}
    for finding in target_findings:
        evidence = _as_dict(finding.get("evidence"))
        artifact = str(evidence.get("artifact") or "")
        entry = evidence_by_artifact.setdefault(
            artifact,
            {
                "artifact": artifact,
                "matchers": set(),
                "locations": [],
            },
        )
        entry["matchers"].update(_string_list(evidence.get("matchers")))
        for location in _as_list(evidence.get("locations")):
            if isinstance(location, dict):
                entry["locations"].append(
                    {
                        "path": str(location.get("path") or ""),
                        "layer_diff_id": str(location.get("layer_diff_id") or ""),
                    }
                )

    merged = []
    for entry in evidence_by_artifact.values():
        location_keys = {
            (str(location.get("path") or ""), str(location.get("layer_diff_id") or ""))
            for location in entry["locations"]
        }
        merged.append(
            {
                "artifact": entry["artifact"],
                "matchers": sorted(entry["matchers"]),
                "locations": [{"path": path, "layer_diff_id": layer_id} for path, layer_id in sorted(location_keys)],
            }
        )
    return sorted(merged, key=lambda item: str(item.get("artifact") or ""))


def _seen_in(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_target: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for finding in findings:
        target = _as_dict(finding.get("target"))
        key = (str(target.get("kind") or ""), str(target.get("name") or ""))
        by_target.setdefault(key, []).append(finding)

    seen = []
    for (kind, name), target_findings in by_target.items():
        seen.append(
            {
                "target": {
                    "kind": kind,
                    "name": name,
                },
                "evidence": _merge_target_evidence(target_findings),
                "finding_count": len(target_findings),
            }
        )
    return sorted(seen, key=lambda item: _target_sort_key(item["target"]))


def _advisory(group_findings: list[dict[str, Any]]) -> dict[str, Any]:
    severities = [_severity(finding.get("severity")) for finding in group_findings]
    advisory_id = str(group_findings[0].get("id") or "")
    return {
        "id": advisory_id,
        "component": _merge_component(group_findings),
        "severity": _max_severity(severities),
        "fix": _merge_fix(group_findings),
        "advisory": _merge_advisory(group_findings),
        "seen_in": _seen_in(group_findings),
        "raw_finding_count": len(group_findings),
    }


def build_report(
    findings: list[dict[str, Any]],
    input_files: list[str],
    *,
    digest_inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for finding in findings:
        grouped.setdefault(_group_key(finding), []).append(finding)

    advisories = sorted(
        (_advisory(group_findings) for group_findings in grouped.values()),
        key=lambda advisory: (
            -SEVERITY_ORDER.get(str(advisory["severity"]), 0),
            str(advisory["id"]),
            str(advisory["component"].get("purl") or advisory["component"].get("name") or ""),
        ),
    )
    severity_counts = Counter(str(advisory["severity"]) for advisory in advisories)
    generated_at = datetime.now(UTC).isoformat()
    return {
        "product": "TokenStream",
        "inventory_type": "release-advisory-findings",
        "schema_version": "1.0",
        "generated_at": generated_at,
        "release_context": _release_context(digest_inventory or {}, generated_at),
        "source": {
            "inventory_type": "normalized-security-findings",
            "input_files": input_files,
        },
        "raw_finding_count": len(findings),
        "advisory_count": len(advisories),
        "severity_counts": {severity: severity_counts.get(severity, 0) for severity in SEVERITIES},
        "fix_available_count": sum(1 for advisory in advisories if advisory["fix"]["available"]),
        "advisories": advisories,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deduplicate normalized release security findings.")
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--release-image-digests", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    findings, input_files = _read_findings(args.input_dir)
    digest_inventory = json.loads(args.release_image_digests.read_text(encoding="utf-8"))
    report = build_report(findings, input_files, digest_inventory=digest_inventory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
