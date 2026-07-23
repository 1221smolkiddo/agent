from __future__ import annotations

import json
import os
from pathlib import Path

from ..auth.models import Account


def agent47_config_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(root) / "Agent47"
    if sys_platform() == "darwin":
        return Path.home() / "Library" / "Application Support" / "Agent47"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "agent47"


def sys_platform() -> str:
    import sys

    return sys.platform


class AccountStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or agent47_config_dir()
        self.path = self.directory / "account.json"

    def load(self) -> Account | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return Account.from_dict(raw)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def save(self, account: Account) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(account.to_dict(), indent=2) + "\n", encoding="utf-8")
        try:
            temp.chmod(0o600)
        except OSError:
            pass
        temp.replace(self.path)

    def delete(self) -> bool:
        if not self.path.exists():
            return False
        self.path.unlink()
        return True
