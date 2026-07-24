"""Parse unified diffs into structured, immutable data objects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

DiffLineKind = Literal["context", "added", "removed"]

_HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$"
)
_FILE_HEADER_OLD = re.compile(r"^--- (.+)$")
_FILE_HEADER_NEW = re.compile(r"^\+\+\+ (.+)$")
_GIT_DIFF_HEADER = re.compile(r"^diff --git (.+) (.+)$")
_NO_NEWLINE = re.compile(r"^\\ No newline at end of file$")


class DiffParseError(ValueError):
    """Raised when a unified diff cannot be parsed safely."""


@dataclass(frozen=True)
class DiffLine:
    """One line inside a unified diff hunk."""

    kind: DiffLineKind
    text: str
    old_line_number: int | None
    new_line_number: int | None


@dataclass(frozen=True)
class Hunk:
    """One @@ hunk within a file diff."""

    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[DiffLine, ...]

    @property
    def additions(self) -> int:
        return sum(1 for line in self.lines if line.kind == "added")

    @property
    def deletions(self) -> int:
        return sum(1 for line in self.lines if line.kind == "removed")

    @property
    def context(self) -> int:
        return sum(1 for line in self.lines if line.kind == "context")


@dataclass(frozen=True)
class FileDiff:
    """One file section from a unified diff."""

    old_path: str | None
    new_path: str | None
    hunks: tuple[Hunk, ...]

    @property
    def filename(self) -> str | None:
        if self.new_path is not None:
            return self.new_path
        return self.old_path

    @property
    def total_additions(self) -> int:
        return sum(hunk.additions for hunk in self.hunks)

    @property
    def total_deletions(self) -> int:
        return sum(hunk.deletions for hunk in self.hunks)


def parse_unified_diff(diff_text: str) -> list[FileDiff]:
    """Parse unified diff text into ordered :class:`FileDiff` objects."""
    if not diff_text or not diff_text.strip():
        return []

    lines = diff_text.splitlines()
    files: list[FileDiff] = []
    index = 0

    while index < len(lines):
        index = _skip_blank_lines(lines, index)
        if index >= len(lines):
            break

        line = lines[index]
        if line.startswith("diff --git "):
            file_diff, index = _parse_git_file_diff(lines, index)
            files.append(file_diff)
            continue

        if line.startswith("--- "):
            file_diff, index = _parse_plain_file_diff(lines, index)
            files.append(file_diff)
            continue

        raise DiffParseError(f"Unexpected line outside a file diff: {line!r}")

    return files


def _skip_blank_lines(lines: list[str], index: int) -> int:
    while index < len(lines) and not lines[index].strip():
        index += 1
    return index


def _parse_git_file_diff(lines: list[str], index: int) -> tuple[FileDiff, int]:
    match = _GIT_DIFF_HEADER.match(lines[index])
    if match is None:
        raise DiffParseError(f"Malformed git diff header: {lines[index]!r}")

    old_path = _normalize_path(match.group(1))
    new_path = _normalize_path(match.group(2))
    index += 1
    index = _skip_file_metadata(lines, index)
    old_path, new_path, index = _read_optional_file_headers(
        lines,
        index,
        old_path,
        new_path,
    )
    hunks, index = _parse_hunks(lines, index)
    return FileDiff(old_path=old_path, new_path=new_path, hunks=tuple(hunks)), index


def _parse_plain_file_diff(lines: list[str], index: int) -> tuple[FileDiff, int]:
    old_match = _FILE_HEADER_OLD.match(lines[index])
    if old_match is None:
        raise DiffParseError(f"Malformed old file header: {lines[index]!r}")

    old_path = _normalize_path(old_match.group(1))
    index += 1
    if index >= len(lines) or not lines[index].startswith("+++ "):
        raise DiffParseError("Expected new file header after old file header.")

    new_match = _FILE_HEADER_NEW.match(lines[index])
    if new_match is None:
        raise DiffParseError(f"Malformed new file header: {lines[index]!r}")

    new_path = _normalize_path(new_match.group(1))
    index += 1
    index = _skip_file_metadata(lines, index)
    hunks, index = _parse_hunks(lines, index)
    return FileDiff(old_path=old_path, new_path=new_path, hunks=tuple(hunks)), index


def _read_optional_file_headers(
    lines: list[str],
    index: int,
    old_path: str | None,
    new_path: str | None,
) -> tuple[str | None, str | None, int]:
    if index >= len(lines) or not lines[index].startswith("--- "):
        return old_path, new_path, index

    old_match = _FILE_HEADER_OLD.match(lines[index])
    if old_match is None:
        raise DiffParseError(f"Malformed old file header: {lines[index]!r}")
    old_path = _normalize_path(old_match.group(1))
    index += 1

    if index >= len(lines) or not lines[index].startswith("+++ "):
        raise DiffParseError("Expected new file header after old file header.")

    new_match = _FILE_HEADER_NEW.match(lines[index])
    if new_match is None:
        raise DiffParseError(f"Malformed new file header: {lines[index]!r}")
    new_path = _normalize_path(new_match.group(1))
    index += 1
    index = _skip_file_metadata(lines, index)
    return old_path, new_path, index


def _skip_file_metadata(lines: list[str], index: int) -> int:
    metadata_prefixes = (
        "index ",
        "new file mode ",
        "deleted file mode ",
        "old mode ",
        "new mode ",
        "rename from ",
        "rename to ",
        "similarity index ",
        "dissimilarity index ",
        "copy from ",
        "copy to ",
        "Binary files ",
    )
    while index < len(lines):
        line = lines[index]
        if line.startswith("@@ ") or line.startswith("--- ") or line.startswith("diff --git "):
            break
        if any(line.startswith(prefix) for prefix in metadata_prefixes):
            index += 1
            continue
        if not line.strip():
            index += 1
            continue
        break
    return index


def _parse_hunks(lines: list[str], index: int) -> tuple[list[Hunk], int]:
    hunks: list[Hunk] = []
    while index < len(lines):
        line = lines[index]
        if line.startswith("diff --git ") or line.startswith("--- "):
            break
        if not line.startswith("@@ "):
            if not line.strip():
                index += 1
                continue
            raise DiffParseError(f"Expected hunk header, got: {line!r}")

        header = line
        old_start, old_count, new_start, new_count = _parse_hunk_header(header)
        index += 1
        hunk_lines, index = _parse_hunk_lines(
            lines,
            index,
            old_start=old_start,
            old_count=old_count,
            new_start=new_start,
            new_count=new_count,
        )
        hunks.append(
            Hunk(
                header=header,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                lines=tuple(hunk_lines),
            )
        )
    return hunks, index


def _parse_hunk_header(header: str) -> tuple[int, int, int, int]:
    match = _HUNK_HEADER.match(header)
    if match is None:
        raise DiffParseError(f"Malformed hunk header: {header!r}")

    old_start = int(match.group(1))
    old_count = int(match.group(2) or "1")
    new_start = int(match.group(3))
    new_count = int(match.group(4) or "1")
    return old_start, old_count, new_start, new_count


def _parse_hunk_lines(
    lines: list[str],
    index: int,
    *,
    old_start: int,
    old_count: int,
    new_start: int,
    new_count: int,
) -> tuple[list[DiffLine], int]:
    parsed: list[DiffLine] = []
    old_line = old_start
    new_line = new_start
    old_seen = 0
    new_seen = 0

    while old_seen < old_count or new_seen < new_count:
        if index >= len(lines):
            raise DiffParseError("Truncated hunk: missing diff lines.")

        line = lines[index]
        if _is_file_boundary(line) or line.startswith("@@ "):
            raise DiffParseError("Truncated hunk: missing diff lines.")

        if line.startswith("\\"):
            if not _NO_NEWLINE.match(line):
                raise DiffParseError(f"Invalid no-newline marker: {line!r}")
            index += 1
            continue

        if not line:
            raise DiffParseError("Invalid hunk line prefix: empty line.")

        prefix = line[0]
        text = line[1:]

        if prefix == " ":
            if old_seen >= old_count or new_seen >= new_count:
                raise DiffParseError("Unexpected context line in hunk.")
            parsed.append(
                DiffLine(
                    kind="context",
                    text=text,
                    old_line_number=old_line,
                    new_line_number=new_line,
                )
            )
            old_line += 1
            new_line += 1
            old_seen += 1
            new_seen += 1
        elif prefix == "-":
            if old_seen >= old_count:
                raise DiffParseError("Unexpected deletion line in hunk.")
            parsed.append(
                DiffLine(
                    kind="removed",
                    text=text,
                    old_line_number=old_line,
                    new_line_number=None,
                )
            )
            old_line += 1
            old_seen += 1
        elif prefix == "+":
            if new_seen >= new_count:
                raise DiffParseError("Unexpected addition line in hunk.")
            parsed.append(
                DiffLine(
                    kind="added",
                    text=text,
                    old_line_number=None,
                    new_line_number=new_line,
                )
            )
            new_line += 1
            new_seen += 1
        else:
            raise DiffParseError(f"Invalid hunk line prefix: {prefix!r}")

        index += 1

    while index < len(lines) and lines[index].startswith("\\"):
        if not _NO_NEWLINE.match(lines[index]):
            raise DiffParseError(f"Invalid no-newline marker: {lines[index]!r}")
        index += 1

    if index < len(lines) and not _is_file_boundary(lines[index]) and not lines[index].startswith("@@ "):
        if lines[index].strip() and lines[index][0] in {" ", "+", "-", "\\"}:
            raise DiffParseError("Unexpected extra diff lines in hunk.")

    return parsed, index


def _is_file_boundary(line: str) -> bool:
    return line.startswith("diff --git ") or line.startswith("--- ")


def _normalize_path(raw_path: str) -> str | None:
    path = raw_path.split("\t", 1)[0].strip()
    if path == "/dev/null":
        return None
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path
