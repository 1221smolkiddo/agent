from typer.testing import CliRunner
import json

import code_agent.evals as evals_module
from code_agent.agent import AgentRunResult
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
        "prompt_injection_secret_exfiltration_refused",
        "prompt_injection_verification_suppression_ignored",
        "automatic_context_preflight",
        "dirty_worktree_awareness",
        "patch_conflict_recovery",
        "long_horizon_replan_after_regression",
    }


def test_live_eval_cases_cover_real_world_benchmark_shapes() -> None:
    names = {case.name for case in live_eval_cases()}

    assert names == {
        "python_bugfix_with_tests",
        "multi_file_cli_feature",
        "prompt_injection_resilience",
        "prompt_injection_secret_exfiltration_resilience",
        "prompt_injection_verification_suppression_resilience",
        "python_edge_case_regression",
        "dirty_worktree_preservation",
        "docs_from_code_behavior",
        "refactor_preserve_tests",
        "json_config_validation",
        "manual_verification_required",
        "javascript_node_bugfix",
        "changelog_from_diff",
        "security_secret_refusal",
    }


def test_run_builtin_evals_passes() -> None:
    result = run_builtin_evals()

    assert result.ok
    assert result.passed == 27
    assert result.failed == 0
    assert result.metrics["pass_rate"] == 1.0
    assert "Agent47 local evals: 27 passed, 0 failed" in result.format()
    assert "Metrics: pass_rate=100.00%, total=27" in result.format()
    assert "fixture/fix_test" in result.format()


def test_eval_suite_format_reports_failures() -> None:
    result = EvalSuiteResult(
        results=[
            EvalResult(name="safe_case", ok=True, detail="good"),
            EvalResult(
                name="broken_case",
                ok=False,
                detail="bad",
                failure_category="validator_failed",
            ),
        ]
    )

    assert not result.ok
    assert result.format() == (
        "Agent47 local evals: 1 passed, 1 failed\n"
        "Metrics: pass_rate=50.00%, total=2\n"
        "- PASS safety/safe_case: good\n"
        "- FAIL safety/broken_case [validator_failed]: bad"
    )


def test_live_eval_fails_closed_when_agent_run_is_blocked() -> None:
    result = AgentRunResult(
        message="Stopped after a model failure: provider quota exhausted",
        run_id=1,
        blocked=True,
        failed_actions=[{"type": "model_failure", "ok": False}],
    )

    assert evals_module._live_run_failure(result) == (
        "agent run was blocked by a model/provider failure"
    )


def _mock_builtin_eval_result() -> EvalSuiteResult:
    results = [
        EvalResult(name="create_file", ok=True, detail="ok", category="fixture"),
        *[EvalResult(name=f"fixture_case_{i}", ok=True, detail="ok", category="fixture") for i in range(19)],
        *[EvalResult(name=f"safety_case_{i}", ok=True, detail="ok", category="safety") for i in range(7)],
    ]
    return EvalSuiteResult(
        results=results,
        metadata={
            "mode": "offline",
            "benchmark_version": "2026.07-v1",
            "case_count": 27,
        },
    )


def test_cli_evals_command_runs_builtin_evals(monkeypatch) -> None:
    monkeypatch.setattr(evals_module, "run_builtin_evals", _mock_builtin_eval_result)
    runner = CliRunner()

    result = runner.invoke(app, ["evals"])

    assert result.exit_code == 0, result.output
    assert "Agent47 local evals: 27 passed, 0 failed" in result.output
    assert "fixture/create_file" in result.output


def test_cli_evals_command_outputs_json(monkeypatch) -> None:
    monkeypatch.setattr(evals_module, "run_builtin_evals", _mock_builtin_eval_result)
    runner = CliRunner()

    result = runner.invoke(app, ["evals", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["metrics"]["passed"] == 27
    assert payload["metrics"]["categories"]["fixture"]["total"] == 20


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
            "trials": 1,
        }
    ]
    assert "live/python_bugfix_with_tests" in result.output


def test_run_live_evals_repeats_trials_and_versions_benchmark(monkeypatch) -> None:
    calls: list[str] = []

    def fake_case(case, **_kwargs):
        calls.append(case.name)
        return EvalResult(name=case.name, ok=True, detail="ok", category="live")

    monkeypatch.setattr(evals_module, "_run_live_fixture_case", fake_case)

    result = evals_module.run_live_evals(limit=1, trials=3)

    assert calls == ["python_bugfix_with_tests"] * 3
    assert [item.name for item in result.results] == [
        "python_bugfix_with_tests::trial-1",
        "python_bugfix_with_tests::trial-2",
        "python_bugfix_with_tests::trial-3",
    ]
    assert [item.metadata["trial"] for item in result.results if item.metadata] == [1, 2, 3]
    assert result.metadata["benchmark_version"] == "2026.07-v1"
    assert result.metadata["run_count"] == 3


def test_cli_evals_save_report_writes_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(evals_module, "run_builtin_evals", _mock_builtin_eval_result)
    runner = CliRunner()

    result = runner.invoke(app, ["evals", "--save-report", "--report-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    reports = list(tmp_path.glob("*.json"))
    assert len(reports) == 1
    assert "Eval report saved:" in result.output
