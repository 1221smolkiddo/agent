from __future__ import annotations

import pytest

from code_agent.diff_viewer import (
    DiffLine,
    DiffParseError,
    FileDiff,
    Hunk,
    parse_unified_diff,
)


def test_parse_empty_diff() -> None:
    assert parse_unified_diff("") == []
    assert parse_unified_diff("\n\n") == []


def test_parse_single_file_mixed_change() -> None:
    diff = "\n".join(
        [
            "--- a/hello.txt",
            "+++ b/hello.txt",
            "@@ -1 +1 @@",
            "-hello",
            "+hello agent47",
            "",
        ]
    )

    files = parse_unified_diff(diff)

    assert len(files) == 1
    file_diff = files[0]
    assert file_diff.old_path == "hello.txt"
    assert file_diff.new_path == "hello.txt"
    assert file_diff.filename == "hello.txt"
    assert file_diff.total_additions == 1
    assert file_diff.total_deletions == 1
    assert len(file_diff.hunks) == 1

    hunk = file_diff.hunks[0]
    assert hunk.header == "@@ -1 +1 @@"
    assert hunk.old_start == 1
    assert hunk.old_count == 1
    assert hunk.new_start == 1
    assert hunk.new_count == 1
    assert hunk.additions == 1
    assert hunk.deletions == 1
    assert hunk.context == 0
    assert hunk.lines == (
        DiffLine(kind="removed", text="hello", old_line_number=1, new_line_number=None),
        DiffLine(kind="added", text="hello agent47", old_line_number=None, new_line_number=1),
    )


def test_parse_multiple_files_preserves_order() -> None:
    diff = "\n".join(
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

    files = parse_unified_diff(diff)

    assert [file.filename for file in files] == ["a.txt", "b.txt"]
    assert [file.total_additions for file in files] == [1, 1]
    assert [file.total_deletions for file in files] == [1, 1]


def test_parse_multiple_hunks_preserves_order() -> None:
    diff = "\n".join(
        [
            "--- a/sample.txt",
            "+++ b/sample.txt",
            "@@ -1,2 +1,2 @@",
            " keep1",
            "-remove1",
            "+add1",
            "@@ -5,1 +5,2 @@",
            " keep5",
            "+add6",
            "",
        ]
    )

    files = parse_unified_diff(diff)
    assert len(files) == 1
    assert len(files[0].hunks) == 2
    assert files[0].hunks[0].header == "@@ -1,2 +1,2 @@"
    assert files[0].hunks[1].header == "@@ -5,1 +5,2 @@"
    assert files[0].total_additions == 2
    assert files[0].total_deletions == 1

    first = files[0].hunks[0].lines
    assert first[0] == DiffLine(kind="context", text="keep1", old_line_number=1, new_line_number=1)
    assert first[1] == DiffLine(kind="removed", text="remove1", old_line_number=2, new_line_number=None)
    assert first[2] == DiffLine(kind="added", text="add1", old_line_number=None, new_line_number=2)

    second = files[0].hunks[1].lines
    assert second[0] == DiffLine(kind="context", text="keep5", old_line_number=5, new_line_number=5)
    assert second[1] == DiffLine(kind="added", text="add6", old_line_number=None, new_line_number=6)


def test_parse_additions_only_hunk() -> None:
    diff = "\n".join(
        [
            "--- /dev/null",
            "+++ b/new.txt",
            "@@ -0,0 +1,2 @@",
            "+alpha",
            "+beta",
            "",
        ]
    )

    files = parse_unified_diff(diff)
    assert len(files) == 1
    file_diff = files[0]
    assert file_diff.old_path is None
    assert file_diff.new_path == "new.txt"
    assert file_diff.filename == "new.txt"
    assert file_diff.total_additions == 2
    assert file_diff.total_deletions == 0

    hunk = file_diff.hunks[0]
    assert hunk.old_start == 0
    assert hunk.old_count == 0
    assert hunk.new_start == 1
    assert hunk.new_count == 2
    assert hunk.lines == (
        DiffLine(kind="added", text="alpha", old_line_number=None, new_line_number=1),
        DiffLine(kind="added", text="beta", old_line_number=None, new_line_number=2),
    )


def test_parse_deletions_only_hunk() -> None:
    diff = "\n".join(
        [
            "--- a/old.txt",
            "+++ /dev/null",
            "@@ -1,2 +0,0 @@",
            "-alpha",
            "-beta",
            "",
        ]
    )

    files = parse_unified_diff(diff)
    file_diff = files[0]
    assert file_diff.old_path == "old.txt"
    assert file_diff.new_path is None
    assert file_diff.filename == "old.txt"
    assert file_diff.total_additions == 0
    assert file_diff.total_deletions == 2

    hunk = file_diff.hunks[0]
    assert hunk.lines == (
        DiffLine(kind="removed", text="alpha", old_line_number=1, new_line_number=None),
        DiffLine(kind="removed", text="beta", old_line_number=2, new_line_number=None),
    )


def test_parse_git_style_diff_with_metadata() -> None:
    diff = "\n".join(
        [
            "diff --git a/notes.md b/notes.md",
            "index 1234567..89abcde 100644",
            "--- a/notes.md",
            "+++ b/notes.md",
            "@@ -1,3 +1,4 @@",
            " line1",
            "-line2",
            "+line2 updated",
            "+line3",
            " line4",
            "",
        ]
    )

    files = parse_unified_diff(diff)
    assert len(files) == 1
    file_diff = files[0]
    assert file_diff.old_path == "notes.md"
    assert file_diff.new_path == "notes.md"
    assert file_diff.total_additions == 2
    assert file_diff.total_deletions == 1

    hunk = file_diff.hunks[0]
    assert hunk.context == 2
    assert hunk.lines[0].old_line_number == 1
    assert hunk.lines[0].new_line_number == 1
    assert hunk.lines[-1].old_line_number == 3
    assert hunk.lines[-1].new_line_number == 4


def test_parse_rename_diff() -> None:
    diff = "\n".join(
        [
            "diff --git a/old_name.txt b/new_name.txt",
            "similarity index 92%",
            "rename from old_name.txt",
            "rename to new_name.txt",
            "index 1111111..2222222 100644",
            "--- a/old_name.txt",
            "+++ b/new_name.txt",
            "@@ -1 +1 @@",
            "-content",
            "+content updated",
            "",
        ]
    )

    files = parse_unified_diff(diff)
    file_diff = files[0]
    assert file_diff.old_path == "old_name.txt"
    assert file_diff.new_path == "new_name.txt"
    assert file_diff.filename == "new_name.txt"


def test_parse_no_newline_marker() -> None:
    diff = "\n".join(
        [
            "--- a/file.txt",
            "+++ b/file.txt",
            "@@ -1 +1 @@",
            "-old",
            "+new",
            "\\ No newline at end of file",
            "",
        ]
    )

    files = parse_unified_diff(diff)
    hunk = files[0].hunks[0]
    assert len(hunk.lines) == 2
    assert hunk.additions == 1
    assert hunk.deletions == 1


def test_parse_is_deterministic() -> None:
    diff = "\n".join(
        [
            "--- a/a.txt",
            "+++ b/a.txt",
            "@@ -1,2 +1,2 @@",
            " ctx",
            "-old",
            "+new",
            "",
        ]
    )

    first = parse_unified_diff(diff)
    second = parse_unified_diff(diff)
    assert first == second
    assert first is not second


def test_malformed_hunk_header_raises() -> None:
    diff = "\n".join(
        [
            "--- a/file.txt",
            "+++ b/file.txt",
            "@@ not-a-valid-header @@",
            "-old",
            "+new",
            "",
        ]
    )

    with pytest.raises(DiffParseError, match="Malformed hunk header"):
        parse_unified_diff(diff)


def test_truncated_hunk_raises() -> None:
    diff = "\n".join(
        [
            "--- a/file.txt",
            "+++ b/file.txt",
            "@@ -1,2 +1,2 @@",
            "-only-one-line",
            "",
        ]
    )

    with pytest.raises(DiffParseError, match="Truncated hunk"):
        parse_unified_diff(diff)


def test_invalid_line_prefix_raises() -> None:
    diff = "\n".join(
        [
            "--- a/file.txt",
            "+++ b/file.txt",
            "@@ -1 +1 @@",
            "*invalid",
            "",
        ]
    )

    with pytest.raises(DiffParseError, match="Invalid hunk line prefix"):
        parse_unified_diff(diff)


def test_missing_new_file_header_raises() -> None:
    diff = "\n".join(
        [
            "--- a/file.txt",
            "@@ -1 +1 @@",
            "-old",
            "+new",
            "",
        ]
    )

    with pytest.raises(DiffParseError, match="Expected new file header"):
        parse_unified_diff(diff)


def test_unexpected_line_outside_file_diff_raises() -> None:
    with pytest.raises(DiffParseError, match="Unexpected line outside a file diff"):
        parse_unified_diff("not a diff")


def test_hunk_counts_match_context_lines() -> None:
    diff = "\n".join(
        [
            "--- a/file.txt",
            "+++ b/file.txt",
            "@@ -2,3 +2,4 @@",
            " unchanged",
            "-removed",
            "+added",
            "+inserted",
            " trailing",
            "",
        ]
    )

    files = parse_unified_diff(diff)
    hunk = files[0].hunks[0]
    assert hunk.old_count == 3
    assert hunk.new_count == 4
    assert hunk.context == 2
    assert hunk.deletions == 1
    assert hunk.additions == 2
    assert isinstance(files[0], FileDiff)
    assert isinstance(hunk, Hunk)
