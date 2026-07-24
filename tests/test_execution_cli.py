import json

from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.durable_execution import Command, DurableExecutionRuntime


runner = CliRunner()


def test_execution_cli_create_show_trace_checkpoint_and_replay(tmp_path):
    db = tmp_path / "executions.db"
    created = runner.invoke(app, [
        "execution", "create", "build engine", "--db", str(db),
        "--token-budget", "5000", "--dollar-budget", "2.5",
    ])
    assert created.exit_code == 0, created.output
    execution_id = created.output.strip().splitlines()[-1]
    shown = runner.invoke(app, ["execution", "show", execution_id, "--db", str(db)])
    assert shown.exit_code == 0
    assert json.loads(shown.output)["goal"] == "build engine"
    checkpoint = runner.invoke(app, [
        "execution", "checkpoint", execution_id, "--db", str(db), "--reason", "test",
    ])
    assert checkpoint.exit_code == 0
    trace = runner.invoke(app, ["execution", "trace", execution_id, "--db", str(db)])
    assert json.loads(trace.output)["event_count"] >= 4
    replay = runner.invoke(app, ["execution", "replay", execution_id, "--db", str(db)])
    assert replay.exit_code == 0
    assert json.loads(replay.output)["id"] == execution_id


def test_execution_cli_pause_resume_and_list(tmp_path):
    db = tmp_path / "executions.db"
    created = runner.invoke(app, ["execution", "create", "goal", "--db", str(db)])
    execution_id = created.output.strip().splitlines()[-1]
    paused = runner.invoke(app, ["execution", "pause", execution_id, "--db", str(db)])
    assert json.loads(paused.output)["status"] == "paused"
    resumed = runner.invoke(app, ["execution", "resume", execution_id, "--db", str(db)])
    assert json.loads(resumed.output)["status"] == "active"
    listed = runner.invoke(app, ["execution", "list", "--db", str(db)])
    assert json.loads(listed.output)[0]["execution_id"] == execution_id


def test_execution_cli_explain_and_shadow_report(tmp_path):
    db = tmp_path / "executions.db"
    created = runner.invoke(app, ["execution", "create", "goal", "--db", str(db)])
    execution_id = created.output.strip()
    explained = runner.invoke(app, ["execution", "explain", execution_id, "--db", str(db)])
    assert explained.exit_code == 0
    assert json.loads(explained.output)["execution"]["compatibility_version"] == "1"
    shadow = runner.invoke(app, [
        "execution", "shadow-report", execution_id, "--db", str(db),
    ])
    assert shadow.exit_code == 0
    assert json.loads(shadow.output)["metrics"]["samples"] == 0
    promotion = runner.invoke(app, ["execution", "promotion-status", "--db", str(db)])
    assert promotion.exit_code == 0
    assert json.loads(promotion.output)["stage"] == "trace_projection"


def test_execution_cli_runtime_status_is_stage_specific(tmp_path):
    db = tmp_path / "executions.db"
    status = runner.invoke(app, [
        "execution", "runtime-status", "--db", str(db), "--minimum-samples", "2",
    ])
    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    assert payload["stage"] == "trace_projection"
    assert payload["next_stage"] == "planning"
    assert payload["required_decision_types"] == ["planning"]
    assert payload["qualification"]["samples"] == 0
    assert not payload["qualification"]["eligible"]


def test_execution_cli_recovers_all_active_effects_without_reexecuting(tmp_path):
    db = tmp_path / "executions.db"
    runtime = DurableExecutionRuntime(db)
    execution_id = runtime.create_planned("goal")
    task_id = next(iter(runtime.engine.state(execution_id).tasks))
    runtime.engine.dispatch(Command("TransitionTask", execution_id, {
        "task_id": task_id, "to": "ready",
    }))
    runtime.engine.dispatch(Command("TransitionTask", execution_id, {
        "task_id": task_id, "to": "running",
    }))
    event = runtime.engine.dispatch(Command("RequestEffect", execution_id, {
        "task_id": task_id,
        "kind": "filesystem",
        "idempotency_key": "effect-1",
        "request": {"action": {"type": "write_file"}},
    }))[0]
    effect_id = event.payload["effect_id"]
    runtime.engine.dispatch(Command("ChangeEffectState", execution_id, {
        "effect_id": effect_id, "state": "running",
    }))

    recovered = runner.invoke(app, [
        "execution", "recover-active", "--db", str(db),
    ])
    assert recovered.exit_code == 0, recovered.output
    payload = json.loads(recovered.output)
    assert payload["recovered"] == [{
        "execution_id": execution_id,
        "status": "active",
        "unknown_effects": [effect_id],
    }]
