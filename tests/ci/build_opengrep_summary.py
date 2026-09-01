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


def _finding_ref(result: dict[str, Any], source: str) -> dict[str, Any]:
    extra = result.get("extra") or {}
    metadata = extra.get("metadata") or {}
    start = result.get("start") or {}
    end = result.get("end") or {}
    return {
        "source": source,
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


def _sort_finding(finding: dict[str, Any]) -> tuple[int, str, int, str, str]:
    severity_rank = {severity: index for index, severity in enumerate(SEVERITIES)}
    line = finding.get("start_line")
    return (
        severity_rank.get(str(finding.get("severity")), severity_rank["UNKNOWN"]),
        str(finding.get("path") or ""),
        int(line) if isinstance(line, int) else 0,
        str(finding.get("check_id") or ""),
        str(finding.get("source") or ""),
    )


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _normalize_prefix(prefix: str) -> str:
    normalized = _normalize_path(prefix)
    return normalized if normalized.endswith("/") else f"{normalized}/"


def _scanned_paths(report: dict[str, Any]) -> list[str]:
    paths = report.get("paths") or {}
    scanned = paths.get("scanned") or []
    return sorted({_normalize_path(str(path)) for path in scanned if path})


def _scan_coverage(reports: list[tuple[Path, dict[str, Any]]], required_prefixes: list[str]) -> dict[str, Any]:
    scanned_paths = sorted({path for _, report in reports for path in _scanned_paths(report)})
    requirements = []

    for prefix in required_prefixes:
        normalized_prefix = _normalize_prefix(prefix)
        matches = [path for path in scanned_paths if path.startswith(normalized_prefix)]
        requirements.append(
            {
                "prefix": normalized_prefix,
                "matched": bool(matches),
                "matching_file_count": len(matches),
            }
        )

    missing_prefixes = [item["prefix"] for item in requirements if not item["matched"]]
    return {
        "passed": not missing_prefixes,
        "scanned_file_count": len(scanned_paths),
        "required_prefixes": requirements,
        "missing_prefixes": missing_prefixes,
    }


def build_reports(
    reports: list[tuple[Path, dict[str, Any]]], required_prefixes: list[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    findings = sorted(
        (
            _finding_ref(result, source=report_path.name)
            for report_path, report in reports
            for result in report.get("results") or []
            if isinstance(result, dict)
        ),
        key=_sort_finding,
    )
    generated_at = datetime.now(UTC).isoformat()
    severity_counts = Counter(finding["severity"] for finding in findings)
    rule_counts = Counter(str(finding["check_id"]) for finding in findings)
    file_counts = Counter(str(finding["path"]) for finding in findings)
    confidence_counts = Counter(str(finding["confidence"] or "UNKNOWN") for finding in findings)
    scanner_error_count = sum(len(report.get("errors") or []) for _, report in reports)
    scan_coverage = _scan_coverage(reports, required_prefixes)
    coverage_passed = bool(scan_coverage["passed"])
    scan_reports = [
        {
            "source": report_path.name,
            "finding_count": len(report.get("results") or []),
            "error_count": len(report.get("errors") or []),
            "scanned_file_count": len(_scanned_paths(report)),
        }
        for report_path, report in reports
    ]

    summary = {
        "product": "TokenStream",
        "scanner": "opengrep",
        "inventory_type": "sast-summary",
        "sources": [report_path.name for report_path, _ in reports],
        "generated_at": generated_at,
        "finding_count": len(findings),
        "error_count": scanner_error_count,
        "severity_counts": {severity: severity_counts.get(severity, 0) for severity in SEVERITIES},
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "rule_counts": dict(sorted(rule_counts.items())),
        "file_counts": dict(sorted(file_counts.items())),
        "error_or_warning_count": severity_counts["ERROR"] + severity_counts["WARNING"],
        "scan_reports": scan_reports,
        "scan_coverage": scan_coverage,
        "top_findings": findings[:50],
    }

    policy = {
        "product": "TokenStream",
        "scanner": "opengrep",
        "inventory_type": "sast-policy-evaluation",
        "sources": summary["sources"],
        "generated_at": generated_at,
        "mode": "permissive-findings-required-coverage" if required_prefixes else "permissive",
        "decision": "passed" if coverage_passed else "failed",
        "enforced": bool(required_prefixes),
        "rules": [
            {
                "id": "SAST-POLICY-001",
                "description": "Report OpenGrep findings without blocking pull requests.",
                "fail_on_severity": "none",
                "fail_on_new_findings": False,
            },
            {
                "id": "SAST-COVERAGE-001",
                "description": "Require OpenGrep reports to include all configured source roots.",
                "required_prefixes": [_normalize_prefix(prefix) for prefix in required_prefixes],
                "fail_on_missing_scan_coverage": True,
            },
        ],
        "observed": {
            "finding_count": len(findings),
            "error_count": scanner_error_count,
            "severity_counts": summary["severity_counts"],
            "error_or_warning_count": summary["error_or_warning_count"],
            "scan_coverage": scan_coverage,
        },
    }

    return summary, policy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Derive SAST summary and policy evidence from OpenGrep JSON.")
    parser.add_argument("--report", required=True, type=Path, action="append")
    parser.add_argument("--require-scanned-prefix", default=[], action="append")
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--policy-output", required=True, type=Path)
    return parser.parse_args()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    reports = [(report_path, json.loads(report_path.read_text(encoding="utf-8"))) for report_path in args.report]
    summary, policy = build_reports(reports, args.require_scanned_prefix)
    _write_json(args.summary_output, summary)
    _write_json(args.policy_output, policy)
    missing_prefixes = summary["scan_coverage"]["missing_prefixes"]
    if missing_prefixes:
        prefixes = ", ".join(missing_prefixes)
        raise SystemExit(f"OpenGrep scan coverage missing required prefix(es): {prefixes}")


if __name__ == "__main__":
    main()
