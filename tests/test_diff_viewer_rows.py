from __future__ import annotations

from code_agent.diff_viewer import parse_unified_diff
from code_agent.diff_viewer_rows import (
    DiffRowKind,
    DiffViewModel,
    FileHeaderRow,
    HunkHeaderRow,
    LineRow,
    build_diff_view_model,
)


def test_build_empty_view_model() -> None:
    model = build_diff_view_model([])

    assert isinstance(model, DiffViewModel)
    assert model.row_count == 0
    assert len(model) == 0
    assert model.rows == ()


def test_flatten_single_file_single_hunk() -> None:
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
    model = build_diff_view_model(files)

    assert model.row_count == 4
    assert [row.kind for row in model.rows] == [
        DiffRowKind.FILE_HEADER,
        DiffRowKind.HUNK_HEADER,
        DiffRowKind.LINE,
        DiffRowKind.LINE,
    ]

    file_row = model.rows[0]
    assert isinstance(file_row, FileHeaderRow)
    assert file_row.file_index == 0
    assert file_row.filename == "hello.txt"
    assert file_row.total_additions == 1
    assert file_row.total_deletions == 1

    hunk_row = model.rows[1]
    assert isinstance(hunk_row, HunkHeaderRow)
    assert hunk_row.file_index == 0
    assert hunk_row.hunk_index == 0
    assert hunk_row.header == "@@ -1 +1 @@"
    assert hunk_row.additions == 1
    assert hunk_row.deletions == 1

    removed_row = model.rows[2]
    added_row = model.rows[3]
    assert isinstance(removed_row, LineRow)
    assert isinstance(added_row, LineRow)
    assert removed_row.line_kind == "removed"
    assert removed_row.text == "hello"
    assert removed_row.old_line_number == 1
    assert removed_row.new_line_number is None
    assert added_row.line_kind == "added"
    assert added_row.text == "hello agent47"
    assert added_row.old_line_number is None
    assert added_row.new_line_number == 1


def test_flatten_multiple_files_and_hunks_preserves_order() -> None:
    diff = "\n".join(
        [
            "--- a/a.txt",
            "+++ b/a.txt",
            "@@ -1 +1 @@",
            "-one",
            "+ONE",
            "@@ -3 +3 @@",
            "-three",
            "+THREE",
            "--- a/b.txt",
            "+++ b/b.txt",
            "@@ -1 +1 @@",
            "-two",
            "+TWO",
            "",
        ]
    )
    model = build_diff_view_model(parse_unified_diff(diff))

    assert [row.kind for row in model.rows] == [
        DiffRowKind.FILE_HEADER,
        DiffRowKind.HUNK_HEADER,
        DiffRowKind.LINE,
        DiffRowKind.LINE,
        DiffRowKind.HUNK_HEADER,
        DiffRowKind.LINE,
        DiffRowKind.LINE,
        DiffRowKind.FILE_HEADER,
        DiffRowKind.HUNK_HEADER,
        DiffRowKind.LINE,
        DiffRowKind.LINE,
    ]

    file_headers = [row for row in model.rows if isinstance(row, FileHeaderRow)]
    assert [row.filename for row in file_headers] == ["a.txt", "b.txt"]
    assert [row.file_index for row in file_headers] == [0, 1]

    hunk_headers = [row for row in model.rows if isinstance(row, HunkHeaderRow)]
    assert [(row.file_index, row.hunk_index) for row in hunk_headers] == [
        (0, 0),
        (0, 1),
        (1, 0),
    ]


def test_line_rows_carry_stable_indices() -> None:
    diff = "\n".join(
        [
            "--- a/sample.txt",
            "+++ b/sample.txt",
            "@@ -1,2 +1,2 @@",
            " keep1",
            "-remove1",
            "+add1",
            "",
        ]
    )
    model = build_diff_view_model(parse_unified_diff(diff))
    line_rows = [row for row in model.rows if isinstance(row, LineRow)]

    assert [(row.file_index, row.hunk_index, row.line_index) for row in line_rows] == [
        (0, 0, 0),
        (0, 0, 1),
        (0, 0, 2),
    ]


def test_build_view_model_is_deterministic() -> None:
    diff = "\n".join(
        [
            "--- a/a.txt",
            "+++ b/a.txt",
            "@@ -1 +1 @@",
            "-old",
            "+new",
            "",
        ]
    )
    files = parse_unified_diff(diff)

    first = build_diff_view_model(files)
    second = build_diff_view_model(files)

    assert first == second
    assert first.rows is not second.rows
