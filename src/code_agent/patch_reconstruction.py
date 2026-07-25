"""Reconstruct a unified diff from a review result, retaining only accepted hunks."""

from __future__ import annotations

from .diff_viewer import parse_unified_diff, DiffParseError
from .interactive_diff import DiffReviewResult


def reconstruct_selected_patch(diff_text: str, review: DiffReviewResult) -> str:
    """Reconstruct a patch preserving exact lines for all accepted hunks."""
    if not review.accepted:
        return ""

    try:
        files = parse_unified_diff(diff_text)
    except DiffParseError:
        return ""

    if not files:
        return ""

    raw_lines = diff_text.splitlines(keepends=True)
    clean_lines = diff_text.splitlines(keepends=False)

    output = []
    line_idx = 0

    def skip_blanks(idx: int) -> int:
        while idx < len(clean_lines) and not clean_lines[idx].strip():
            idx += 1
        return idx

    for file_idx, file_diff in enumerate(files):
        line_idx = skip_blanks(line_idx)
        if line_idx >= len(clean_lines):
            break

        header_start = line_idx

        # Advance past the file metadata
        line = clean_lines[line_idx]
        if line.startswith("diff --git "):
            line_idx += 1
            while line_idx < len(clean_lines):
                line = clean_lines[line_idx]
                if line.startswith("@@ ") or line.startswith("--- ") or line.startswith("diff --git "):
                    break
                line_idx += 1

            if line_idx < len(clean_lines) and clean_lines[line_idx].startswith("--- "):
                line_idx += 1
                if line_idx < len(clean_lines) and clean_lines[line_idx].startswith("+++ "):
                    line_idx += 1
                while line_idx < len(clean_lines):
                    line = clean_lines[line_idx]
                    if line.startswith("@@ ") or line.startswith("--- ") or line.startswith("diff --git "):
                        break
                    line_idx += 1
        elif line.startswith("--- "):
            line_idx += 1
            if line_idx < len(clean_lines) and clean_lines[line_idx].startswith("+++ "):
                line_idx += 1
            while line_idx < len(clean_lines):
                line = clean_lines[line_idx]
                if line.startswith("@@ ") or line.startswith("--- ") or line.startswith("diff --git "):
                    break
                line_idx += 1

        header_end = line_idx

        # Check if the file has any accepted hunks
        file_has_accepted = any(
            (file_idx, h_idx) in review.accepted for h_idx in range(len(file_diff.hunks))
        )

        if file_has_accepted:
            for i in range(header_start, header_end):
                output.append(raw_lines[i])

        # Parse hunks
        for hunk_idx, _ in enumerate(file_diff.hunks):
            is_accepted = (file_idx, hunk_idx) in review.accepted

            # Hunk header
            if line_idx < len(clean_lines) and clean_lines[line_idx].startswith("@@ "):
                if is_accepted:
                    output.append(raw_lines[line_idx])
                line_idx += 1

            # Hunk lines
            while line_idx < len(clean_lines):
                line = clean_lines[line_idx]
                if not line:
                    break
                if line[0] in {" ", "+", "-", "\\"}:
                    if is_accepted:
                        output.append(raw_lines[line_idx])
                    line_idx += 1
                else:
                    break

    return "".join(output)
