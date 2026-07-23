"""OS-backed credential store using the ``keyring`` library.

Supports:
- **Windows**: Credential Manager (DPAPI)
- **macOS**: Keychain
- **Linux**: Secret Service D-Bus API (libsecret / gnome-keyring / KWallet)

When the platform credential store is unavailable or the ``keyring`` package
is missing, :class:`KeyringUnavailableError` is raised with platform-specific
setup guidance instead of silently falling back to plaintext storage.
"""

from __future__ import annotations

import json
import platform
from typing import Any

from .providers import provider_spec

SERVICE_NAME = "Agent47"
OAUTH_ACCOUNT = "google-oauth-tokens"

# Token fields that are safe to persist.  Everything else is stripped.
_SAFE_TOKEN_FIELDS = frozenset(
    {"access_token", "refresh_token", "expires_in", "issued_at", "scope", "token_type"}
)


class KeyringUnavailableError(RuntimeError):
    """Raised when the OS credential store cannot be accessed.

    The message always includes a user-friendly hint for the current platform.
    """


def keyring_setup_hint() -> str:
    """Return platform-specific guidance for setting up a secure keyring backend."""
    system = platform.system().lower()
    if system == "windows":
        return (
            "Windows Credential Manager should be available by default.  "
            "Run 'pip install keyring' if the keyring package is missing."
        )
    if system == "darwin":
        return (
            "macOS Keychain should be available by default.  "
            "Run 'pip install keyring' if the keyring package is missing."
        )
    # Linux and others.
    return (
        "Linux requires a Secret Service provider.  Install one of:\n"
        "  - GNOME: sudo apt install gnome-keyring libsecret-1-0\n"
        "  - KDE: sudo apt install kwalletmanager\n"
        "  Then run: pip install keyring SecretStorage\n"
        "  and ensure D-Bus is running: dbus-run-session -- agent47 login"
    )


class CredentialStore:
    """Small facade over the platform credential manager.

    Values are stored in the OS keyring and never touch the filesystem.
    The allowlist in :meth:`set_oauth_tokens` ensures that only recognized
    token fields are persisted — no raw Google response body is stored.
    """

    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        self.service_name = service_name

    @staticmethod
    def _backend() -> Any:
        """Return the ``keyring`` module after validating that a secure backend is active.

        Raises :class:`KeyringUnavailableError` with platform-specific guidance
        when:
        - The ``keyring`` package is not installed.
        - The active backend is ``NullKeyring``, ``PlaintextKeyring``, or
          another insecure fallback.
        - The backend raises an error during probe.
        """
        try:
            import keyring
            from keyring.errors import KeyringError
        except ImportError as exc:
            raise KeyringUnavailableError(
                "Secure credential storage is unavailable; install the keyring package.\n"
                f"  {keyring_setup_hint()}"
            ) from exc

        try:
            backend = keyring.get_keyring()
        except KeyringError as exc:
            raise KeyringUnavailableError(
                "Your operating system credential store is unavailable.\n"
                f"  {keyring_setup_hint()}"
            ) from exc

        # Reject insecure fallback backends that store secrets in plaintext.
        backend_name = type(backend).__name__.lower()
        if "null" in backend_name or "plaintext" in backend_name or "fail" in backend_name:
            raise KeyringUnavailableError(
                f"The active keyring backend ({type(backend).__name__}) is not secure.\n"
                f"  {keyring_setup_hint()}"
            )

        return keyring

    @staticmethod
    def is_available() -> bool:
        """Return True if a secure keyring backend is accessible."""
        try:
            CredentialStore._backend()
            return True
        except KeyringUnavailableError:
            return False

    @staticmethod
    def backend_name() -> str:
        """Return the name of the active keyring backend, or an error message."""
        try:
            import keyring

            return type(keyring.get_keyring()).__name__
        except Exception:
            return "unavailable"

    # ------------------------------------------------------------------
    # Provider API keys
    # ------------------------------------------------------------------

    def get_provider_key(self, provider: str) -> str | None:
        spec = provider_spec(provider)
        try:
            return self._backend().get_password(self.service_name, f"provider:{spec.name}")
        except Exception as exc:  # keyring backends expose varied exception types
            raise KeyringUnavailableError(
                "Could not access the operating system credential store.\n"
                f"  {keyring_setup_hint()}"
            ) from exc

    def set_provider_key(self, provider: str, value: str) -> None:
        spec = provider_spec(provider)
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("API keys must not be blank.")
        try:
            self._backend().set_password(self.service_name, f"provider:{spec.name}", cleaned)
        except Exception as exc:
            raise KeyringUnavailableError(
                "Could not save to the operating system credential store.\n"
                f"  {keyring_setup_hint()}"
            ) from exc

    def delete_provider_key(self, provider: str) -> bool:
        spec = provider_spec(provider)
        keyring = self._backend()
        try:
            if keyring.get_password(self.service_name, f"provider:{spec.name}") is None:
                return False
            keyring.delete_password(self.service_name, f"provider:{spec.name}")
            return True
        except Exception as exc:
            raise KeyringUnavailableError(
                "Could not delete from the operating system credential store.\n"
                f"  {keyring_setup_hint()}"
            ) from exc

    # ------------------------------------------------------------------
    # OAuth tokens
    # ------------------------------------------------------------------

    def get_oauth_tokens(self) -> dict[str, Any] | None:
        try:
            raw = self._backend().get_password(self.service_name, OAUTH_ACCOUNT)
        except Exception as exc:
            raise KeyringUnavailableError(
                "Could not access the operating system credential store.\n"
                f"  {keyring_setup_hint()}"
            ) from exc
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise KeyringUnavailableError(
                "Stored OAuth credentials are invalid.  "
                "Run 'agent47 logout' then log in again."
            ) from exc
        return value if isinstance(value, dict) else None

    def set_oauth_tokens(self, tokens: dict[str, Any]) -> None:
        """Persist OAuth tokens in the keyring after filtering through an allowlist."""
        safe_tokens = {
            key: value for key, value in tokens.items() if key in _SAFE_TOKEN_FIELDS
        }
        try:
            self._backend().set_password(
                self.service_name, OAUTH_ACCOUNT, json.dumps(safe_tokens)
            )
        except Exception as exc:
            raise KeyringUnavailableError(
                "Could not save OAuth credentials to the operating system credential store.\n"
                f"  {keyring_setup_hint()}"
            ) from exc

    def delete_oauth_tokens(self) -> bool:
        keyring = self._backend()
        try:
            if keyring.get_password(self.service_name, OAUTH_ACCOUNT) is None:
                return False
            keyring.delete_password(self.service_name, OAUTH_ACCOUNT)
            return True
        except Exception as exc:
            raise KeyringUnavailableError(
                "Could not delete OAuth credentials from the operating system credential store.\n"
                f"  {keyring_setup_hint()}"
            ) from exc
