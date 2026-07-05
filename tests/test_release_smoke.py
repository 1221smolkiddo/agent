from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

import code_agent.cli as cli_module
from code_agent.cli import app
from code_agent.release_smoke import SmokeCheck, SmokeCommand, SmokeReport, run_release_smoke


def test_run_release_smoke_runs_commands_until_failure(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(command, cwd, text, capture_output, check):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if "bad" in command else 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    report = run_release_smoke(
        tmp_path,
        commands=[
            SmokeCommand("good", ("good",)),
            SmokeCommand("bad", ("bad",)),
            SmokeCommand("skipped", ("skipped",)),
        ],
    )

    assert not report.ok
    assert calls == [["good"], ["bad"]]
    assert [check.name for check in report.checks] == ["good", "bad"]
    assert "FAIL bad" in report.format_text()


def test_run_release_smoke_json_shape(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, cwd, text, capture_output, check):
        return subprocess.CompletedProcess(command, 0, "done", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    report = run_release_smoke(tmp_path, commands=[SmokeCommand("unit-tests", ("pytest",))])
    payload = json.loads(report.to_json())

    assert payload["ok"] is True
    assert payload["checks"][0]["name"] == "unit-tests"
    assert payload["checks"][0]["command"] == ["pytest"]


def test_cli_release_smoke_command_outputs_report(monkeypatch, tmp_path: Path) -> None:
    report = SmokeReport(
        checks=[
            SmokeCheck(
                name="unit-tests",
                command=["uv", "run", "pytest"],
                ok=True,
                returncode=0,
                output="passed",
            )
        ]
    )
    monkeypatch.setattr(cli_module, "run_release_smoke", lambda cwd, include_build=True: report)

    runner = CliRunner()
    result = runner.invoke(app, ["release-smoke", "--cwd", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "PASS unit-tests" in result.output


def test_cli_release_smoke_command_exits_nonzero_when_blocked(monkeypatch, tmp_path: Path) -> None:
    report = SmokeReport(
        checks=[
            SmokeCheck(
                name="unit-tests",
                command=["uv", "run", "pytest"],
                ok=False,
                returncode=1,
                output="failed",
            )
        ]
    )
    monkeypatch.setattr(cli_module, "run_release_smoke", lambda cwd, include_build=True: report)

    runner = CliRunner()
    result = runner.invoke(app, ["release-smoke", "--cwd", str(tmp_path), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False


def test_cli_release_smoke_can_require_dashboard_gate(monkeypatch, tmp_path: Path) -> None:
    report = SmokeReport(
        checks=[
            SmokeCheck(
                name="unit-tests",
                command=["uv", "run", "pytest"],
                ok=True,
                returncode=0,
                output="passed",
            )
        ]
    )
    dashboard = {
        "gate": {"status": "blocked", "reasons": ["provider quota"]},
        "reports": {"total": 1, "live": 1, "offline": 0},
        "capability": {"cases": 3, "pass_rate": 0.0},
        "failure_hotspots": [],
        "recommendations": ["switch model"],
    }
    monkeypatch.setattr(cli_module, "run_release_smoke", lambda cwd, include_build=True: report)
    monkeypatch.setattr(cli_module, "build_capability_dashboard", lambda report_dir: dashboard)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "release-smoke",
            "--cwd",
            str(tmp_path),
            "--require-dashboard",
            "--report-dir",
            str(tmp_path / "reports"),
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["capability_dashboard"]["gate"]["status"] == "blocked"
