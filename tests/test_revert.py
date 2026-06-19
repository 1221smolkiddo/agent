from __future__ import annotations

import hashlib
from pathlib import Path

from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.patches import git_style_unified_diff
from code_agent.revert import apply_revert_plan, build_revert_plan, format_revert_preview
from code_agent.storage import AgentStorage


def test_revert_plan_applies_inverse_patch_and_verifies_files(tmp_path: Path) -> None:
    storage = AgentStorage(tmp_path / "agent.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.md"
    target.write_text("new\n", encoding="utf-8")
    run_id = _record_mutation_run(storage, workspace, "notes.md", before="old\n", after="new\n")

    plan = build_revert_plan(storage, run_id)
    preview = format_revert_preview(plan)
    result = apply_revert_plan(storage, plan, approval_callback=lambda _a, _d, _m: True)

    assert "Revert preview" in preview
    assert "notes.md" in preview
    assert result.ok
    assert result.reverted_run_id == run_id
    assert target.read_text(encoding="utf-8") == "old\n"
    assert storage.get_run(result.run_id) is not None
    steps = storage.run_steps_payloads(result.run_id)
    assert steps[-1]["payload"]["verification"] == "Reverted files match their recorded pre-change state."


def test_revert_denial_leaves_file_unchanged(tmp_path: Path) -> None:
    storage = AgentStorage(tmp_path / "agent.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.md"
    target.write_text("new\n", encoding="utf-8")
    run_id = _record_mutation_run(storage, workspace, "notes.md", before="old\n", after="new\n")
    plan = build_revert_plan(storage, run_id)

    result = apply_revert_plan(storage, plan, approval_callback=lambda _a, _d, _m: False)

    assert not result.ok
    assert "Permission denied for apply_patch" in result.output
    assert target.read_text(encoding="utf-8") == "new\n"


def test_revert_conflict_fails_without_overwriting_user_changes(tmp_path: Path) -> None:
    storage = AgentStorage(tmp_path / "agent.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.md"
    target.write_text("user change\n", encoding="utf-8")
    run_id = _record_mutation_run(storage, workspace, "notes.md", before="old\n", after="new\n")
    plan = build_revert_plan(storage, run_id)

    result = apply_revert_plan(storage, plan, approval_callback=lambda _a, _d, _m: True)

    assert not result.ok
    assert "Patch check failed" in result.output
    assert target.read_text(encoding="utf-8") == "user change\n"


def test_revert_created_file_removes_file(tmp_path: Path) -> None:
    storage = AgentStorage(tmp_path / "agent.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "created.md"
    target.write_text("created\n", encoding="utf-8")
    run_id = _record_mutation_run(
        storage,
        workspace,
        "created.md",
        before="",
        after="created\n",
        before_exists=False,
        after_exists=True,
    )
    plan = build_revert_plan(storage, run_id)

    result = apply_revert_plan(storage, plan, approval_callback=lambda _a, _d, _m: True)

    assert result.ok
    assert not target.exists()


def test_revert_cli_applies_approved_revert(tmp_path: Path) -> None:
    db_path = tmp_path / "agent.db"
    storage = AgentStorage(db_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.md"
    target.write_text("new\n", encoding="utf-8")
    run_id = _record_mutation_run(storage, workspace, "notes.md", before="old\n", after="new\n")
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["revert", str(run_id)],
        input="y\n",
        env={"AGENT_DB_PATH": str(db_path)},
    )

    assert result.exit_code == 0
    assert "Revert preview" in result.output
    assert "Reverted run" in result.output
    assert target.read_text(encoding="utf-8") == "old\n"


def _record_mutation_run(
    storage: AgentStorage,
    workspace: Path,
    path: str,
    *,
    before: str,
    after: str,
    before_exists: bool = True,
    after_exists: bool = True,
) -> int:
    run_id = storage.create_run("change file", "fake-model", workspace)
    storage.add_step(run_id, "assistant", {"type": "apply_patch", "patch": "forward"})
    storage.add_step(
        run_id,
        "tool",
        {
            "type": "tool_result",
            "ok": True,
            "mutation_records": [
                {
                    "action": "apply_patch",
                    "path": path,
                    "ok": True,
                    "verified": True,
                    "exists_before": before_exists,
                    "exists_after": after_exists,
                    "content_changed": True,
                    "before_sha256": _sha(before) if before_exists else None,
                    "after_sha256": _sha(after) if after_exists else None,
                    "inverse_patch": git_style_unified_diff(
                        path,
                        after,
                        before,
                        before_exists=after_exists,
                        after_exists=before_exists,
                    ),
                }
            ],
        },
    )
    return run_id


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
