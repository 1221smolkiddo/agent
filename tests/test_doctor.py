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
    assert "project-memory" in names
    assert "model-deadlines" in names
    assert "model-fallback" in names
    assert not any("test-key" in check.detail for check in report.checks)


def test_run_doctor_reports_project_memory_health(tmp_path: Path) -> None:
    memory = tmp_path / ".code-agent" / "memory" / "project.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("# Agent47 Project Memory\n\n## Project Conventions\n\n- Use Ruff.\n", encoding="utf-8")
    settings = Settings(openrouter_api_key="test-key", agent_db_path=tmp_path / ".agent.db")

    report = run_doctor(cwd=tmp_path, settings=settings)
    memory_check = next(check for check in report.checks if check.name == "project-memory")

    assert memory_check.status == "pass"
    assert "project.md" in memory_check.detail


def test_run_doctor_reports_selected_provider_key(tmp_path: Path) -> None:
    settings = Settings(
        agent_provider="deepseek",
        agent_model_preset=None,
        deepseek_api_key="deepseek-test-key",
        agent_db_path=tmp_path / ".agent.db",
    )

    report = run_doctor(cwd=tmp_path, settings=settings)
    api_key = next(check for check in report.checks if check.name == "api-key")

    assert api_key.status == "pass"
    assert "provider deepseek" in api_key.detail
    assert "deepseek-test-key" not in api_key.detail


def test_run_doctor_reports_preset_provider_key(tmp_path: Path) -> None:
    settings = Settings(
        agent_model_preset="glm-5.2",
        nvidia_api_key="nvidia-test-key",
        agent_db_path=tmp_path / ".agent.db",
    )

    report = run_doctor(cwd=tmp_path, settings=settings)
    api_key = next(check for check in report.checks if check.name == "api-key")

    assert api_key.status == "pass"
    assert "provider nvidia" in api_key.detail
    assert "nvidia-test-key" not in api_key.detail


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


def test_doctor_reports_optional_missing_fallback_without_blocking_strict_mode(
    tmp_path: Path,
) -> None:
    settings = Settings(openrouter_api_key="test-key", agent_db_path=tmp_path / ".agent.db")

    report = run_doctor(cwd=tmp_path, settings=settings)
    fallback = next(check for check in report.checks if check.name == "model-fallback")

    assert fallback.status == "pass"
    assert "no fallback models" in fallback.detail


def test_doctor_reports_usable_cross_provider_fallback(tmp_path: Path) -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        nvidia_api_key="nvidia-key",
        agent_fallback_models="z-ai/glm-5.2",
        agent_db_path=tmp_path / ".agent.db",
    )

    report = run_doctor(cwd=tmp_path, settings=settings)
    fallback = next(check for check in report.checks if check.name == "model-fallback")

    assert fallback.status == "pass"
    assert "1 configured" in fallback.detail
