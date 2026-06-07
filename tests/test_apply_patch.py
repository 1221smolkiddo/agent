from pathlib import Path

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
    assert result.output == "Patch applied."
    assert source.read_text(encoding="utf-8") == "hello agent47\n"
    assert approval_details == [patch]


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
