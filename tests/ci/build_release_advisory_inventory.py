"""Build release advisory and update candidate evidence from Grype reports."""

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
SCOPE = {
    "included": [
        "dependency vulnerabilities from the Grype release source SBOM scan",
        "dependency and operating system vulnerabilities from Grype release image SBOM scans",
    ],
    "excluded": [
        "SAST findings",
        "DAST findings",
        "Trivy image lint findings",
        "license issues",
        "secret findings",
    ],
}


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


def _version_sort_key(version: str) -> tuple[tuple[int, int | str], ...]:
    parts: list[tuple[int, int | str]] = []
    for part in version.replace("-", ".").replace("_", ".").split("."):
        parts.append((0, int(part)) if part.isdigit() else (1, part.lower()))
    return tuple(parts)


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


def _artifact_path(path: Path) -> str:
    return path.as_posix()


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


def _fix(vulnerability: dict[str, Any]) -> dict[str, Any]:
    fix = _as_dict(vulnerability.get("fix"))
    versions = sorted({str(version) for version in _as_list(fix.get("versions")) if version}, key=_version_sort_key)
    return {
        "available": bool(versions),
        "versions": versions,
        "state": str(fix.get("state") or ("fixed_available" if versions else "unknown")),
    }


def _component(artifact: dict[str, Any]) -> dict[str, str]:
    return {
        "name": str(artifact.get("name") or ""),
        "version": str(artifact.get("version") or ""),
        "type": str(artifact.get("type") or ""),
        "purl": str(artifact.get("purl") or ""),
        "language": str(artifact.get("language") or ""),
    }


def _locations(artifact: dict[str, Any]) -> list[dict[str, str]]:
    locations = []
    for location in _as_list(artifact.get("locations")):
        if not isinstance(location, dict):
            continue
        locations.append(
            {
                "path": str(location.get("path") or ""),
                "layer_diff_id": str(_as_dict(location.get("layer")).get("diffID") or ""),
            }
        )
    return locations


def _related_vulnerability_ids(match: dict[str, Any]) -> list[str]:
    return [
        str(item.get("id"))
        for item in _as_list(match.get("relatedVulnerabilities"))
        if isinstance(item, dict) and item.get("id")
    ]


def _matcher_names(match: dict[str, Any]) -> list[str]:
    names = []
    for detail in _as_list(match.get("matchDetails")):
        if isinstance(detail, dict) and detail.get("matcher"):
            names.append(str(detail["matcher"]))
    return sorted(set(names))


def _finding(
    match: dict[str, Any],
    *,
    target_kind: str,
    target_name: str,
    evidence_artifact: str,
) -> dict[str, Any]:
    artifact = _as_dict(match.get("artifact"))
    vulnerability = _as_dict(match.get("vulnerability"))
    return {
        "id": str(vulnerability.get("id") or ""),
        "source": "grype",
        "target": {
            "kind": target_kind,
            "name": target_name,
        },
        "component": _component(artifact),
        "severity": _severity(vulnerability.get("severity")),
        "fix": _fix(vulnerability),
        "advisory": {
            "id": str(vulnerability.get("id") or ""),
            "namespace": str(vulnerability.get("namespace") or ""),
            "data_source": str(vulnerability.get("dataSource") or ""),
            "urls": [str(url) for url in _as_list(vulnerability.get("urls")) if url],
            "related_ids": _related_vulnerability_ids(match),
        },
        "evidence": {
            "artifact": evidence_artifact,
            "matchers": _matcher_names(match),
            "locations": _locations(artifact),
        },
    }


def _read_grype_findings(
    report_path: Path,
    *,
    target_kind: str,
    target_name: str,
) -> list[dict[str, Any]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return [
        _finding(
            match,
            target_kind=target_kind,
            target_name=target_name,
            evidence_artifact=report_path.name,
        )
        for match in _as_list(report.get("matches"))
        if isinstance(match, dict) and _as_dict(match.get("vulnerability")).get("id")
    ]


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
    versions = sorted(
        {str(version) for fix in fixes for version in _as_list(fix.get("versions")) if version},
        key=_version_sort_key,
    )
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
    return {
        "id": str(group_findings[0].get("id") or ""),
        "component": _merge_component(group_findings),
        "severity": _max_severity(severities),
        "fix": _merge_fix(group_findings),
        "advisory": _merge_advisory(group_findings),
        "seen_in": _seen_in(group_findings),
        "raw_finding_count": len(group_findings),
    }


def _advisories(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for finding in findings:
        grouped.setdefault(_group_key(finding), []).append(finding)

    return sorted(
        (_advisory(group_findings) for group_findings in grouped.values()),
        key=lambda advisory: (
            -SEVERITY_ORDER.get(str(advisory["severity"]), 0),
            str(advisory["id"]),
            str(advisory["component"].get("purl") or advisory["component"].get("name") or ""),
        ),
    )


def _target_name(target: dict[str, Any]) -> str:
    name = str(target.get("name") or "")
    kind = str(target.get("kind") or "")
    return name or kind


def _affected_targets(advisory: dict[str, Any]) -> list[str]:
    targets = set()
    for item in _as_list(advisory.get("seen_in")):
        if isinstance(item, dict):
            target_name = _target_name(_as_dict(item.get("target")))
            if target_name:
                targets.add(target_name)
    return sorted(targets)


def _reason_ids(advisory: dict[str, Any]) -> list[str]:
    nested = _as_dict(advisory.get("advisory"))
    reasons = {str(advisory.get("id") or "")}
    reasons.update(str(item) for item in _as_list(nested.get("related_ids")) if item)
    return sorted(reason for reason in reasons if reason)


def _update_candidate(group: list[dict[str, Any]]) -> dict[str, Any]:
    component = _as_dict(group[0].get("component"))
    versions = sorted(
        {
            str(version)
            for advisory in group
            for version in _as_list(_as_dict(advisory.get("fix")).get("versions"))
            if version
        },
        key=_version_sort_key,
    )
    reasons = sorted({reason for advisory in group for reason in _reason_ids(advisory)})
    affected_targets = sorted({target for advisory in group for target in _affected_targets(advisory)})
    severities = [_severity(advisory.get("severity")) for advisory in group]
    return {
        "component": {
            "name": str(component.get("name") or ""),
            "current_version": str(component.get("version") or ""),
            "type": str(component.get("type") or ""),
            "purl": str(component.get("purl") or ""),
            "language": str(component.get("language") or ""),
        },
        "suggested_versions": versions,
        "reason": reasons,
        "affected_targets": affected_targets,
        "max_severity": _max_severity(severities),
        "advisory_count": len(group),
        "evidence": {
            "artifact": "release-advisory-inventory.json",
        },
    }


def _update_candidates(advisories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for advisory in advisories:
        if _as_list(_as_dict(advisory.get("fix")).get("versions")):
            grouped.setdefault(_component_key(_as_dict(advisory.get("component"))), []).append(advisory)

    return sorted(
        (_update_candidate(group) for group in grouped.values()),
        key=lambda candidate: (
            -SEVERITY_ORDER.get(str(candidate["max_severity"]), 0),
            str(candidate["component"].get("purl") or candidate["component"].get("name") or ""),
            str(candidate["component"].get("current_version") or ""),
        ),
    )


def _parse_image_report(value: str) -> tuple[str, Path]:
    if "=" in value:
        name, path = value.split("=", 1)
        return name, Path(path)
    path = Path(value)
    marker = "release-image-vulnerabilities."
    name = path.stem
    if path.name.startswith(marker) and path.name.endswith(".json"):
        name = path.name[len(marker) : -len(".json")]
    return name, path


def build_reports(
    *,
    source_grype_report: Path,
    image_grype_reports: list[tuple[str, Path]],
    image_digests: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    digest_inventory = json.loads(image_digests.read_text(encoding="utf-8"))
    generated_at = datetime.now(UTC).isoformat()
    release_context = _release_context(digest_inventory, generated_at)
    input_reports = [
        {
            "target": {
                "kind": "source-tree",
                "name": "source",
            },
            "path": _artifact_path(source_grype_report),
            "artifact": source_grype_report.name,
        }
    ]
    findings = _read_grype_findings(source_grype_report, target_kind="source-tree", target_name="source")

    for image_name, report_path in image_grype_reports:
        input_reports.append(
            {
                "target": {
                    "kind": "container-image",
                    "name": image_name,
                },
                "path": _artifact_path(report_path),
                "artifact": report_path.name,
            }
        )
        findings.extend(_read_grype_findings(report_path, target_kind="container-image", target_name=image_name))

    advisories = _advisories(findings)
    candidates = _update_candidates(advisories)
    advisory_severity_counts = Counter(str(advisory["severity"]) for advisory in advisories)
    candidate_severity_counts = Counter(str(candidate["max_severity"]) for candidate in candidates)
    advisory_inventory = {
        "product": "TokenStream",
        "inventory_type": "release-advisory-inventory",
        "schema_version": "1.0",
        "generated_at": generated_at,
        "release_context": release_context,
        "scope": SCOPE,
        "source": {
            "scanner": "grype",
            "input_reports": input_reports,
        },
        "raw_finding_count": len(findings),
        "advisory_count": len(advisories),
        "severity_counts": {severity: advisory_severity_counts.get(severity, 0) for severity in SEVERITIES},
        "fix_available_count": sum(1 for advisory in advisories if advisory["fix"]["available"]),
        "advisories": advisories,
    }
    update_candidates = {
        "product": "TokenStream",
        "inventory_type": "release-update-candidates",
        "schema_version": "1.0",
        "generated_at": generated_at,
        "release_context": release_context,
        "scope": SCOPE,
        "source": {
            "inventory_type": "release-advisory-inventory",
            "artifact": "release-advisory-inventory.json",
        },
        "candidate_count": len(candidates),
        "severity_counts": {severity: candidate_severity_counts.get(severity, 0) for severity in SEVERITIES},
        "candidates": candidates,
    }
    return advisory_inventory, update_candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build release advisory inventory and update candidate evidence.")
    parser.add_argument("--source-grype-report", required=True, type=Path)
    parser.add_argument("--image-grype-report", action="append", default=[])
    parser.add_argument("--image-digests", required=True, type=Path)
    parser.add_argument("--advisory-output", required=True, type=Path)
    parser.add_argument("--update-candidates-output", required=True, type=Path)
    return parser.parse_args()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    advisory_inventory, update_candidates = build_reports(
        source_grype_report=args.source_grype_report,
        image_grype_reports=[_parse_image_report(value) for value in args.image_grype_report],
        image_digests=args.image_digests,
    )
    _write_json(args.advisory_output, advisory_inventory)
    _write_json(args.update_candidates_output, update_candidates)


if __name__ == "__main__":
    main()
