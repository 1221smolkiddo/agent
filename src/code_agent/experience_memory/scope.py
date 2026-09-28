from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class RepositoryScope:
    bank_id: str
    branch: str | None = field(default=None, repr=False)
    head: str | None = None


def _git(workspace: Path, *args: str) -> str | None:
    # Inspection only. Preserve Git configuration paths so diff uses the same text normalization as the repository.
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
        "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "XDG_CONFIG_HOME",
    }}
    env.update({"GIT_TERMINAL_PROMPT": "0"})
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=2, check=False, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def normalize_origin(origin: str, root: Path) -> str:
    """Canonical host/path across HTTPS and SSH; discard userinfo and query secrets."""
    value = origin.strip()
    if "://" not in value and not re.match(r"^[A-Za-z]:[\\/]", value):
        scp = re.fullmatch(r"(?:[^/@]+@)?([^/:]+):(.+)", value)
        if scp:
            value = f"ssh://{scp[1]}/{scp[2]}"
    parsed = urlsplit(value)
    if parsed.scheme == "file":
        raw_path = unquote(parsed.path)
        if parsed.hostname and parsed.hostname != "localhost":
            raw_path = "//" + parsed.hostname.lower() + raw_path
        elif os.name == "nt" and re.match(r"^/[A-Za-z]:/", raw_path):
            raw_path = raw_path[1:]
        path = Path(raw_path)
        return "local:" + os.path.normcase(str(path.resolve()))
    if parsed.hostname:
        host = parsed.hostname.lower().rstrip(".")
        port = parsed.port
        if port and (parsed.scheme, port) not in {("https", 443), ("http", 80), ("ssh", 22)}:
            host += f":{port}"
        path = parsed.path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        if host in {"github.com", "gitlab.com", "bitbucket.org"}:
            path = path.lower()
        return f"remote:{host}/{path}"
    return "local:" + os.path.normcase(str((root / value).resolve()))


def repository_scope(workspace: Path) -> RepositoryScope:
    root = workspace.resolve()
    top = _git(root, "rev-parse", "--show-toplevel")
    if top:
        root = Path(top).resolve()
    origin = _git(root, "config", "--get", "remote.origin.url")
    identity = None
    if origin:
        try:
            identity = normalize_origin(origin, root)
        except ValueError:
            pass  # A malformed origin is never exported or included in an exception.
    if identity is None:
        common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
        anchor = Path(common).resolve() if common else root
        identity = "fallback:" + os.path.normcase(str(anchor))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return RepositoryScope(
        bank_id="agent47-repo-" + digest,
        branch=_git(root, "symbolic-ref", "--quiet", "--short", "HEAD"),
        head=_git(root, "rev-parse", "--verify", "HEAD"),
    )
