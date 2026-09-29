"""Keep release archives free of local qualification and scratch material."""

from __future__ import annotations

import subprocess
import tarfile
import zipfile
from pathlib import Path

from code_agent import __version__


PROHIBITED_PARTS = {
    ".git", ".code-agent", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules",
    "scratch", "temp", "tmp",
}
PROHIBITED_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".pyc")
REQUIRED_MODULES = {
    "src/code_agent/__init__.py", "src/code_agent/agent.py",
    "src/code_agent/cli.py", "src/code_agent/sandbox.py",
}


def _prohibited(path: str) -> bool:
    parts = tuple(part.lower() for part in path.split("/"))
    name = parts[-1]
    return (
        bool(PROHIBITED_PARTS.intersection(parts))
        or name == ".env"
        or (name.startswith(".env.") and name != ".env.example")
        or name.startswith("debug_run_eval")
        or "debug_workspace" in parts
        or parts[:2] == ("docs", "qualification")
        or name.endswith(PROHIBITED_SUFFIXES)
    )


def test_built_wheel_and_sdist_exclude_release_scratch(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["uv", "build", "--out-dir", str(tmp_path)],
        cwd=root, check=True, capture_output=True, text=True,
    )
    wheel = tmp_path / f"agent47-{__version__}-py3-none-any.whl"
    sdist = tmp_path / f"agent47-{__version__}.tar.gz"
    assert wheel.is_file() and sdist.is_file()

    with zipfile.ZipFile(wheel) as archive:
        wheel_names = archive.namelist()
        assert not [name for name in wheel_names if _prohibited(name)]
        assert all(name.removeprefix("src/") in wheel_names for name in REQUIRED_MODULES)
        metadata = next(name for name in wheel_names if name.endswith(".dist-info/METADATA"))
        assert f"Version: {__version__}" in archive.read(metadata).decode("utf-8")

    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = [member.name.split("/", 1)[1] for member in archive if member.isfile()]
        assert not [name for name in sdist_names if _prohibited(name)]
        assert REQUIRED_MODULES.issubset(sdist_names)
        assert {"README.md", "CHANGELOG.md", "SECURITY.md", "pyproject.toml"}.issubset(
            sdist_names
        )
        metadata = next(member for member in archive.getmembers() if member.name.endswith("/PKG-INFO"))
        metadata_file = archive.extractfile(metadata)
        assert metadata_file is not None
        assert f"Version: {__version__}" in metadata_file.read().decode("utf-8")
