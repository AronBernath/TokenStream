"""Build evidence-backed release update candidates from deduplicated advisories."""

from __future__ import annotations

import argparse
import json
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


def _severity(value: Any) -> str:
    severity = str(value or "Unknown")
    return severity if severity in SEVERITY_ORDER else "Unknown"


def _component_key(component: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(component.get("purl") or ""),
        str(component.get("name") or ""),
        str(component.get("version") or ""),
    )


def _version_sort_key(version: str) -> tuple[tuple[int, int | str], ...]:
    parts: list[tuple[int, int | str]] = []
    for part in version.replace("-", ".").replace("_", ".").split("."):
        parts.append((0, int(part)) if part.isdigit() else (1, part.lower()))
    return tuple(parts)


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


def _candidate(group: list[dict[str, Any]]) -> dict[str, Any]:
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
        "max_severity": max(severities or ["Unknown"], key=lambda severity: SEVERITY_ORDER.get(severity, 0)),
        "advisory_count": len(group),
        "evidence": {
            "artifact": "release-advisory-findings.json",
        },
    }


def build_report(advisory_report: dict[str, Any]) -> dict[str, Any]:
    advisories = [
        advisory
        for advisory in _as_list(advisory_report.get("advisories"))
        if isinstance(advisory, dict) and _as_list(_as_dict(advisory.get("fix")).get("versions"))
    ]
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for advisory in advisories:
        grouped.setdefault(_component_key(_as_dict(advisory.get("component"))), []).append(advisory)

    candidates = sorted(
        (_candidate(group) for group in grouped.values()),
        key=lambda candidate: (
            -SEVERITY_ORDER.get(str(candidate["max_severity"]), 0),
            str(candidate["component"].get("purl") or candidate["component"].get("name") or ""),
            str(candidate["component"].get("current_version") or ""),
        ),
    )
    severity_counts = Counter(str(candidate["max_severity"]) for candidate in candidates)
    return {
        "product": "TokenStream",
        "inventory_type": "release-update-candidates",
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "inventory_type": "release-advisory-findings",
            "artifact": "release-advisory-findings.json",
        },
        "candidate_count": len(candidates),
        "severity_counts": {severity: severity_counts.get(severity, 0) for severity in SEVERITIES},
        "candidates": candidates,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build release update candidates from advisory evidence.")
    parser.add_argument("--advisories", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    advisory_report = json.loads(args.advisories.read_text(encoding="utf-8"))
    report = build_report(advisory_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
