"""Build static image lint summary and permissive policy evidence from Trivy JSON."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")


def _severity(value: Any) -> str:
    severity = str(value or "UNKNOWN").upper()
    return severity if severity in SEVERITIES else "UNKNOWN"


def _location(data: dict[str, Any]) -> dict[str, Any]:
    cause = data.get("CauseMetadata") if isinstance(data.get("CauseMetadata"), dict) else {}
    return {
        "target": str(data.get("Target") or ""),
        "class": str(data.get("Class") or ""),
        "type": str(data.get("Type") or ""),
        "resource": str(cause.get("Resource") or ""),
        "provider": str(cause.get("Provider") or ""),
        "service": str(cause.get("Service") or ""),
        "start_line": cause.get("StartLine"),
        "end_line": cause.get("EndLine"),
    }


def _misconfiguration_ref(result: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("ID") or item.get("AVDID") or ""),
        "avd_id": str(item.get("AVDID") or ""),
        "severity": _severity(item.get("Severity")),
        "status": str(item.get("Status") or ""),
        "title": str(item.get("Title") or ""),
        "message": str(item.get("Message") or ""),
        "primary_url": str(item.get("PrimaryURL") or ""),
        "location": _location({**result, "CauseMetadata": item.get("CauseMetadata")}),
    }


def _secret_ref(result: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("RuleID") or ""),
        "category": str(item.get("Category") or ""),
        "severity": _severity(item.get("Severity")),
        "title": str(item.get("Title") or ""),
        "location": {
            "target": str(result.get("Target") or ""),
            "class": str(result.get("Class") or ""),
            "type": str(result.get("Type") or ""),
            "start_line": item.get("StartLine"),
            "end_line": item.get("EndLine"),
        },
    }


def _collect_misconfigurations(report: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for result in report.get("Results") or []:
        if not isinstance(result, dict):
            continue
        for item in result.get("Misconfigurations") or []:
            if isinstance(item, dict):
                findings.append(_misconfiguration_ref(result, item))
    return findings


def _collect_secrets(report: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for result in report.get("Results") or []:
        if not isinstance(result, dict):
            continue
        for item in result.get("Secrets") or []:
            if isinstance(item, dict):
                findings.append(_secret_ref(result, item))
    return findings


def _severity_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(item.get("severity") or "UNKNOWN").upper() for item in items)
    return {severity: counts.get(severity, 0) for severity in SEVERITIES}


def _sort_finding(item: dict[str, Any]) -> tuple[int, str, str]:
    rank = {severity: index for index, severity in enumerate(SEVERITIES)}
    return (
        rank.get(str(item.get("severity") or "UNKNOWN"), rank["UNKNOWN"]),
        str(item.get("id") or ""),
        str(item.get("title") or ""),
    )


def build_reports(
    report: dict[str, Any],
    *,
    source: str,
    target_name: str,
    target_kind: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    generated_at = datetime.now(UTC).isoformat()
    misconfigurations = sorted(_collect_misconfigurations(report), key=_sort_finding)
    secrets = sorted(_collect_secrets(report), key=_sort_finding)
    findings = [*misconfigurations, *secrets]

    summary = {
        "product": "TokenStream",
        "scanner": "trivy",
        "inventory_type": "image-lint-summary",
        "source": source,
        "generated_at": generated_at,
        "target": {"name": target_name, "kind": target_kind},
        "artifact_name": str(report.get("ArtifactName") or ""),
        "artifact_type": str(report.get("ArtifactType") or ""),
        "result_count": len([item for item in report.get("Results") or [] if isinstance(item, dict)]),
        "misconfiguration_count": len(misconfigurations),
        "secret_count": len(secrets),
        "finding_count": len(findings),
        "misconfiguration_severity_counts": _severity_counts(misconfigurations),
        "secret_severity_counts": _severity_counts(secrets),
        "finding_severity_counts": _severity_counts(findings),
        "critical_or_high_count": sum(1 for item in findings if item.get("severity") in {"CRITICAL", "HIGH"}),
        "top_misconfigurations": misconfigurations[:50],
        "top_secrets": secrets[:50],
    }

    policy = {
        "product": "TokenStream",
        "scanner": "trivy",
        "inventory_type": "image-lint-policy-evaluation",
        "source": source,
        "generated_at": generated_at,
        "target": {"name": target_name, "kind": target_kind},
        "mode": "permissive",
        "decision": "passed",
        "enforced": False,
        "rules": [
            {
                "id": "IMAGE-LINT-POLICY-001",
                "description": "Report Trivy static image lint findings without blocking pull requests.",
                "severity_cutoff": "none",
                "fail_on_misconfiguration": False,
                "fail_on_secret": False,
            }
        ],
        "observed": {
            "misconfiguration_count": summary["misconfiguration_count"],
            "secret_count": summary["secret_count"],
            "finding_count": summary["finding_count"],
            "finding_severity_counts": summary["finding_severity_counts"],
            "critical_or_high_count": summary["critical_or_high_count"],
        },
    }
    return summary, policy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Derive static image lint evidence from Trivy JSON.")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--target-kind", default="container-image")
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--policy-output", required=True, type=Path)
    return parser.parse_args()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    summary, policy = build_reports(
        report,
        source=args.source,
        target_name=args.target_name,
        target_kind=args.target_kind,
    )
    _write_json(args.summary_output, summary)
    _write_json(args.policy_output, policy)


if __name__ == "__main__":
    main()
