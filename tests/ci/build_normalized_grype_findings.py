"""Normalize Grype source and image vulnerability findings into one schema."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SEVERITIES = ("Critical", "High", "Medium", "Low", "Negligible", "Unknown")


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _severity(value: Any) -> str:
    severity = str(value or "Unknown")
    return severity if severity in SEVERITIES else "Unknown"


def _fix(vulnerability: dict[str, Any]) -> dict[str, Any]:
    fix = _as_dict(vulnerability.get("fix"))
    versions = [str(version) for version in _as_list(fix.get("versions")) if version]
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


def _locations(match: dict[str, Any]) -> list[dict[str, str]]:
    artifact = _as_dict(match.get("artifact"))
    locations = []
    for location in _as_list(artifact.get("locations")):
        if not isinstance(location, dict):
            continue
        path = location.get("path")
        layer_id = _as_dict(location.get("layer")).get("diffID")
        locations.append(
            {
                "path": str(path or ""),
                "layer_diff_id": str(layer_id or ""),
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
            "locations": _locations(match),
        },
    }


def build_report(
    report: dict[str, Any],
    *,
    target_kind: str,
    target_name: str,
    evidence_artifact: str,
) -> dict[str, Any]:
    matches = [match for match in _as_list(report.get("matches")) if isinstance(match, dict)]
    findings = sorted(
        (
            _finding(
                match,
                target_kind=target_kind,
                target_name=target_name,
                evidence_artifact=evidence_artifact,
            )
            for match in matches
        ),
        key=lambda finding: (
            str(finding["id"]),
            str(finding["component"]["purl"] or finding["component"]["name"]),
            str(finding["target"]["name"]),
        ),
    )
    severity_counts = Counter(str(finding["severity"]) for finding in findings)
    fix_counts = Counter(str(finding["fix"]["state"]) for finding in findings)
    return {
        "product": "TokenStream",
        "inventory_type": "normalized-security-findings",
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "scanner": "grype",
            "artifact": evidence_artifact,
        },
        "target": {
            "kind": target_kind,
            "name": target_name,
        },
        "finding_count": len(findings),
        "severity_counts": {severity: severity_counts.get(severity, 0) for severity in SEVERITIES},
        "fix_state_counts": dict(sorted(fix_counts.items())),
        "findings": findings,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize Grype vulnerability findings.")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--target-kind", required=True)
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--evidence-artifact", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    normalized = build_report(
        report,
        target_kind=args.target_kind,
        target_name=args.target_name,
        evidence_artifact=args.evidence_artifact,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
