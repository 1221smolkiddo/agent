from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from .evals import EvalSuiteResult


DEFAULT_REPORT_DIR = Path(".code-agent") / "eval-reports"
DEFAULT_MIN_PASS_RATE = 0.8
DEFAULT_MIN_LIVE_REPORTS = 1


def save_eval_report(
    result: EvalSuiteResult,
    *,
    report_dir: Path = DEFAULT_REPORT_DIR,
    label: str = "evals",
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"{created_at}-{_slug(label)}.json"
    path = report_dir / filename
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        **result.as_dict(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def list_eval_reports(report_dir: Path = DEFAULT_REPORT_DIR) -> list[dict[str, Any]]:
    if not report_dir.exists():
        return []
    reports: list[dict[str, Any]] = []
    for item in load_eval_report_payloads(report_dir):
        path = item["path"]
        payload = item["payload"]
        metrics = payload.get("metrics", {})
        metadata = payload.get("metadata", {})
        failure_categories = _failure_categories(payload)
        reports.append(
            {
                "path": str(path),
                "created_at": str(payload.get("created_at", "")),
                "ok": bool(payload.get("ok", False)),
                "total": int(metrics.get("total", 0)),
                "passed": int(metrics.get("passed", 0)),
                "failed": int(metrics.get("failed", 0)),
                "pass_rate": float(metrics.get("pass_rate", 0.0)),
                "mode": str(metadata.get("mode", "")),
                "model": str(metadata.get("model", "")),
                "provider": str(metadata.get("provider", "")),
                "failure_categories": failure_categories,
            }
        )
    return reports


def load_eval_report_payloads(report_dir: Path = DEFAULT_REPORT_DIR) -> list[dict[str, Any]]:
    if not report_dir.exists():
        return []
    payloads: list[dict[str, Any]] = []
    for path in sorted(report_dir.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        payloads.append({"path": str(path), "payload": payload})
    return payloads


def summarize_eval_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for report in reports:
        key = (
            str(report.get("mode", "")),
            str(report.get("provider", "")),
            str(report.get("model", "")),
        )
        item = grouped.setdefault(
            key,
            {
                "mode": key[0],
                "provider": key[1],
                "model": key[2],
                "reports": 0,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "failure_categories": {},
            },
        )
        item["reports"] += 1
        item["total"] += int(report.get("total", 0))
        item["passed"] += int(report.get("passed", 0))
        item["failed"] += int(report.get("failed", 0))
        for category, count in dict(report.get("failure_categories", {})).items():
            item["failure_categories"][category] = item["failure_categories"].get(category, 0) + int(count)

    summaries = list(grouped.values())
    for item in summaries:
        total = int(item["total"])
        item["pass_rate"] = round(int(item["passed"]) / total, 4) if total else 0.0
    return sorted(
        summaries,
        key=lambda item: (str(item["mode"]), str(item["provider"]), str(item["model"])),
    )


def format_eval_report_index(reports: list[dict[str, Any]]) -> str:
    if not reports:
        return "No eval reports found."
    lines = ["Agent47 eval reports:"]
    for report in reports:
        status = "PASS" if report["ok"] else "FAIL"
        lines.append(
            f"- {status} {report['created_at']} "
            f"{report['passed']}/{report['total']} "
            f"pass_rate={float(report['pass_rate']):.2%} "
            f"mode={report['mode'] or '<unknown>'} "
            f"provider={report['provider'] or '<default>'} "
            f"model={report['model'] or '<default>'} "
            f"path={report['path']}"
        )
    return "\n".join(lines)


def format_eval_report_summary(summaries: list[dict[str, Any]]) -> str:
    if not summaries:
        return "No eval reports found."
    lines = ["Agent47 eval report summary:"]
    for item in summaries:
        failures = _format_failure_categories(dict(item.get("failure_categories", {})))
        lines.append(
            f"- mode={item['mode'] or '<unknown>'} "
            f"provider={item['provider'] or '<default>'} "
            f"model={item['model'] or '<default>'}: "
            f"reports={item['reports']} "
            f"passed={item['passed']}/{item['total']} "
            f"pass_rate={float(item['pass_rate']):.2%} "
            f"failures={failures}"
        )
    return "\n".join(lines)


def build_capability_dashboard(
    report_dir: Path = DEFAULT_REPORT_DIR,
    *,
    min_pass_rate: float = DEFAULT_MIN_PASS_RATE,
    min_live_reports: int = DEFAULT_MIN_LIVE_REPORTS,
) -> dict[str, Any]:
    payloads = load_eval_report_payloads(report_dir)
    reports = list_eval_reports(report_dir)
    summaries = summarize_eval_reports(reports)
    latest = payloads[0] if payloads else None
    previous = payloads[1] if len(payloads) > 1 else None
    live_reports = [report for report in reports if report.get("mode") == "live"]
    latest_live = _latest_report_for_mode(reports, "live")
    report_metrics = [_report_capability_metrics(item["path"], item["payload"]) for item in payloads]
    aggregate = _aggregate_capability_metrics(report_metrics)
    failure_hotspots = _failure_hotspots(payloads)
    gate = _capability_gate(
        reports=reports,
        latest_live=latest_live,
        min_pass_rate=min_pass_rate,
        min_live_reports=min_live_reports,
    )
    dashboard = {
        "report_dir": str(report_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gate": gate,
        "thresholds": {
            "min_pass_rate": min_pass_rate,
            "min_live_reports": min_live_reports,
        },
        "reports": {
            "total": len(reports),
            "live": len(live_reports),
            "offline": sum(1 for report in reports if report.get("mode") == "offline"),
            "latest": _report_identity(latest),
            "previous": _report_identity(previous),
            "latest_delta": _latest_delta(latest, previous),
        },
        "capability": aggregate,
        "models": summaries,
        "failure_hotspots": failure_hotspots,
        "recommendations": _dashboard_recommendations(
            gate=gate,
            reports=reports,
            latest_live=latest_live,
            aggregate=aggregate,
            failure_hotspots=failure_hotspots,
        ),
    }
    return dashboard


def format_capability_dashboard(dashboard: dict[str, Any]) -> str:
    gate = dict(dashboard.get("gate", {}))
    reports = dict(dashboard.get("reports", {}))
    capability = dict(dashboard.get("capability", {}))
    lines = [
        "Agent47 capability dashboard:",
        f"- Gate: {gate.get('status', 'unknown').upper()}",
    ]
    reasons = list(gate.get("reasons", []))
    if reasons:
        lines.append("- Gate reasons: " + "; ".join(str(reason) for reason in reasons))
    lines.append(
        "- Reports: "
        f"total={reports.get('total', 0)} "
        f"live={reports.get('live', 0)} "
        f"offline={reports.get('offline', 0)}"
    )
    latest = reports.get("latest") or {}
    if isinstance(latest, dict) and latest:
        lines.append(
            "- Latest: "
            f"{latest.get('created_at', '<unknown>')} "
            f"mode={latest.get('mode') or '<unknown>'} "
            f"provider={latest.get('provider') or '<default>'} "
            f"model={latest.get('model') or '<default>'} "
            f"pass_rate={float(latest.get('pass_rate', 0.0)):.2%}"
        )
    delta = reports.get("latest_delta") or {}
    if isinstance(delta, dict) and delta:
        lines.append(
            "- Latest delta: "
            f"pass_rate={float(delta.get('pass_rate_delta', 0.0)):+.2%} "
            f"passed={int(delta.get('passed_delta', 0)):+d} "
            f"failed={int(delta.get('failed_delta', 0)):+d}"
        )

    lines.extend(
        [
            "- Capability: "
            f"cases={capability.get('cases', 0)} "
            f"pass_rate={float(capability.get('pass_rate', 0.0)):.2%} "
            f"verification_rate={float(capability.get('verification_rate', 0.0)):.2%} "
            f"change_rate={float(capability.get('change_rate', 0.0)):.2%} "
            f"blocked_rate={float(capability.get('blocked_rate', 0.0)):.2%}",
        ]
    )

    categories = dict(capability.get("categories", {}))
    if categories:
        lines.append("- Categories:")
        for name, item in sorted(categories.items()):
            lines.append(
                f"  - {name}: {item['passed']}/{item['total']} "
                f"pass_rate={float(item['pass_rate']):.2%}"
            )

    hotspots = list(dashboard.get("failure_hotspots", []))
    if hotspots:
        lines.append("- Failure hotspots:")
        for item in hotspots[:8]:
            lines.append(
                f"  - {item['failure_category']}: count={item['count']} "
                f"examples={', '.join(item['examples'])}"
            )

    recommendations = list(dashboard.get("recommendations", []))
    if recommendations:
        lines.append("- Next actions:")
        for item in recommendations:
            lines.append(f"  - {item}")
    return "\n".join(lines)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return cleaned or "evals"


def _failure_categories(payload: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in payload.get("results", []):
        if not isinstance(result, dict) or result.get("ok") is True:
            continue
        category = str(result.get("failure_category") or "unknown")
        counts[category] = counts.get(category, 0) + 1
    return counts


def _format_failure_categories(categories: dict[str, int]) -> str:
    if not categories:
        return "none"
    return ", ".join(f"{name}:{count}" for name, count in sorted(categories.items()))


def _latest_report_for_mode(reports: list[dict[str, Any]], mode: str) -> dict[str, Any] | None:
    for report in reports:
        if report.get("mode") == mode:
            return report
    return None


def _report_identity(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    path = str(item["path"])
    payload = dict(item["payload"])
    metrics = dict(payload.get("metrics", {}))
    metadata = dict(payload.get("metadata", {}))
    return {
        "path": path,
        "created_at": str(payload.get("created_at", "")),
        "ok": bool(payload.get("ok", False)),
        "mode": str(metadata.get("mode", "")),
        "provider": str(metadata.get("provider", "")),
        "model": str(metadata.get("model", "")),
        "total": int(metrics.get("total", 0)),
        "passed": int(metrics.get("passed", 0)),
        "failed": int(metrics.get("failed", 0)),
        "pass_rate": float(metrics.get("pass_rate", 0.0)),
    }


def _latest_delta(
    latest: dict[str, Any] | None,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    latest_identity = _report_identity(latest)
    previous_identity = _report_identity(previous)
    if not latest_identity or not previous_identity:
        return {}
    return {
        "pass_rate_delta": round(
            float(latest_identity["pass_rate"]) - float(previous_identity["pass_rate"]),
            4,
        ),
        "passed_delta": int(latest_identity["passed"]) - int(previous_identity["passed"]),
        "failed_delta": int(latest_identity["failed"]) - int(previous_identity["failed"]),
    }


def _report_capability_metrics(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    metrics = dict(payload.get("metrics", {}))
    metadata = dict(payload.get("metadata", {}))
    results = [item for item in payload.get("results", []) if isinstance(item, dict)]
    cases = len(results)
    verification_cases = 0
    changed_cases = 0
    blocked_cases = 0
    model_usage_cost = 0.0
    model_usage_tokens = 0
    categories: dict[str, dict[str, int]] = {}

    for result in results:
        category = str(result.get("category") or "uncategorized")
        item = categories.setdefault(category, {"total": 0, "passed": 0, "failed": 0})
        item["total"] += 1
        if result.get("ok") is True:
            item["passed"] += 1
        else:
            item["failed"] += 1

        result_metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        commands = result_metadata.get("commands") or []
        verification = result_metadata.get("verification") or []
        if commands or verification:
            verification_cases += 1
        if result_metadata.get("changed_paths"):
            changed_cases += 1
        if result_metadata.get("blocked") is True:
            blocked_cases += 1
        for usage in result_metadata.get("model_usage") or []:
            if not isinstance(usage, dict):
                continue
            model_usage_tokens += int(usage.get("total_tokens") or 0)
            model_usage_cost += float(usage.get("estimated_cost_usd") or 0.0)

    category_rates = {
        name: {
            **item,
            "pass_rate": round(item["passed"] / item["total"], 4) if item["total"] else 0.0,
        }
        for name, item in categories.items()
    }
    return {
        "path": path,
        "created_at": str(payload.get("created_at", "")),
        "mode": str(metadata.get("mode", "")),
        "provider": str(metadata.get("provider", "")),
        "model": str(metadata.get("model", "")),
        "cases": cases,
        "passed": int(metrics.get("passed", 0)),
        "failed": int(metrics.get("failed", 0)),
        "pass_rate": float(metrics.get("pass_rate", 0.0)),
        "verification_cases": verification_cases,
        "changed_cases": changed_cases,
        "blocked_cases": blocked_cases,
        "model_usage_tokens": model_usage_tokens,
        "estimated_cost_usd": round(model_usage_cost, 6),
        "categories": category_rates,
    }


def _aggregate_capability_metrics(report_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    cases = sum(int(item["cases"]) for item in report_metrics)
    passed = sum(int(item["passed"]) for item in report_metrics)
    failed = sum(int(item["failed"]) for item in report_metrics)
    verification_cases = sum(int(item["verification_cases"]) for item in report_metrics)
    changed_cases = sum(int(item["changed_cases"]) for item in report_metrics)
    blocked_cases = sum(int(item["blocked_cases"]) for item in report_metrics)
    model_usage_tokens = sum(int(item["model_usage_tokens"]) for item in report_metrics)
    estimated_cost_usd = sum(float(item["estimated_cost_usd"]) for item in report_metrics)
    categories: dict[str, dict[str, int]] = {}
    for report in report_metrics:
        for name, item in dict(report.get("categories", {})).items():
            category = categories.setdefault(str(name), {"total": 0, "passed": 0, "failed": 0})
            category["total"] += int(item.get("total", 0))
            category["passed"] += int(item.get("passed", 0))
            category["failed"] += int(item.get("failed", 0))
    category_rates = {
        name: {
            **item,
            "pass_rate": round(item["passed"] / item["total"], 4) if item["total"] else 0.0,
        }
        for name, item in categories.items()
    }
    return {
        "cases": cases,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / cases, 4) if cases else 0.0,
        "verification_cases": verification_cases,
        "verification_rate": round(verification_cases / cases, 4) if cases else 0.0,
        "changed_cases": changed_cases,
        "change_rate": round(changed_cases / cases, 4) if cases else 0.0,
        "blocked_cases": blocked_cases,
        "blocked_rate": round(blocked_cases / cases, 4) if cases else 0.0,
        "model_usage_tokens": model_usage_tokens,
        "estimated_cost_usd": round(estimated_cost_usd, 6),
        "categories": category_rates,
    }


def _failure_hotspots(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hotspots: dict[str, dict[str, Any]] = {}
    for item in payloads:
        payload = dict(item["payload"])
        for result in payload.get("results", []):
            if not isinstance(result, dict) or result.get("ok") is True:
                continue
            category = str(result.get("failure_category") or "unknown")
            hotspot = hotspots.setdefault(category, {"failure_category": category, "count": 0, "examples": []})
            hotspot["count"] += 1
            examples = hotspot["examples"]
            if len(examples) < 3:
                examples.append(str(result.get("name") or "<unnamed>"))
    return sorted(hotspots.values(), key=lambda item: (-int(item["count"]), str(item["failure_category"])))


def _capability_gate(
    *,
    reports: list[dict[str, Any]],
    latest_live: dict[str, Any] | None,
    min_pass_rate: float,
    min_live_reports: int,
) -> dict[str, Any]:
    reasons: list[str] = []
    live_count = sum(1 for report in reports if report.get("mode") == "live")
    if live_count < min_live_reports:
        reasons.append(f"needs at least {min_live_reports} saved live eval report(s)")
    if latest_live is None:
        reasons.append("no live eval report found")
    elif float(latest_live.get("pass_rate", 0.0)) < min_pass_rate:
        reasons.append(
            "latest live pass rate "
            f"{float(latest_live.get('pass_rate', 0.0)):.2%} is below {min_pass_rate:.2%}"
        )
    if latest_live is not None and int(latest_live.get("failed", 0)) > 0:
        categories = dict(latest_live.get("failure_categories", {}))
        if categories:
            reasons.append("latest live failures: " + _format_failure_categories(categories))
    return {
        "status": "pass" if not reasons else "fail",
        "reasons": reasons,
    }


def _dashboard_recommendations(
    *,
    gate: dict[str, Any],
    reports: list[dict[str, Any]],
    latest_live: dict[str, Any] | None,
    aggregate: dict[str, Any],
    failure_hotspots: list[dict[str, Any]],
) -> list[str]:
    recommendations: list[str] = []
    if gate.get("status") != "pass":
        recommendations.append("Run `code-agent evals --live --save-report` on the intended release model.")
    if reports and float(aggregate.get("verification_rate", 0.0)) < 0.6:
        recommendations.append("Increase eval validators that require recorded test, lint, build, or command evidence.")
    if latest_live is not None and float(latest_live.get("pass_rate", 0.0)) < DEFAULT_MIN_PASS_RATE:
        recommendations.append("Inspect the latest failing live cases before adding new agent features.")
    if failure_hotspots:
        top = failure_hotspots[0]
        recommendations.append(f"Prioritize the `{top['failure_category']}` failure class; it is the largest hotspot.")
    if not reports:
        recommendations.append("Save an offline report first with `code-agent evals --save-report`.")
    return recommendations
