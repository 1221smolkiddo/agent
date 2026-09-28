"""Paired, repeatable A/B qualification runner for Hindsight recall."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

SCENARIOS = (
    ("repeated_bug_class", True),
    ("recurring_ci_failure", True),
    ("rejected_approach_later", True),
    ("multi_session_migration", True),
    ("release_rollback_lesson", True),
    ("architecture_decision_recall", True),
    ("simple_rename", False),
    ("isolated_simple_bug", False),
)
METRICS = (
    "verified_completion", "first_pass_verification", "steps_to_verified_solution",
    "model_calls", "tool_calls", "repository_reads", "verification_commands",
    "repeated_failed_strategies", "recall_latency_seconds", "memory_context_bytes",
    "stale_recalls", "irrelevant_recalls", "total_run_latency_seconds", "recall_requests",
    "input_tokens", "output_tokens",
)
BOOL_METRICS = {"verified_completion", "first_pass_verification"}


def catalog() -> list[dict[str, Any]]:
    return [{"name": name, "expected_recall": recall} for name, recall in SCENARIOS]


def _metric_view(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    result: dict[str, Any] = {}
    for key in METRICS:
        value = raw.get(key)
        if key in BOOL_METRICS:
            result[key] = value if isinstance(value, bool) else None
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            result[key] = value
        else:
            result[key] = None
    return result


def run_evaluation(
    manifest: Path, runner: list[str], output: Path, *, timeout_seconds: float = 600.0,
    trials: int = 1,
) -> dict[str, Any]:
    """Runner writes JSON metrics to AGENT47_EVAL_METRICS; stdout is never persisted."""
    if not runner or not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or not 1 <= trials <= 100:
        raise ValueError("A runner command and positive timeout are required.")
    source = json.loads(manifest.read_text(encoding="utf-8"))
    cases = source.get("scenarios") if isinstance(source, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("Manifest requires a nonempty scenarios list.")
    names = dict(SCENARIOS)
    results = []
    expanded = [(trial, case) for trial in range(1, trials + 1) for case in cases]
    for index, (trial, case) in enumerate(expanded):
        if not isinstance(case, dict) or case.get("name") not in names:
            raise ValueError("Unknown evaluation scenario.")
        name = case["name"]
        task = case.get("task")
        fixture_name = case.get("fixture")
        if not isinstance(task, str) or not task.strip() or not isinstance(fixture_name, str):
            raise ValueError("Each scenario needs a task and fixture.")
        fixture = (manifest.parent / fixture_name).resolve()
        if not fixture.is_dir() or not fixture.is_relative_to(manifest.parent.resolve()):
            raise ValueError("Fixture must be a directory within the manifest tree.")
        arms = ("A", "B") if index % 2 == 0 else ("B", "A")
        arm_results = {}
        for arm in arms:
            with tempfile.TemporaryDirectory(prefix="agent47-memory-eval-") as temp:
                workspace = Path(temp) / "workspace"
                shutil.copytree(fixture, workspace, ignore=shutil.ignore_patterns(".code-agent", ".env", ".env.*", "__pycache__"))
                metrics_file = Path(temp) / "metrics.json"
                env = os.environ.copy()
                env.update({
                    "AGENT_EXPERIENCE_MEMORY_ENABLED": "true" if arm == "B" else "false",
                    "AGENT_EXPERIENCE_MEMORY_AUTOMATIC_RECALL_MAX_REQUESTS": "1" if arm == "B" else "0",
                    "AGENT47_EVAL_TASK": task,
                    "AGENT47_EVAL_METRICS": str(metrics_file),
                    "AGENT47_EVAL_SCENARIO": name,
                    "AGENT47_EVAL_ARM": arm,
                    "AGENT47_EVAL_TRIAL": str(trial),
                    "AGENT_DB_PATH": str(workspace / ".code-agent" / "eval.db"),
                    "AGENT_EXPERIENCE_MEMORY_AUTOMATIC_REFLECT_ENABLED": "false",
                    "AGENT_EXPERIENCE_MEMORY_AUTOMATIC_REFLECT_MAX_REQUESTS": "0",
                })
                if source.get("runner_kind") == "offline_scripted_synthetic":
                    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
                started = time.monotonic()
                try:
                    completed = subprocess.run(
                        runner, cwd=workspace, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=timeout_seconds, check=False,
                    )
                    exit_code: int | None = completed.returncode
                    error = "" if exit_code == 0 else "runner_failed"
                except subprocess.TimeoutExpired:
                    exit_code = None
                    error = "timeout"
                except OSError:
                    exit_code = None
                    error = "runner_unavailable"
                elapsed = time.monotonic() - started
                try:
                    raw = json.loads(metrics_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    raw = {}
                    if not error:
                        error = "metrics_missing"
                if not isinstance(raw, dict):
                    raw = {}
                    error = error or "metrics_invalid"
                metrics = _metric_view(raw)
                if metrics["total_run_latency_seconds"] is None:
                    metrics["total_run_latency_seconds"] = elapsed
                arm_results[arm] = {
                    "exit_code": exit_code, "error": error, "metrics": metrics,
                }
        results.append({
            "scenario": name, "trial": trial, "expected_recall": names[name], "order": list(arms),
            "arms": arm_results,
        })
    report = {
        "schema_version": 1, "design": "paired_fresh_fixture_ab",
        "metric_definitions": list(METRICS), "results": results,
        "trials": trials, "reflect_enabled": False, "runner_kind": "offline_scripted_synthetic" if source.get("runner_kind") == "offline_scripted_synthetic" else "external",
        "conclusion": "No superiority claim; inspect measured per-scenario outcomes.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    human_path = output.with_suffix(".md") if output.suffix != ".md" else output.with_name(output.name + ".md")
    human_path.write_text(human_report(report) + "\n", encoding="utf-8")
    return report


def human_report(report: dict[str, Any]) -> str:
    rows = ["Memory A/B evaluation", "A=disabled; B=selective recall", ""]
    for result in report["results"]:
        a = result["arms"]["A"]
        b = result["arms"]["B"]
        rows.append(
            f"{result['scenario']}: A verified={a['metrics']['verified_completion']}, "
            f"B verified={b['metrics']['verified_completion']}; "
            f"A error={a['error'] or 'none'}, B error={b['error'] or 'none'}"
        )
    rows.extend(["", report["conclusion"]])
    return "\n".join(rows)
