from typer.testing import CliRunner
import json

import code_agent.evals as evals_module
from code_agent.cli import app
from code_agent.evals import (
    EvalResult,
    EvalSuiteResult,
    builtin_eval_cases,
    builtin_fixture_eval_cases,
    live_eval_cases,
    run_builtin_evals,
)


def test_builtin_eval_cases_cover_public_safety_regressions() -> None:
    names = {case.name for case in builtin_eval_cases()}

    assert names == {
        "greeting_without_workspace_inspection",
        "blocked_write_no_success_claim",
        "denied_read_no_content_leak",
        "sandbox_write_does_not_touch_base",
        "shell_compound_command_blocked",
        "shell_inline_code_blocked",
        "shell_env_scrubs_secrets",
    }


def test_builtin_fixture_eval_cases_cover_coding_behaviors() -> None:
    names = {case.name for case in builtin_fixture_eval_cases()}

    assert names == {
        "create_file",
        "edit_file",
        "fix_test",
        "recover_after_failed_read",
        "recover_after_failed_verification",
        "multi_file_import_fix",
        "diagnose_pytest_failure_context",
        "denied_delete_no_success_claim",
        "delete_file",
        "apply_patch_create_file",
        "manual_verification_command",
        "verification_false_pass_claim",
        "sensitive_file_refusal",
        "prompt_injection_file_is_untrusted",
        "automatic_context_preflight",
        "dirty_worktree_awareness",
        "patch_conflict_recovery",
    }


def test_live_eval_cases_cover_real_world_benchmark_shapes() -> None:
    names = {case.name for case in live_eval_cases()}

    assert names == {
        "python_bugfix_with_tests",
        "multi_file_cli_feature",
        "prompt_injection_resilience",
    }


def test_run_builtin_evals_passes() -> None:
    result = run_builtin_evals()

    assert result.ok
    assert result.passed == 24
    assert result.failed == 0
    assert result.metrics["pass_rate"] == 1.0
    assert "Agent47 local evals: 24 passed, 0 failed" in result.format()
    assert "Metrics: pass_rate=100.00%, total=24" in result.format()
    assert "fixture/fix_test" in result.format()


def test_eval_suite_format_reports_failures() -> None:
    result = EvalSuiteResult(
        results=[
            EvalResult(name="safe_case", ok=True, detail="good"),
            EvalResult(name="broken_case", ok=False, detail="bad"),
        ]
    )

    assert not result.ok
    assert result.format() == (
        "Agent47 local evals: 1 passed, 1 failed\n"
        "Metrics: pass_rate=50.00%, total=2\n"
        "- PASS safety/safe_case: good\n"
        "- FAIL safety/broken_case: bad"
    )


def test_cli_evals_command_runs_builtin_evals() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["evals"])

    assert result.exit_code == 0, result.output
    assert "Agent47 local evals: 24 passed, 0 failed" in result.output
    assert "fixture/create_file" in result.output


def test_cli_evals_command_outputs_json() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["evals", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["metrics"]["passed"] == 24
    assert payload["metrics"]["categories"]["fixture"]["total"] == 17


def test_cli_evals_command_exits_nonzero_when_any_eval_fails(monkeypatch) -> None:
    runner = CliRunner()
    failed_result = EvalSuiteResult(
        results=[
            EvalResult(name="broken_case", ok=False, detail="bad"),
        ]
    )
    monkeypatch.setattr(evals_module, "run_builtin_evals", lambda: failed_result)

    result = runner.invoke(app, ["evals"])

    assert result.exit_code == 1
    assert "- FAIL safety/broken_case: bad" in result.output


def test_cli_evals_live_command_uses_live_runner(monkeypatch) -> None:
    runner = CliRunner()
    live_result = EvalSuiteResult(
        results=[
            EvalResult(name="python_bugfix_with_tests", ok=True, detail="good", category="live"),
        ]
    )
    calls = []

    def fake_run_live_evals(**kwargs):
        calls.append(kwargs)
        return live_result

    monkeypatch.setattr(evals_module, "run_live_evals", fake_run_live_evals)

    result = runner.invoke(app, ["evals", "--live", "--limit", "1", "--profile", "coder"])

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "model": None,
            "provider": None,
            "preset": None,
            "profile": "coder",
            "limit": 1,
        }
    ]
    assert "live/python_bugfix_with_tests" in result.output
