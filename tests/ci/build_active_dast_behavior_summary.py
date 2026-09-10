"""Build active DAST behavior evidence from OWASP ZAP coverage data."""

from __future__ import annotations

import argparse
import json
import shlex
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


RISK_LEVELS = ("High", "Medium", "Low", "Informational", "Unknown")
STATUS_FAMILIES = ("1xx", "2xx", "3xx", "4xx", "5xx", "unknown")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _status_code(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _status_family(status: int | None) -> str:
    if status is None:
        return "unknown"
    if 100 <= status <= 599:
        return f"{status // 100}xx"
    return "unknown"


def _status_key(status: int | None) -> str:
    if status is None:
        return "unknown"
    return str(status)


def _risk(alert: dict[str, Any]) -> str:
    risk = str(alert.get("risk") or alert.get("riskdesc") or "Unknown").split("(", maxsplit=1)[0].strip()
    return risk if risk in RISK_LEVELS else "Unknown"


def _confidence(alert: dict[str, Any]) -> str:
    return str(alert.get("confidence") or "Unknown").strip() or "Unknown"


def _alert_instances(alert: dict[str, Any]) -> int:
    instances = alert.get("instances")
    if isinstance(instances, list):
        return len(instances)
    try:
        return int(alert.get("count"))
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
    }


def _report_insights(report: dict[str, Any], source: str) -> list[dict[str, Any]]:
    insights = []
    for insight in report.get("insights") or []:
        if isinstance(insight, dict):
            insights.append(
                {
                    "source": source,
                    "key": insight.get("key") or "",
                    "level": insight.get("level") or "",
                    "reason": insight.get("reason") or "",
                    "description": insight.get("description") or "",
                    "statistic": insight.get("statistic") or "",
                }
            )
    return insights


def _message_endpoint(message: dict[str, Any], target_endpoints: tuple[str, ...]) -> str | None:
    matched = str(message.get("matched_protected_prefix") or "")
    if matched in target_endpoints:
        return matched

    path = urlparse(str(message.get("url") or "")).path
    for endpoint in target_endpoints:
        if path == endpoint or path.startswith(f"{endpoint}/"):
            return endpoint
    return None


def _safe_message(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(message.get("id") or ""),
        "method": str(message.get("method") or ""),
        "url": str(message.get("url") or ""),
        "matched_protected_prefix": str(message.get("matched_protected_prefix") or ""),
        "status": _status_code(message.get("status")),
        "authorization_bearer_present": bool(message.get("authorization_bearer_present")),
    }


def _counter_dict(counter: Counter[Any], *, stringify_keys: bool = False) -> dict[str, int]:
    items = sorted(counter.items(), key=lambda item: str(item[0]))
    if stringify_keys:
        return {str(key): count for key, count in items}
    return dict(items)


def _status_family_counts(counter: Counter[str]) -> dict[str, int]:
    return {family: counter.get(family, 0) for family in STATUS_FAMILIES}


def _empty_endpoint(endpoint: str) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "observed": False,
        "authenticated": False,
        "request_count": 0,
        "authenticated_request_count": 0,
        "missing_auth_count": 0,
        "methods": {},
        "status_counts": {},
        "status_family_counts": {family: 0 for family in STATUS_FAMILIES},
        "samples_5xx": [],
    }


def _parse_value(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(value)
    except ValueError:
        return value


def _parse_option_pairs(pairs: list[str]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"ZAP option must use key=value form: {pair}")
        key, value = pair.split("=", maxsplit=1)
        key = key.strip()
        if not key:
            raise ValueError(f"ZAP option key is empty: {pair}")
        options[key] = _parse_value(value.strip())
    return options


def _command_options(command: str) -> dict[str, Any]:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()

    def value_after(flag: str) -> str | None:
        if flag not in parts:
            return None
        index = parts.index(flag)
        if index + 1 >= len(parts):
            return None
        return parts[index + 1]

    safe_scan_flag = "-S" in parts
    timeout = value_after("-T")
    return {
        "command": command,
        "active_scan_enabled": not safe_scan_flag,
        "safe_scan_flag": safe_scan_flag,
        "ignore_alert_exit_code": "-I" in parts,
        "format": value_after("-f"),
        "target": value_after("-t"),
        "timeout_minutes": _parse_value(timeout) if timeout is not None else None,
        "json_report": value_after("-J"),
        "hook": value_after("--hook"),
    }


def _collect_alerts(report_paths: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    alerts = []
    insights = []
    for path in report_paths:
        report = _load_json(path)
        alerts.extend(_alert_ref(alert, path.name) for alert in _site_alerts(report))
        insights.extend(_report_insights(report, path.name))
    return alerts, insights


def _rule(
    rule_id: str,
    description: str,
    *,
    result: str,
    enforced: bool,
    observed: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": rule_id,
        "description": description,
        "result": result,
        "enforced": enforced,
        "observed": observed,
    }


def build_behavior_evidence(
    *,
    coverage_path: Path,
    zap_report_paths: list[Path],
    target_endpoints: list[str],
    scan_mode: str,
    policy_mode: str,
    zap_command: str,
    zap_option_pairs: list[str],
    fail_on_missing_target: bool,
    fail_on_missing_auth: bool,
    fail_on_no_authenticated_traffic: bool,
    fail_on_no_targeted_traffic: bool,
    max_samples: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    generated_at = datetime.now(UTC).isoformat()
    unique_targets = tuple(dict.fromkeys(target_endpoints))
    if not unique_targets:
        raise ValueError("At least one target endpoint is required")

    coverage = _load_json(coverage_path)
    alerts, insights = _collect_alerts(zap_report_paths)
    risk_counts = Counter(alert["risk"] for alert in alerts)
    confidence_counts = Counter(alert["confidence"] for alert in alerts)
    high_medium_alerts = [alert for alert in alerts if alert["risk"] in {"High", "Medium"}]

    endpoint_working: dict[str, dict[str, Any]] = {
        endpoint: {
            "methods": Counter(),
            "status_counts": Counter(),
            "status_family_counts": Counter(),
            "request_count": 0,
            "authenticated_request_count": 0,
            "missing_auth_count": 0,
            "samples_5xx": [],
        }
        for endpoint in unique_targets
    }
    targeted_status_families: Counter[str] = Counter()
    missing_auth_samples = []
    out_of_scope_protected_message_count = 0

    protected_messages = [
        _safe_message(message) for message in coverage.get("protected_messages") or [] if isinstance(message, dict)
    ]
    for message in protected_messages:
        endpoint = _message_endpoint(message, unique_targets)
        if endpoint is None:
            out_of_scope_protected_message_count += 1
            continue

        status = message["status"]
        family = _status_family(status)
        stats = endpoint_working[endpoint]
        stats["request_count"] += 1
        stats["methods"][message["method"]] += 1
        stats["status_counts"][_status_key(status)] += 1
        stats["status_family_counts"][family] += 1
        targeted_status_families[family] += 1

        if message["authorization_bearer_present"]:
            stats["authenticated_request_count"] += 1
        else:
            stats["missing_auth_count"] += 1
            if len(missing_auth_samples) < max_samples:
                missing_auth_samples.append(message)

        if family == "5xx" and len(stats["samples_5xx"]) < max_samples:
            stats["samples_5xx"].append(message)

    endpoints = []
    for endpoint in unique_targets:
        stats = endpoint_working[endpoint]
        endpoint_summary = _empty_endpoint(endpoint)
        endpoint_summary.update(
            {
                "observed": stats["request_count"] > 0,
                "authenticated": stats["request_count"] > 0 and stats["missing_auth_count"] == 0,
                "request_count": stats["request_count"],
                "authenticated_request_count": stats["authenticated_request_count"],
                "missing_auth_count": stats["missing_auth_count"],
                "methods": _counter_dict(stats["methods"], stringify_keys=True),
                "status_counts": _counter_dict(stats["status_counts"], stringify_keys=True),
                "status_family_counts": _status_family_counts(stats["status_family_counts"]),
                "samples_5xx": stats["samples_5xx"],
            }
        )
        endpoints.append(endpoint_summary)

    missing_targets = [endpoint["endpoint"] for endpoint in endpoints if not endpoint["observed"]]
    endpoints_missing_auth = [endpoint["endpoint"] for endpoint in endpoints if endpoint["missing_auth_count"] > 0]
    targeted_request_count = sum(endpoint["request_count"] for endpoint in endpoints)
    targeted_authenticated_request_count = sum(endpoint["authenticated_request_count"] for endpoint in endpoints)
    total_5xx_count = sum(endpoint["status_family_counts"]["5xx"] for endpoint in endpoints)
    samples_5xx = [sample for endpoint in endpoints for sample in endpoint["samples_5xx"][:max_samples]][:max_samples]

    command_options = _command_options(zap_command)
    command_options.update(_parse_option_pairs(zap_option_pairs))

    blocking_failures = []
    no_targeted_traffic = targeted_request_count == 0
    no_authenticated_traffic = targeted_authenticated_request_count == 0
    if fail_on_no_targeted_traffic and no_targeted_traffic:
        blocking_failures.append("ACTIVE-DAST-TRAFFIC-001")
    if fail_on_no_authenticated_traffic and no_authenticated_traffic:
        blocking_failures.append("ACTIVE-DAST-AUTH-TRAFFIC-001")
    if fail_on_missing_target and missing_targets:
        blocking_failures.append("ACTIVE-DAST-COVERAGE-001")
    if fail_on_missing_auth and endpoints_missing_auth:
        blocking_failures.append("ACTIVE-DAST-AUTH-001")

    policy_decision = "failed" if blocking_failures else "passed"
    rules = [
        _rule(
            "ACTIVE-DAST-ARTIFACTS-001",
            "Required active DAST input artifacts must be readable.",
            result="passed",
            enforced=True,
            observed={
                "coverage_report": coverage_path.name,
                "zap_reports": [path.name for path in zap_report_paths],
            },
        ),
        _rule(
            "ACTIVE-DAST-TRAFFIC-001",
            "Active DAST must record traffic against at least one targeted endpoint.",
            result="failed" if no_targeted_traffic else "passed",
            enforced=fail_on_no_targeted_traffic,
            observed={"targeted_request_count": targeted_request_count},
        ),
        _rule(
            "ACTIVE-DAST-AUTH-TRAFFIC-001",
            "Active DAST must record authenticated traffic against the target API.",
            result="failed" if no_authenticated_traffic else "passed",
            enforced=fail_on_no_authenticated_traffic,
            observed={"targeted_authenticated_request_count": targeted_authenticated_request_count},
        ),
        _rule(
            "ACTIVE-DAST-COVERAGE-001",
            "Every required active DAST endpoint must be observed.",
            result="failed" if missing_targets else "passed",
            enforced=fail_on_missing_target,
            observed={"missing_targeted_endpoints": missing_targets},
        ),
        _rule(
            "ACTIVE-DAST-AUTH-001",
            "Protected active DAST endpoint traffic must include bearer authorization.",
            result="failed" if endpoints_missing_auth else "passed",
            enforced=fail_on_missing_auth,
            observed={
                "endpoints_missing_auth": endpoints_missing_auth,
                "missing_auth_sample_count": len(missing_auth_samples),
                "missing_auth_samples": missing_auth_samples,
            },
        ),
        _rule(
            "ACTIVE-DAST-ALERTS-001",
            "High and Medium OWASP ZAP alerts are reported but not blocking in the initial active DAST rollout.",
            result="reported" if high_medium_alerts else "passed",
            enforced=False,
            observed={
                "high_medium_alert_count": len(high_medium_alerts),
                "risk_counts": {risk: risk_counts.get(risk, 0) for risk in RISK_LEVELS},
            },
        ),
        _rule(
            "ACTIVE-DAST-5XX-001",
            "5xx responses are reported but not blocking in the initial active DAST rollout.",
            result="reported" if total_5xx_count else "passed",
            enforced=False,
            observed={"targeted_5xx_count": total_5xx_count, "samples_5xx": samples_5xx},
        ),
    ]

    summary = {
        "product": "TokenStream",
        "scanner": "owasp-zap",
        "inventory_type": "active-dast-behavior-summary",
        "scan_mode": scan_mode,
        "generated_at": generated_at,
        "sources": {
            "coverage_report": coverage_path.name,
            "zap_reports": [path.name for path in zap_report_paths],
        },
        "targeted_endpoints": list(unique_targets),
        "zap_options": command_options,
        "coverage": {
            "message_count": coverage.get("summary", {}).get("message_count", len(protected_messages)),
            "protected_message_count": coverage.get("summary", {}).get(
                "protected_message_count", len(protected_messages)
            ),
            "targeted_request_count": targeted_request_count,
            "targeted_authenticated_request_count": targeted_authenticated_request_count,
            "protected_messages_missing_auth_count": sum(endpoint["missing_auth_count"] for endpoint in endpoints),
            "out_of_scope_protected_message_count": out_of_scope_protected_message_count,
            "all_targets_observed": not missing_targets,
            "missing_targeted_endpoints": missing_targets,
            "status_family_counts": _status_family_counts(targeted_status_families),
        },
        "endpoints": endpoints,
        "zap_alerts": {
            "alert_count": len(alerts),
            "alert_instance_count": sum(alert["instance_count"] for alert in alerts),
            "risk_counts": {risk: risk_counts.get(risk, 0) for risk in RISK_LEVELS},
            "confidence_counts": dict(sorted(confidence_counts.items())),
            "high_medium_alert_count": len(high_medium_alerts),
            "top_alerts": alerts[:50],
            "insights": insights,
        },
        "policy": {
            "decision": policy_decision,
            "enforced": any(rule["enforced"] for rule in rules),
            "blocking_failures": blocking_failures,
        },
    }

    policy = {
        "product": "TokenStream",
        "scanner": "owasp-zap",
        "inventory_type": "active-dast-behavior-policy-evaluation",
        "mode": policy_mode,
        "generated_at": generated_at,
        "decision": policy_decision,
        "enforced": any(rule["enforced"] for rule in rules),
        "rules": rules,
        "observed": {
            "scan_mode": scan_mode,
            "targeted_endpoints": list(unique_targets),
            "targeted_request_count": targeted_request_count,
            "targeted_authenticated_request_count": targeted_authenticated_request_count,
            "missing_targeted_endpoints": missing_targets,
            "endpoints_missing_auth": endpoints_missing_auth,
            "targeted_5xx_count": total_5xx_count,
            "high_medium_alert_count": len(high_medium_alerts),
            "risk_counts": {risk: risk_counts.get(risk, 0) for risk in RISK_LEVELS},
        },
    }
    return summary, policy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Derive active DAST behavior evidence from ZAP coverage output.")
    parser.add_argument("--coverage-report", required=True, type=Path)
    parser.add_argument("--zap-report", action="append", default=[], type=Path)
    parser.add_argument("--target-endpoint", action="append", required=True)
    parser.add_argument("--scan-mode", default="active-authenticated-orchestrator-api")
    parser.add_argument("--policy-mode", default="active-authenticated-orchestrator-permissive")
    parser.add_argument("--zap-command", required=True)
    parser.add_argument("--zap-option", action="append", default=[])
    parser.add_argument("--fail-on-missing-target", action="store_true")
    parser.add_argument("--fail-on-missing-auth", action="store_true")
    parser.add_argument("--fail-on-no-authenticated-traffic", action="store_true")
    parser.add_argument("--fail-on-no-targeted-traffic", action="store_true")
    parser.add_argument("--max-samples", type=int, default=10)
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--policy-output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary, policy = build_behavior_evidence(
        coverage_path=args.coverage_report,
        zap_report_paths=args.zap_report,
        target_endpoints=args.target_endpoint,
        scan_mode=args.scan_mode,
        policy_mode=args.policy_mode,
        zap_command=args.zap_command,
        zap_option_pairs=args.zap_option,
        fail_on_missing_target=args.fail_on_missing_target,
        fail_on_missing_auth=args.fail_on_missing_auth,
        fail_on_no_authenticated_traffic=args.fail_on_no_authenticated_traffic,
        fail_on_no_targeted_traffic=args.fail_on_no_targeted_traffic,
        max_samples=args.max_samples,
    )
    _write_json(args.summary_output, summary)
    _write_json(args.policy_output, policy)
    if policy["decision"] != "passed":
        failed = [rule["id"] for rule in policy["rules"] if rule["enforced"] and rule["result"] == "failed"]
        raise SystemExit("Active DAST behavior policy failed: " + ", ".join(failed))


if __name__ == "__main__":
    main()
