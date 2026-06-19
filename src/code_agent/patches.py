from __future__ import annotations

import difflib


def git_style_unified_diff(
    path: str,
    before: str,
    after: str,
    *,
    before_exists: bool,
    after_exists: bool,
) -> str:
    before_lines = before.splitlines() if before_exists else []
    after_lines = after.splitlines() if after_exists else []
    fromfile = f"a/{path}" if before_exists else "/dev/null"
    tofile = f"b/{path}" if after_exists else "/dev/null"
    body = "\n".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )
    if not body:
        return ""
    header = [f"diff --git a/{path} b/{path}"]
    if not before_exists:
        header.extend(["new file mode 100644", "index 0000000..0000000"])
    elif not after_exists:
        header.extend(["deleted file mode 100644", "index 0000000..0000000"])
    else:
        header.append("index 0000000..0000000 100644")
    return "\n".join([*header, body]).rstrip() + "\n"
