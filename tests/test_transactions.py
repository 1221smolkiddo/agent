from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from code_agent.cli import app
from code_agent.schema import (
    MoveFileAction,
    RedoTransactionAction,
    RestoreSnapshotAction,
    UndoTransactionAction,
    WriteFileAction,
)
from code_agent.tools import ToolRegistry
from code_agent.transactions import (
    FileMutation,
    TransactionConflict,
    TransactionError,
    WorkspaceTransactionManager,
)


def _text_mutation(
    manager: WorkspaceTransactionManager,
    path: str,
    content: str,
) -> FileMutation:
    state, _before = manager.read_text(path)
    return FileMutation(
        path=state.path,
        base=state,
        before=state,
        after_exists=True,
        after_bytes=manager.encode_text(content, state),
        operation="update" if state.exists else "create",
    )


def test_text_transaction_preserves_encoding_line_endings_mode_and_timestamp(
    tmp_path: Path,
) -> None:
    target = tmp_path / "notes.txt"
    target.write_bytes("old\r\nline\r\n".encode("utf-16"))
    os.chmod(target, 0o640)
    original_stat = target.stat()
    manager = WorkspaceTransactionManager(tmp_path)
    state, before = manager.read_text("notes.txt")

    plan = manager.plan_text(
        "write_file",
        "notes.txt",
        before.replace("old", "new"),
        expected=state,
    )
    result = plan.commit()

    assert result.ok
    data = target.read_bytes()
    assert data.startswith(b"\xff\xfe")
    assert "new\r\nline\r\n" == data.decode("utf-16")
    assert stat.S_IMODE(target.stat().st_mode) == stat.S_IMODE(original_stat.st_mode)
    assert target.stat().st_mtime_ns == original_stat.st_mtime_ns
    journal = manager.load_journal(result.transaction_id)
    assert journal["state"] == "committed"
    assert journal["entries"][0]["before"]["sha256"]
    assert journal["entries"][0]["after"]["sha256"]


def test_read_text_normalizes_windows_newlines_for_exact_edits(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_bytes(b"def first():\r\n    return 1\r\n\r\ndef second():\r\n    return 2\r\n")
    manager = WorkspaceTransactionManager(tmp_path)

    state, text = manager.read_text("module.py")
    assert "\r" not in text
    updated = text.replace(
        "def second():\n    return 2",
        "def second():\n    return 3",
    )
    result = manager.plan_text("edit_file", "module.py", updated, expected=state).commit()

    assert result.ok
    assert target.read_bytes() == (
        b"def first():\r\n    return 1\r\n\r\ndef second():\r\n    return 3\r\n"
    )


def test_multi_file_transaction_rolls_back_when_second_write_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
    manager = WorkspaceTransactionManager(tmp_path)
    plan = manager.plan(
        "test",
        [
            _text_mutation(manager, "a.txt", "A\n"),
            _text_mutation(manager, "b.txt", "B\n"),
        ],
    )
    original_apply = manager._apply_entry

    def fail_second(plan_value, entry):
        if entry.path == "b.txt":
            raise OSError("injected write failure")
        original_apply(plan_value, entry)

    monkeypatch.setattr(manager, "_apply_entry", fail_second)

    result = plan.commit()

    assert not result.ok
    assert result.state == "rolled_back"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "a\n"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "b\n"


def test_failed_transaction_validator_rolls_back_all_files(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("old\n", encoding="utf-8")
    manager = WorkspaceTransactionManager(tmp_path)
    plan = manager.plan_text("formatting", "app.py", "formatted\n")

    result = plan.commit(validator=lambda _plan: (False, "formatter validation failed"))

    assert not result.ok
    assert result.state == "rolled_back"
    assert target.read_text(encoding="utf-8") == "old\n"


def test_tool_registry_validator_rolls_back_workspace_mutation(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("old\n", encoding="utf-8")
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda *_args: True,
        transaction_validator=lambda _plan: (False, "workspace validation failed"),
    )

    result = tools.run(
        WriteFileAction(type="write_file", path="app.py", content="new\n")
    )

    assert not result.ok
    assert result.metadata["transaction"]["state"] == "rolled_back"
    assert target.read_text(encoding="utf-8") == "old\n"


def test_rollback_refuses_to_overwrite_newer_user_edit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
    manager = WorkspaceTransactionManager(tmp_path)
    plan = manager.plan(
        "test",
        [
            _text_mutation(manager, "a.txt", "A\n"),
            _text_mutation(manager, "b.txt", "B\n"),
        ],
    )
    original_apply = manager._apply_entry

    def conflict_after_first(plan_value, entry):
        if entry.path == "b.txt":
            (tmp_path / "a.txt").write_text("newer user edit\n", encoding="utf-8")
            raise OSError("injected failure")
        original_apply(plan_value, entry)

    monkeypatch.setattr(manager, "_apply_entry", conflict_after_first)

    result = plan.commit()

    assert not result.ok
    assert result.state == "rollback_conflict"
    assert result.conflicts == ("a.txt",)
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "newer user edit\n"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "b\n"


def test_startup_recovers_partially_applied_transaction(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("old\n", encoding="utf-8")
    manager = WorkspaceTransactionManager(tmp_path)
    plan = manager.plan_text("edit_file", "app.py", "new\n")
    plan.entries = manager._preflight(plan.entries)
    plan.state = "applying"
    manager._write_plan(plan, applied_paths=[])
    manager._apply_entry(plan, plan.entries[0])
    manager._write_plan(plan, applied_paths=["app.py"])
    assert target.read_text(encoding="utf-8") == "new\n"

    recovered_manager = WorkspaceTransactionManager(tmp_path)

    assert target.read_text(encoding="utf-8") == "old\n"
    assert recovered_manager.consume_recovery_results()[0].recovered
    assert recovered_manager.load_journal(plan.transaction_id)["state"] == (
        "recovered_rolled_back"
    )


def test_non_overlapping_external_edit_uses_three_way_merge(tmp_path: Path) -> None:
    target = tmp_path / "app.txt"
    target.write_text("first\nsecond\n", encoding="utf-8")
    manager = WorkspaceTransactionManager(tmp_path)
    state, _ = manager.read_text("app.txt")
    plan = manager.plan_text(
        "edit_file",
        "app.txt",
        "FIRST\nsecond\n",
        expected=state,
    )
    target.write_text("first\nSECOND\n", encoding="utf-8")

    result = plan.commit()

    assert result.ok
    assert target.read_text(encoding="utf-8") == "FIRST\nSECOND\n"
    journal = manager.load_journal(result.transaction_id)
    assert journal["entries"][0]["before"]["sha256"] != journal["entries"][0]["base"]["sha256"]


def test_overlapping_external_edit_is_preserved_as_conflict(tmp_path: Path) -> None:
    target = tmp_path / "app.txt"
    target.write_text("value = 1\n", encoding="utf-8")
    manager = WorkspaceTransactionManager(tmp_path)
    state, _ = manager.read_text("app.txt")
    plan = manager.plan_text(
        "edit_file",
        "app.txt",
        "value = 2\n",
        expected=state,
    )
    target.write_text("value = 3\n", encoding="utf-8")

    result = plan.commit()

    assert not result.ok
    assert result.state == "rollback_conflict"
    assert target.read_text(encoding="utf-8") == "value = 3\n"


def test_transaction_undo_redo_and_selective_restore(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
    manager = WorkspaceTransactionManager(tmp_path)
    plan = manager.plan(
        "apply_patch",
        [
            _text_mutation(manager, "a.txt", "A\n"),
            _text_mutation(manager, "b.txt", "B\n"),
        ],
    )
    committed = plan.commit()
    assert committed.ok

    undone = manager.undo(committed.transaction_id, paths=["a.txt"])
    assert undone.ok
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "a\n"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "B\n"

    redone = manager.redo(committed.transaction_id, paths=["a.txt"])
    assert redone.ok
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "A\n"


def test_redo_refuses_later_transaction_on_same_path(tmp_path: Path) -> None:
    manager = WorkspaceTransactionManager(tmp_path)
    original = manager.plan_text("write_file", "app.txt", "one\n").commit()
    assert manager.undo(original.transaction_id).ok
    later = manager.plan_text("write_file", "app.txt", "different\n").commit()
    assert later.ok

    with pytest.raises(TransactionConflict, match="Later committed transactions"):
        manager.plan_redo(original.transaction_id)


def test_restore_complete_workspace_snapshot(tmp_path: Path) -> None:
    original = tmp_path / "original.txt"
    original.write_text("before\n", encoding="utf-8")
    manager = WorkspaceTransactionManager(tmp_path)
    committed = manager.plan_text("write_file", "original.txt", "after\n").commit()
    (tmp_path / "later.txt").write_text("later\n", encoding="utf-8")

    restored = manager.restore_snapshot(committed.transaction_id)

    assert restored.ok
    assert original.read_text(encoding="utf-8") == "before\n"
    assert not (tmp_path / "later.txt").exists()


def test_move_file_is_atomic_and_preserves_mode(tmp_path: Path) -> None:
    source = tmp_path / "old.txt"
    source.write_text("content\n", encoding="utf-8")
    os.chmod(source, 0o640)
    manager = WorkspaceTransactionManager(tmp_path)

    plan = manager.plan_move("move_file", "old.txt", "nested/new.txt")
    preview = manager.format_preview(plan)
    result = plan.commit()

    assert result.ok
    assert "Unified diff:" in preview
    assert "old.txt" in preview
    assert "nested/new.txt" in preview
    assert not source.exists()
    destination = tmp_path / "nested" / "new.txt"
    assert destination.read_text(encoding="utf-8") == "content\n"
    if os.name != "nt":
        assert stat.S_IMODE(destination.stat().st_mode) == 0o640


def test_symlink_mutation_is_refused_without_replacing_link(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("target\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target.name)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    manager = WorkspaceTransactionManager(tmp_path)

    with pytest.raises(TransactionError, match="symlink"):
        manager.plan_text("write_file", "link.txt", "changed\n")

    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == "target\n"


def test_workspace_snapshot_restores_symlink_identity(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")
    link = tmp_path / "current.txt"
    try:
        link.symlink_to(first.name)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    manager = WorkspaceTransactionManager(tmp_path)
    committed = manager.plan_text("write_file", "first.txt", "changed\n").commit()
    link.unlink()
    link.symlink_to(second.name)

    restored = manager.restore_snapshot(committed.transaction_id)

    assert restored.ok
    assert link.is_symlink()
    assert os.readlink(link) == first.name
    assert first.read_text(encoding="utf-8") == "first\n"


def test_blob_corruption_blocks_undo(tmp_path: Path) -> None:
    target = tmp_path / "app.txt"
    target.write_text("before\n", encoding="utf-8")
    manager = WorkspaceTransactionManager(tmp_path)
    committed = manager.plan_text("write_file", "app.txt", "after\n").commit()
    journal = manager.load_journal(committed.transaction_id)
    before_blob = journal["entries"][0]["before"]["blob"]
    (manager.blobs / before_blob).write_bytes(b"corrupt")

    with pytest.raises(TransactionError, match="Corrupt transaction blob"):
        manager.plan_undo(committed.transaction_id)


def test_transaction_audit_and_run_context_are_persisted(tmp_path: Path) -> None:
    manager = WorkspaceTransactionManager(tmp_path)
    committed = manager.plan_text("write_file", "app.txt", "content\n").commit()

    manager.attach_run_context(committed.transaction_id, run_id=12, step=3)

    journal = manager.load_journal(committed.transaction_id)
    assert journal["metadata"]["run_id"] == 12
    assert journal["metadata"]["step"] == 3
    listed = manager.list_transactions()
    assert listed[0]["run_id"] == 12
    audit = manager.audit_path.read_text(encoding="utf-8")
    assert "transaction_proposed" in audit
    assert "transaction_committed" in audit


def test_workspace_snapshot_excludes_sensitive_credentials(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    manager = WorkspaceTransactionManager(tmp_path)

    snapshot = manager.capture_workspace_snapshot()

    assert ".env" not in snapshot
    assert "app.py" in snapshot
    blob_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in manager.blobs.iterdir()
    )
    assert "TOKEN=secret" not in blob_text


def test_tool_registry_returns_transaction_metadata_and_move_action(
    tmp_path: Path,
) -> None:
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda *_args: True,
    )
    written = tools.run(
        WriteFileAction(type="write_file", path="old.txt", content="content\n")
    )

    assert written.ok
    transaction_id = written.metadata["transaction"]["id"]
    moved = tools.run(
        MoveFileAction(
            type="move_file",
            source="old.txt",
            destination="new.txt",
        )
    )
    assert moved.ok
    assert moved.metadata["transaction"]["state"] == "committed"

    undone = tools.run(
        UndoTransactionAction(
            type="undo_transaction",
            transaction_id=transaction_id,
        )
    )
    assert not undone.ok
    assert "Later committed transactions touched" in undone.output


def test_permission_denial_aborts_checkpoint_without_mutating_file(tmp_path: Path) -> None:
    target = tmp_path / "app.txt"
    target.write_text("old\n", encoding="utf-8")
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda *_args: False,
    )

    result = tools.run(
        WriteFileAction(type="write_file", path="app.txt", content="new\n")
    )

    assert not result.ok
    assert target.read_text(encoding="utf-8") == "old\n"
    transactions = tools.transaction_manager.list_transactions()
    assert transactions[0]["state"] == "aborted"


def test_transaction_tool_undo_redo_and_snapshot_restore(tmp_path: Path) -> None:
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda *_args: True,
    )
    result = tools.run(
        WriteFileAction(type="write_file", path="app.txt", content="one\n")
    )
    transaction_id = result.metadata["transaction"]["id"]

    undo = tools.run(
        UndoTransactionAction(
            type="undo_transaction",
            transaction_id=transaction_id,
        )
    )
    assert undo.ok
    assert not (tmp_path / "app.txt").exists()

    redo = tools.run(
        RedoTransactionAction(
            type="redo_transaction",
            transaction_id=transaction_id,
        )
    )
    assert redo.ok
    assert (tmp_path / "app.txt").read_text(encoding="utf-8") == "one\n"

    (tmp_path / "extra.txt").write_text("extra\n", encoding="utf-8")
    restore = tools.run(
        RestoreSnapshotAction(
            type="restore_snapshot",
            transaction_id=transaction_id,
        )
    )
    assert restore.ok
    assert not (tmp_path / "app.txt").exists()
    assert not (tmp_path / "extra.txt").exists()


def test_transactions_cli_lists_and_undoes(tmp_path: Path) -> None:
    manager = WorkspaceTransactionManager(tmp_path)
    committed = manager.plan_text("write_file", "app.txt", "content\n").commit()
    runner = CliRunner()

    listed = runner.invoke(
        app,
        ["transactions", "list", "--cwd", str(tmp_path)],
    )
    undone = runner.invoke(
        app,
        ["transactions", "undo", committed.transaction_id, "--cwd", str(tmp_path)],
        input="y\n",
    )

    assert listed.exit_code == 0
    assert committed.transaction_id in listed.output
    assert undone.exit_code == 0
    assert not (tmp_path / "app.txt").exists()


def test_plan_patch_is_atomic_for_multi_file_change(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
    patch = (
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+A\n"
        "--- a/b.txt\n"
        "+++ b/b.txt\n"
        "@@ -1 +1 @@\n"
        "-b\n"
        "+B\n"
    )
    manager = WorkspaceTransactionManager(tmp_path)

    result = manager.plan_patch(
        "apply_patch",
        patch,
        ["a.txt", "b.txt"],
    ).commit()

    assert result.ok
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "A\n"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "B\n"


def test_patch_staging_does_not_mutate_parent_git_workspace(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    manager = WorkspaceTransactionManager(tmp_path)
    patch = (
        "diff --git a/NOTES.md b/NOTES.md\n"
        "new file mode 100644\n"
        "index 0000000..c169369\n"
        "--- /dev/null\n"
        "+++ b/NOTES.md\n"
        "@@ -0,0 +1,2 @@\n"
        "+# Notes\n"
        "+Patch-backed creation.\n"
    )

    plan = manager.plan_patch("apply_patch", patch, ["NOTES.md"])

    assert not (tmp_path / "NOTES.md").exists()
    result = plan.commit()
    assert result.ok
    assert (tmp_path / "NOTES.md").read_text(encoding="utf-8") == (
        "# Notes\nPatch-backed creation.\n"
    )
