from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.evals import EvalResult, EvalSuiteResult, builtin_eval_cases, run_builtin_evals


def test_builtin_eval_cases_cover_public_safety_regressions() -> None:
    names = {case.name for case in builtin_eval_cases()}

    assert names == {
        "greeting_without_workspace_inspection",
        "blocked_write_no_success_claim",
        "denied_read_no_content_leak",
        "sandbox_write_does_not_touch_base",
    }


def test_run_builtin_evals_passes() -> None:
    result = run_builtin_evals()

    assert result.ok
    assert result.passed == 4
    assert result.failed == 0
    assert "Agent47 local evals: 4 passed, 0 failed" in result.format()


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
        "- PASS safe_case: good\n"
        "- FAIL broken_case: bad"
    )


def test_cli_evals_command_runs_builtin_evals() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["evals"])

    assert result.exit_code == 0
    assert "Agent47 local evals: 4 passed, 0 failed" in result.output
