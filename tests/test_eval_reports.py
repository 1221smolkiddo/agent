from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.eval_reports import (
    build_capability_dashboard,
    format_capability_dashboard,
    format_eval_report_index,
    format_eval_report_summary,
    list_eval_reports,
    save_eval_report,
    summarize_eval_reports,
)
from code_agent.evals import EvalResult, EvalSuiteResult


def test_save_eval_report_writes_machine_readable_payload(tmp_path: Path) -> None:
    result = EvalSuiteResult(
        results=[
            EvalResult(
                name="live_case",
                ok=False,
                detail="model stopped",
                category="live",
                failure_category="model_error",
                metadata={"changed_paths": ["app.py"]},
            )
        ],
        metadata={"mode": "live", "model": "test-model", "provider": "test-provider"},
    )

    path = save_eval_report(result, report_dir=tmp_path, label="live evals")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.name.endswith("-live-evals.json")
    assert payload["ok"] is False
    assert payload["metadata"]["mode"] == "live"
    assert payload["results"][0]["failure_category"] == "model_error"
    assert payload["results"][0]["metadata"]["changed_paths"] == ["app.py"]


def test_list_eval_reports_summarizes_saved_reports(tmp_path: Path) -> None:
    result = EvalSuiteResult(
        results=[EvalResult(name="case", ok=True, detail="good", category="live")],
        metadata={"mode": "live", "model": "model-a", "provider": "provider-a"},
    )
    save_eval_report(result, report_dir=tmp_path, label="live")

    reports = list_eval_reports(tmp_path)

    assert len(reports) == 1
    assert reports[0]["ok"] is True
    assert reports[0]["mode"] == "live"
    assert reports[0]["model"] == "model-a"
    assert reports[0]["provider"] == "provider-a"
    assert reports[0]["failure_categories"] == {}
    assert "PASS" in format_eval_report_index(reports)


def test_summarize_eval_reports_groups_model_results_and_failures(tmp_path: Path) -> None:
    passing = EvalSuiteResult(
        results=[EvalResult(name="case_a", ok=True, detail="good", category="live")],
        metadata={"mode": "live", "model": "model-a", "provider": "provider-a"},
    )
    failing = EvalSuiteResult(
        results=[
            EvalResult(
                name="case_b",
                ok=False,
                detail="bad",
                category="live",
                failure_category="verification_failed",
            )
        ],
        metadata={"mode": "live", "model": "model-a", "provider": "provider-a"},
    )
    save_eval_report(passing, report_dir=tmp_path, label="first")
    save_eval_report(failing, report_dir=tmp_path, label="second")

    summaries = summarize_eval_reports(list_eval_reports(tmp_path))

    assert summaries == [
        {
            "mode": "live",
            "provider": "provider-a",
            "model": "model-a",
            "reports": 2,
            "total": 2,
            "passed": 1,
            "failed": 1,
            "failure_categories": {"verification_failed": 1},
            "pass_rate": 0.5,
        }
    ]
    assert "verification_failed:1" in format_eval_report_summary(summaries)


def test_cli_eval_reports_lists_saved_reports(tmp_path: Path) -> None:
    result = EvalSuiteResult(
        results=[EvalResult(name="case", ok=True, detail="good", category="fixture")],
        metadata={"mode": "offline"},
    )
    save_eval_report(result, report_dir=tmp_path, label="offline")

    runner = CliRunner()
    output = runner.invoke(app, ["eval-reports", "--report-dir", str(tmp_path)])

    assert output.exit_code == 0, output.output
    assert "Agent47 eval reports:" in output.output
    assert "mode=offline" in output.output


def test_cli_eval_reports_outputs_json(tmp_path: Path) -> None:
    result = EvalSuiteResult(
        results=[EvalResult(name="case", ok=True, detail="good", category="fixture")],
        metadata={"mode": "offline"},
    )
    save_eval_report(result, report_dir=tmp_path, label="offline")

    runner = CliRunner()
    output = runner.invoke(app, ["eval-reports", "--report-dir", str(tmp_path), "--json"])

    assert output.exit_code == 0, output.output
    payload = json.loads(output.output)
    assert payload[0]["mode"] == "offline"


def test_cli_eval_reports_summary_outputs_grouped_json(tmp_path: Path) -> None:
    result = EvalSuiteResult(
        results=[EvalResult(name="case", ok=True, detail="good", category="live")],
        metadata={"mode": "live", "model": "model-a", "provider": "provider-a"},
    )
    save_eval_report(result, report_dir=tmp_path, label="live")

    runner = CliRunner()
    output = runner.invoke(
        app,
        ["eval-reports", "--report-dir", str(tmp_path), "--summary", "--json"],
    )

    assert output.exit_code == 0, output.output
    payload = json.loads(output.output)
    assert payload[0]["reports"] == 1
    assert payload[0]["pass_rate"] == 1.0


def test_capability_dashboard_builds_gate_metrics_and_hotspots(tmp_path: Path) -> None:
    first = {
        "created_at": "2026-07-05T10:00:00+00:00",
        "ok": False,
        "metrics": {"total": 2, "passed": 1, "failed": 1, "pass_rate": 0.5},
        "metadata": {"mode": "live", "provider": "provider-a", "model": "model-a"},
        "results": [
            {
                "name": "python_bugfix",
                "ok": True,
                "detail": "fixed",
                "category": "python",
                "failure_category": None,
                "metadata": {
                    "changed_paths": ["stats.py"],
                    "commands": [{"command": "pytest", "ok": True}],
                    "verification": [{"command": "pytest", "ok": True}],
                    "blocked": False,
                },
            },
            {
                "name": "node_bugfix",
                "ok": False,
                "detail": "tests failed",
                "category": "node",
                "failure_category": "verification_failed",
                "metadata": {"changed_paths": ["total.js"], "commands": [], "verification": []},
            },
        ],
    }
    second = {
        "created_at": "2026-07-05T11:00:00+00:00",
        "ok": True,
        "metrics": {"total": 2, "passed": 2, "failed": 0, "pass_rate": 1.0},
        "metadata": {"mode": "live", "provider": "provider-a", "model": "model-a"},
        "results": [
            {
                "name": "python_bugfix",
                "ok": True,
                "detail": "fixed",
                "category": "python",
                "failure_category": None,
                "metadata": {
                    "changed_paths": ["stats.py"],
                    "commands": [{"command": "pytest", "ok": True}],
                    "verification": [{"command": "pytest", "ok": True}],
                },
            },
            {
                "name": "node_bugfix",
                "ok": True,
                "detail": "fixed",
                "category": "node",
                "failure_category": None,
                "metadata": {
                    "changed_paths": ["total.js"],
                    "commands": [{"command": "npm test", "ok": True}],
                    "verification": [{"command": "npm test", "ok": True}],
                },
            },
        ],
    }
    (tmp_path / "20260705-100000-live.json").write_text(json.dumps(first), encoding="utf-8")
    (tmp_path / "20260705-110000-live.json").write_text(json.dumps(second), encoding="utf-8")

    dashboard = build_capability_dashboard(tmp_path, min_pass_rate=0.8)

    assert dashboard["gate"]["status"] == "pass"
    assert dashboard["reports"]["latest_delta"]["pass_rate_delta"] == 0.5
    assert dashboard["capability"]["cases"] == 4
    assert dashboard["capability"]["verification_rate"] == 0.75
    assert dashboard["capability"]["categories"]["node"]["pass_rate"] == 0.5
    assert dashboard["failure_hotspots"][0]["failure_category"] == "verification_failed"
    formatted = format_capability_dashboard(dashboard)
    assert "Agent47 capability dashboard:" in formatted
    assert "verification_failed" in formatted


def test_cli_eval_reports_dashboard_outputs_json(tmp_path: Path) -> None:
    result = EvalSuiteResult(
        results=[EvalResult(name="case", ok=True, detail="good", category="live")],
        metadata={"mode": "live", "model": "model-a", "provider": "provider-a"},
    )
    save_eval_report(result, report_dir=tmp_path, label="live")

    runner = CliRunner()
    output = runner.invoke(
        app,
        ["eval-reports", "--report-dir", str(tmp_path), "--dashboard", "--json"],
    )

    assert output.exit_code == 0, output.output
    payload = json.loads(output.output)
    assert payload["gate"]["status"] == "pass"
    assert payload["reports"]["live"] == 1
