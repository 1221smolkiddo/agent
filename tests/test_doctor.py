from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import code_agent.cli as cli_module
from code_agent.cli import app
from code_agent.config import Settings
from code_agent.doctor import DoctorCheck, DoctorReport, run_doctor


def test_run_doctor_reports_core_install_checks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    (tmp_path / ".env.example").write_text("OPENROUTER_API_KEY=\n", encoding="utf-8")
    settings = Settings(openrouter_api_key="test-key", agent_db_path=tmp_path / ".agent.db")

    report = run_doctor(cwd=tmp_path, settings=settings)
    names = {check.name for check in report.checks}

    assert report.ok
    assert "python-version" in names
    assert "platform" in names
    assert "workspace-writable" in names
    assert "sqlite-storage" in names
    assert "api-key" in names
    assert "env-file" in names
    assert not any("test-key" in check.detail for check in report.checks)


def test_doctor_json_is_machine_readable() -> None:
    report = DoctorReport(
        platform="Linux",
        python="3.12.0",
        cwd="/tmp/project",
        checks=[DoctorCheck("python-version", "pass", "3.12.0")],
    )

    payload = json.loads(report.to_json())

    assert payload["ok"] is True
    assert payload["checks"] == [
        {"name": "python-version", "status": "pass", "detail": "3.12.0", "hint": ""}
    ]


def test_doctor_strict_fails_on_warnings(monkeypatch, tmp_path: Path) -> None:
    report = DoctorReport(
        platform="Linux",
        python="3.12.0",
        cwd=str(tmp_path),
        checks=[DoctorCheck("api-key", "warn", "not configured")],
    )
    monkeypatch.setattr(cli_module, "run_doctor", lambda cwd=None, settings=None: report)

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--cwd", str(tmp_path), "--strict"])

    assert result.exit_code == 1
    assert "WARN api-key" in result.output


def test_doctor_cli_outputs_json(monkeypatch, tmp_path: Path) -> None:
    report = DoctorReport(
        platform="Linux",
        python="3.12.0",
        cwd=str(tmp_path),
        checks=[DoctorCheck("python-version", "pass", "3.12.0")],
    )
    monkeypatch.setattr(cli_module, "run_doctor", lambda cwd=None, settings=None: report)

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--cwd", str(tmp_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["checks"][0]["name"] == "python-version"
