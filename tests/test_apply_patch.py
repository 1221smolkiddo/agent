from pathlib import Path
from typing import Any

from code_agent.schema import ApplyPatchAction
from code_agent.tools import ToolRegistry


def test_apply_patch_applies_unified_diff_after_permission(tmp_path: Path) -> None:
    source = tmp_path / "hello.txt"
    source.write_text("hello\n", encoding="utf-8")
    patch = "\n".join(
        [
            "--- a/hello.txt",
            "+++ b/hello.txt",
            "@@ -1 +1 @@",
            "-hello",
            "+hello agent47",
            "",
        ]
    )
    approval_details: list[str] = []
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda _action, detail: approval_details.append(detail) or True,
    )

    result = tools.run(ApplyPatchAction(type="apply_patch", patch=patch))

    assert result.ok
    assert result.output.startswith("Patch applied.")
    assert result.metadata["paths"] == ["hello.txt"]
    assert result.metadata["file_count"] == 1
    assert result.metadata["total_additions"] == 1
    assert result.metadata["total_deletions"] == 1
    assert source.read_text(encoding="utf-8") == "hello agent47\n"
    assert approval_details
    assert "Patch preview:" in approval_details[0]
    assert "- hello.txt: update, +1 -1" in approval_details[0]
    assert patch in approval_details[0]


def test_apply_patch_reports_multi_file_change_set_metadata(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    patch = "\n".join(
        [
            "--- a/a.txt",
            "+++ b/a.txt",
            "@@ -1 +1 @@",
            "-one",
            "+ONE",
            "--- a/b.txt",
            "+++ b/b.txt",
            "@@ -1 +1 @@",
            "-two",
            "+TWO",
            "",
        ]
    )
    approval_metadata: list[dict[str, Any]] = []
    tools = ToolRegistry(
        workspace=tmp_path,
        dry_run=False,
        approval_callback=lambda _action, _detail, metadata: approval_metadata.append(metadata) or True,
    )

    result = tools.run(ApplyPatchAction(type="apply_patch", patch=patch))

    assert result.ok
    assert first.read_text(encoding="utf-8") == "ONE\n"
    assert second.read_text(encoding="utf-8") == "TWO\n"
    assert result.metadata["paths"] == ["a.txt", "b.txt"]
    assert result.metadata["file_count"] == 2
    assert result.metadata["total_additions"] == 2
    assert result.metadata["total_deletions"] == 2
    assert approval_metadata[0]["paths"] == ["a.txt", "b.txt"]
    assert "stage" not in approval_metadata[0]
    assert result.metadata["stage"] == "apply"


def test_apply_patch_requires_permission(tmp_path: Path) -> None:
    source = tmp_path / "hello.txt"
    source.write_text("hello\n", encoding="utf-8")
    patch = "\n".join(
        [
            "--- a/hello.txt",
            "+++ b/hello.txt",
            "@@ -1 +1 @@",
            "-hello",
            "+blocked",
            "",
        ]
    )
    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda _a, _d: False)

    result = tools.run(ApplyPatchAction(type="apply_patch", patch=patch))

    assert not result.ok
    assert result.output == "Permission denied for apply_patch."
    assert source.read_text(encoding="utf-8") == "hello\n"


def test_apply_patch_is_blocked_in_dry_run(tmp_path: Path) -> None:
    tools = ToolRegistry(workspace=tmp_path, dry_run=True, approval_callback=lambda _a, _d: True)

    result = tools.run(ApplyPatchAction(type="apply_patch", patch="--- a/file\n+++ b/file\n"))

    assert not result.ok
    assert "Dry-run mode skipped apply_patch" in result.output


def test_apply_patch_rejects_paths_outside_workspace(tmp_path: Path) -> None:
    patch = "\n".join(
        [
            "--- a/../outside.txt",
            "+++ b/../outside.txt",
            "@@ -1 +1 @@",
            "-old",
            "+new",
            "",
        ]
    )
    tools = ToolRegistry(workspace=tmp_path, dry_run=False, approval_callback=lambda _a, _d: True)

    result = tools.run(ApplyPatchAction(type="apply_patch", patch=patch))

    assert not result.ok
    assert "Path escapes workspace" in result.output
