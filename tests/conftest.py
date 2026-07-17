from __future__ import annotations

import os
import subprocess
from typing import Any


_ORIGINAL_POPEN = subprocess.Popen


def _no_window_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
    if os.name == "nt":
        kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | subprocess.CREATE_NO_WINDOW
    return _ORIGINAL_POPEN(*args, **kwargs)


def pytest_sessionstart() -> None:
    if os.name == "nt":
        subprocess.Popen = _no_window_popen


def pytest_sessionfinish() -> None:
    subprocess.Popen = _ORIGINAL_POPEN
