import json

from typer.testing import CliRunner

from code_agent.cli import app


runner = CliRunner()


def test_execution_cli_create_show_trace_checkpoint_and_replay(tmp_path):
    db = tmp_path / "executions.db"
    created = runner.invoke(app, [
        "execution", "create", "build engine", "--db", str(db),
        "--token-budget", "5000", "--dollar-budget", "2.5",
    ])
    assert created.exit_code == 0, created.output
    execution_id = created.output.strip()
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
    execution_id = created.output.strip()
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
