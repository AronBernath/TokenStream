"""Build OWASP ZAP DAST summary and permissive policy evidence."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


RISK_LEVELS = ("High", "Medium", "Low", "Informational", "Unknown")


def _risk(alert: dict[str, Any]) -> str:
    risk = str(alert.get("risk") or alert.get("riskdesc") or "Unknown").split("(", maxsplit=1)[0].strip()
    return risk if risk in RISK_LEVELS else "Unknown"


def _confidence(alert: dict[str, Any]) -> str:
    return str(alert.get("confidence") or "Unknown").strip() or "Unknown"


def _alert_instances(alert: dict[str, Any]) -> int:
    instances = alert.get("instances")
    if isinstance(instances, list):
        return len(instances)
    count = alert.get("count")
    try:
        return int(count)
    except (TypeError, ValueError):
        return 1


def _site_alerts(report: dict[str, Any]) -> list[dict[str, Any]]:
    alerts = []
    for site in report.get("site") or []:
        if not isinstance(site, dict):
            continue
        site_name = site.get("@name") or site.get("name") or ""
        for alert in site.get("alerts") or []:
            if isinstance(alert, dict):
                alerts.append({**alert, "_site": site_name})
    return alerts


def _alert_ref(alert: dict[str, Any], source: str) -> dict[str, Any]:
    instances = alert.get("instances") if isinstance(alert.get("instances"), list) else []
    sample_urls = []
    for instance in instances[:5]:
        if isinstance(instance, dict):
            uri = instance.get("uri") or instance.get("url")
            if uri:
                sample_urls.append(str(uri))
    return {
        "source": source,
        "site": alert.get("_site") or "",
        "plugin_id": str(alert.get("pluginid") or alert.get("pluginId") or ""),
        "alert": alert.get("alert") or alert.get("name") or "",
        "risk": _risk(alert),
        "confidence": _confidence(alert),
        "instance_count": _alert_instances(alert),
        "cwe_id": str(alert.get("cweid") or ""),
        "wasc_id": str(alert.get("wascid") or ""),
        "description": alert.get("desc") or alert.get("description") or "",
        "solution": alert.get("solution") or "",
        "sample_urls": sample_urls,
    }


def _sort_alert(alert: dict[str, Any]) -> tuple[int, str, str, str]:
    risk_rank = {risk: index for index, risk in enumerate(RISK_LEVELS)}
    return (
        risk_rank.get(str(alert.get("risk")), risk_rank["Unknown"]),
        str(alert.get("source") or ""),
        str(alert.get("plugin_id") or ""),
        str(alert.get("alert") or ""),
    )


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_probe(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return _load_json(path)


def build_summary(report_paths: list[Path], probe_path: Path | None) -> tuple[dict[str, Any], dict[str, Any]]:
    generated_at = datetime.now(UTC).isoformat()
    reports = [(path, _load_json(path)) for path in report_paths]
    alerts = sorted(
        (_alert_ref(alert, source=path.name) for path, report in reports for alert in _site_alerts(report)),
        key=_sort_alert,
    )
    risk_counts = Counter(alert["risk"] for alert in alerts)
    confidence_counts = Counter(alert["confidence"] for alert in alerts)
    plugin_counts = Counter(f"{alert['plugin_id']} {alert['alert']}".strip() for alert in alerts)
    source_counts = Counter(alert["source"] for alert in alerts)
    probe = _load_probe(probe_path)

    summary = {
        "product": "TokenStream",
        "scanner": "owasp-zap",
        "inventory_type": "dast-summary",
        "mode": "unauthenticated-baseline",
        "generated_at": generated_at,
        "sources": [path.name for path, _ in reports],
        "alert_count": len(alerts),
        "alert_instance_count": sum(alert["instance_count"] for alert in alerts),
        "risk_counts": {risk: risk_counts.get(risk, 0) for risk in RISK_LEVELS},
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "plugin_counts": dict(sorted(plugin_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "reports": [
            {
                "source": path.name,
                "site_count": len(report.get("site") or []),
                "alert_count": len(_site_alerts(report)),
            }
            for path, report in reports
        ],
        "unauthenticated_probe": {
            "source": probe_path.name if probe_path else None,
            "summary": probe.get("summary", {}) if probe else {},
        },
        "top_alerts": alerts[:50],
    }

    policy = {
        "product": "TokenStream",
        "scanner": "owasp-zap",
        "inventory_type": "dast-policy-evaluation",
        "mode": "unauthenticated-baseline-permissive",
        "generated_at": generated_at,
        "decision": "passed",
        "enforced": False,
        "rules": [
            {
                "id": "DAST-POLICY-001",
                "description": "Generate unauthenticated OWASP ZAP baseline evidence without blocking on alerts.",
                "fail_on_alert_risk": "none",
            },
            {
                "id": "DAST-COVERAGE-001",
                "description": "Fail workflow steps when the CI stack, ZAP runtime, or DAST artifacts are unavailable.",
                "fail_on_runtime_or_artifact_error": True,
            },
        ],
        "observed": {
            "alert_count": summary["alert_count"],
            "alert_instance_count": summary["alert_instance_count"],
            "risk_counts": summary["risk_counts"],
            "unauthenticated_probe": summary["unauthenticated_probe"]["summary"],
        },
    }
    return summary, policy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Derive DAST summary and policy evidence from ZAP JSON reports.")
    parser.add_argument("--zap-report", required=True, type=Path, action="append")
    parser.add_argument("--probe-report", type=Path)
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--policy-output", required=True, type=Path)
    return parser.parse_args()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    summary, policy = build_summary(args.zap_report, args.probe_report)
    _write_json(args.summary_output, summary)
    _write_json(args.policy_output, policy)


if __name__ == "__main__":
    main()
