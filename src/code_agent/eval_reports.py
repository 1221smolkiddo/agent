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
            }
        )
    return reports


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


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return cleaned or "evals"
