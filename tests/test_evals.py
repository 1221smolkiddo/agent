from typer.testing import CliRunner

import code_agent.cli as cli
from code_agent.cli import app
from code_agent.evals import (
    EvalResult,
    EvalSuiteResult,
    builtin_eval_cases,
    builtin_fixture_eval_cases,
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
        "denied_delete_no_success_claim",
        "delete_file",
        "apply_patch_create_file",
        "manual_verification_command",
        "sensitive_file_refusal",
        "prompt_injection_file_is_untrusted",
    }


def test_run_builtin_evals_passes() -> None:
    result = run_builtin_evals()

    assert result.ok
    assert result.passed == 19
    assert result.failed == 0
    assert "Agent47 local evals: 19 passed, 0 failed" in result.format()
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
        "- PASS safety/safe_case: good\n"
        "- FAIL safety/broken_case: bad"
    )


def test_cli_evals_command_runs_builtin_evals() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["evals"])

    assert result.exit_code == 0
    assert "Agent47 local evals: 19 passed, 0 failed" in result.output
    assert "fixture/create_file" in result.output


def test_cli_evals_command_exits_nonzero_when_any_eval_fails(monkeypatch) -> None:
    runner = CliRunner()
    failed_result = EvalSuiteResult(
        results=[
            EvalResult(name="broken_case", ok=False, detail="bad"),
        ]
    )
    monkeypatch.setattr(cli, "run_builtin_evals", lambda: failed_result)

    result = runner.invoke(app, ["evals"])

    assert result.exit_code == 1
    assert "- FAIL safety/broken_case: bad" in result.output
