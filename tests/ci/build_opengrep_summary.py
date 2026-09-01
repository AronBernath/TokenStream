"""Build OpenGrep SAST summary and permissive policy evidence."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SEVERITIES = ("ERROR", "WARNING", "INFO", "UNKNOWN")


def _severity(result: dict[str, Any]) -> str:
    severity = str((result.get("extra") or {}).get("severity") or "UNKNOWN").upper()
    if severity not in SEVERITIES:
        return "UNKNOWN"
    return severity


def _metadata_list(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key)
    if isinstance(value, list):
        return sorted(str(item) for item in value)
    if value:
        return [str(value)]
    return []


def _finding_ref(result: dict[str, Any]) -> dict[str, Any]:
    extra = result.get("extra") or {}
    metadata = extra.get("metadata") or {}
    start = result.get("start") or {}
    end = result.get("end") or {}
    return {
        "check_id": result.get("check_id") or "",
        "path": result.get("path") or "",
        "start_line": start.get("line"),
        "end_line": end.get("line"),
        "severity": _severity(result),
        "message": extra.get("message") or "",
        "category": metadata.get("category") or "",
        "confidence": metadata.get("confidence") or "",
        "impact": metadata.get("impact") or "",
        "likelihood": metadata.get("likelihood") or "",
        "cwe": _metadata_list(metadata, "cwe"),
        "owasp": _metadata_list(metadata, "owasp"),
        "references": _metadata_list(metadata, "references"),
    }


def _sort_finding(finding: dict[str, Any]) -> tuple[int, str, int, str]:
    severity_rank = {severity: index for index, severity in enumerate(SEVERITIES)}
    line = finding.get("start_line")
    return (
        severity_rank.get(str(finding.get("severity")), severity_rank["UNKNOWN"]),
        str(finding.get("path") or ""),
        int(line) if isinstance(line, int) else 0,
        str(finding.get("check_id") or ""),
    )


def build_reports(report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    results = [result for result in report.get("results") or [] if isinstance(result, dict)]
    findings = sorted((_finding_ref(result) for result in results), key=_sort_finding)
    generated_at = datetime.now(UTC).isoformat()
    severity_counts = Counter(finding["severity"] for finding in findings)
    rule_counts = Counter(str(finding["check_id"]) for finding in findings)
    file_counts = Counter(str(finding["path"]) for finding in findings)
    confidence_counts = Counter(str(finding["confidence"] or "UNKNOWN") for finding in findings)

    summary = {
        "product": "TokenStream",
        "scanner": "opengrep",
        "inventory_type": "sast-summary",
        "source": "opengrep-report.json",
        "generated_at": generated_at,
        "finding_count": len(findings),
        "error_count": len(report.get("errors") or []),
        "severity_counts": {severity: severity_counts.get(severity, 0) for severity in SEVERITIES},
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "rule_counts": dict(sorted(rule_counts.items())),
        "file_counts": dict(sorted(file_counts.items())),
        "error_or_warning_count": severity_counts["ERROR"] + severity_counts["WARNING"],
        "top_findings": findings[:50],
    }

    policy = {
        "product": "TokenStream",
        "scanner": "opengrep",
        "inventory_type": "sast-policy-evaluation",
        "source": "opengrep-report.json",
        "generated_at": generated_at,
        "mode": "permissive",
        "decision": "passed",
        "enforced": False,
        "rules": [
            {
                "id": "SAST-POLICY-001",
                "description": "Report OpenGrep findings without blocking pull requests.",
                "fail_on_severity": "none",
                "fail_on_new_findings": False,
            }
        ],
        "observed": {
            "finding_count": len(findings),
            "error_count": len(report.get("errors") or []),
            "severity_counts": summary["severity_counts"],
            "error_or_warning_count": summary["error_or_warning_count"],
        },
    }

    return summary, policy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Derive SAST summary and policy evidence from OpenGrep JSON.")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--policy-output", required=True, type=Path)
    return parser.parse_args()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    summary, policy = build_reports(report)
    _write_json(args.summary_output, summary)
    _write_json(args.policy_output, policy)


if __name__ == "__main__":
    main()
