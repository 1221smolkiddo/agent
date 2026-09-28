"""Paired, repeatable A/B or A/B/C qualification with allowlisted metrics."""
from __future__ import annotations

import json
import math
import os
import re
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
    "input_tokens", "output_tokens", "reflect_requests", "reflect_latency_seconds",
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
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                result[key] = value if math.isfinite(value) and value >= 0 else None
            except OverflowError:
                result[key] = None
        else:
            result[key] = None
    return result



def _real_model_environment(source: dict[str, Any], cases: list[Any], *,
                            include_reflect: bool, timeout_seconds: float, trials: int) -> dict[str, str]:
    """Paid trials stay held until exact core, reviewed seeds, and external cost controls exist."""
    sha = source.get("final_core_sha")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("A reviewed final core SHA is required for real-model trials.")
    limits = source.get("limits")
    if not isinstance(limits, dict) or not include_reflect or trials != 1 or len(cases) > 6:
        raise ValueError("Real-model qualification requires a bounded single A/B/C trial.")
    for name, maximum in (("max_steps", 8), ("max_output_tokens", 1024), ("max_context_chars", 12000)):
        value = limits.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or not (8000 if name == "max_context_chars" else 1) <= value <= maximum:
            raise ValueError("Real-model runner limits are missing or excessive.")
    cap = limits.get("provider_budget_usd")
    if (not isinstance(cap, (int, float)) or isinstance(cap, bool)
            or not 0 < cap <= 5 or limits.get("provider_budget_enforced") is not True
            or source.get("historical_seed_snapshot_reviewed") is not True
            or timeout_seconds > 120):
        raise ValueError("Reviewed frozen seeds and an enforced provider cost ceiling are required.")
    repository = Path(__file__).resolve().parents[2]
    approved = subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"],
                              cwd=repository, capture_output=True, check=False, timeout=5)
    if approved.returncode:
        raise ValueError("Qualification checkout does not contain the approved final core.")
    difference = subprocess.run([
        "git", "diff", sha, "--", "src/code_agent/agent.py", "src/code_agent/execution_host.py",
        "src/code_agent/experience_memory",
    ], cwd=repository, capture_output=True, check=True, timeout=5)
    if difference.stdout:
        raise ValueError("Core behavior differs from the approved final core.")
    return {
        "AGENT47_EVAL_APPROVED_CORE_SHA": sha,
        "AGENT47_EVAL_MAX_STEPS": str(limits["max_steps"]),
        "AGENT_MAX_TOKENS": str(limits["max_output_tokens"]),
        "AGENT_CONTEXT_MAX_CHARS": str(limits["max_context_chars"]),
        "AGENT_MODEL_RETRY_COUNT": "0", "AGENT_MAX_FAILURES": "3",
        "AGENT_REVIEWER_PASS": "false", "AGENT_SHADOW_PLANNER": "deterministic",
        "AGENT_STREAM": "false", "AGENT_FALLBACK_MODELS": "",
        "AGENT_RUN_TIMEOUT_SECONDS": str(min(120, timeout_seconds)),
    }


def run_evaluation(
    manifest: Path, runner: list[str], output: Path, *, timeout_seconds: float = 600.0,
    trials: int = 1, include_reflect: bool = False,
) -> dict[str, Any]:
    """Runner writes JSON metrics to AGENT47_EVAL_METRICS; stdout is never persisted."""
    if not runner or not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or not 1 <= trials <= 100:
        raise ValueError("A runner command and positive timeout are required.")
    source = json.loads(manifest.read_text(encoding="utf-8"))
    cases = source.get("scenarios") if isinstance(source, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("Manifest requires a nonempty scenarios list.")
    real_environment = _real_model_environment(
        source, cases, include_reflect=include_reflect, timeout_seconds=timeout_seconds, trials=trials,
    ) if source.get("runner_kind") == "real_model" else {}
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
        if include_reflect:
            rotation = index % 3
            arms = ("A", "B", "C")[rotation:] + ("A", "B", "C")[:rotation]
        else:
            arms = ("A", "B") if index % 2 == 0 else ("B", "A")
        arm_results = {}
        for arm in arms:
            with tempfile.TemporaryDirectory(prefix="agent47-memory-eval-") as temp:
                workspace = Path(temp) / "workspace"
                shutil.copytree(fixture, workspace, ignore=shutil.ignore_patterns(".code-agent", ".env", ".env.*", "__pycache__"))
                metrics_file = Path(temp) / "metrics.json"
                env = os.environ.copy()
                env.update(real_environment)
                env.update({
                    "AGENT_EXPERIENCE_MEMORY_ENABLED": "true" if arm in {"B", "C"} else "false",
                    "AGENT_EXPERIENCE_MEMORY_AUTOMATIC_RECALL_MAX_REQUESTS": "1" if arm in {"B", "C"} else "0",
                    "AGENT47_EVAL_TASK": task,
                    "AGENT47_EVAL_METRICS": str(metrics_file),
                    "AGENT47_EVAL_SCENARIO": name,
                    "AGENT47_EVAL_ARM": arm,
                    "AGENT47_EVAL_TRIAL": str(trial),
                    "AGENT_DB_PATH": str(workspace / ".code-agent" / "eval.db"),
                    "AGENT_EXPERIENCE_MEMORY_AUTOMATIC_REFLECT_ENABLED": "true" if arm == "C" else "false",
                    "AGENT_EXPERIENCE_MEMORY_AUTOMATIC_REFLECT_MAX_REQUESTS": "1" if arm == "C" else "0",
                })
                if source.get("runner_kind") == "offline_scripted_synthetic" and include_reflect:
                    raise ValueError("A/B/C requires a reviewed external runner on the final core.")
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
        "schema_version": 1, "design": "paired_fresh_fixture_abc" if include_reflect else "paired_fresh_fixture_ab",
        "metric_definitions": list(METRICS), "results": results,
        "trials": trials, "reflect_enabled": include_reflect, "runner_kind": source.get("runner_kind") if source.get("runner_kind") in {"offline_scripted_synthetic", "real_model"} else "external",
        "conclusion": "No superiority claim; inspect measured per-scenario outcomes.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    human_path = output.with_suffix(".md") if output.suffix != ".md" else output.with_name(output.name + ".md")
    human_path.write_text(human_report(report) + "\n", encoding="utf-8")
    return report


def human_report(report: dict[str, Any]) -> str:
    include_reflect = report.get("reflect_enabled", False)
    rows = ["Memory A/B/C evaluation" if include_reflect else "Memory A/B evaluation",
            "A=disabled; B=selective recall" + ("; C=recall plus Reflect" if include_reflect else ""), ""]
    for result in report["results"]:
        a = result["arms"]["A"]
        b = result["arms"]["B"]
        line = (
            f"{result['scenario']}: A verified={a['metrics']['verified_completion']}, "
            f"B verified={b['metrics']['verified_completion']}; "
            f"A error={a['error'] or 'none'}, B error={b['error'] or 'none'}"
        )
        if include_reflect:
            c = result["arms"]["C"]
            line += f"; C verified={c['metrics']['verified_completion']}, C error={c['error'] or 'none'}"
        rows.append(line)
    rows.extend(["", report["conclusion"]])
    return "\n".join(rows)
