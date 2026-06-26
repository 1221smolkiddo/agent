from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from .evals import EvalSuiteResult


DEFAULT_REPORT_DIR = Path(".code-agent") / "eval-reports"


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
    for path in sorted(report_dir.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
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
