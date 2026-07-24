"""Flat view model for rendering parsed unified diffs without nested traversal."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .diff_viewer import DiffLineKind, FileDiff


class DiffRowKind(Enum):
    """Kind of row in the flattened diff view model."""

    FILE_HEADER = "file_header"
    HUNK_HEADER = "hunk_header"
    LINE = "line"


@dataclass(frozen=True)
class FileHeaderRow:
    """Renderable header row for one file in the diff."""

    file_index: int
    old_path: str | None
    new_path: str | None
    total_additions: int
    total_deletions: int

    @property
    def kind(self) -> DiffRowKind:
        return DiffRowKind.FILE_HEADER

    @property
    def filename(self) -> str | None:
        if self.new_path is not None:
            return self.new_path
        return self.old_path


@dataclass(frozen=True)
class HunkHeaderRow:
    """Renderable header row for one hunk."""

    file_index: int
    hunk_index: int
    header: str
    additions: int
    deletions: int
    context: int

    @property
    def kind(self) -> DiffRowKind:
        return DiffRowKind.HUNK_HEADER


@dataclass(frozen=True)
class LineRow:
    """Renderable row for one diff line."""

    file_index: int
    hunk_index: int
    line_index: int
    line_kind: DiffLineKind
    text: str
    old_line_number: int | None
    new_line_number: int | None

    @property
    def kind(self) -> DiffRowKind:
        return DiffRowKind.LINE


DiffRow = FileHeaderRow | HunkHeaderRow | LineRow


@dataclass(frozen=True)
class DiffViewModel:
    """Scrollable, flat projection of parsed diff data."""

    rows: tuple[DiffRow, ...]

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def row_count(self) -> int:
        return len(self.rows)


def build_diff_view_model(files: list[FileDiff]) -> DiffViewModel:
    """Build a flat row list from immutable parser output."""
    rows: list[DiffRow] = []

    for file_index, file_diff in enumerate(files):
        rows.append(
            FileHeaderRow(
                file_index=file_index,
                old_path=file_diff.old_path,
                new_path=file_diff.new_path,
                total_additions=file_diff.total_additions,
                total_deletions=file_diff.total_deletions,
            )
        )
        for hunk_index, hunk in enumerate(file_diff.hunks):
            rows.append(
                HunkHeaderRow(
                    file_index=file_index,
                    hunk_index=hunk_index,
                    header=hunk.header,
                    additions=hunk.additions,
                    deletions=hunk.deletions,
                    context=hunk.context,
                )
            )
            for line_index, line in enumerate(hunk.lines):
                rows.append(
                    LineRow(
                        file_index=file_index,
                        hunk_index=hunk_index,
                        line_index=line_index,
                        line_kind=line.kind,
                        text=line.text,
                        old_line_number=line.old_line_number,
                        new_line_number=line.new_line_number,
                    )
                )

    return DiffViewModel(rows=tuple(rows))
