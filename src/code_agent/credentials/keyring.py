from __future__ import annotations

import json
from typing import Any

from .providers import provider_spec

SERVICE_NAME = "Agent47"
OAUTH_ACCOUNT = "google-oauth-tokens"


class KeyringUnavailableError(RuntimeError):
    pass


class CredentialStore:
    """Small facade over the platform credential manager; values never hit disk."""

    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        self.service_name = service_name

    @staticmethod
    def _backend() -> Any:
        try:
            import keyring
            from keyring.errors import KeyringError
        except ImportError as exc:
            raise KeyringUnavailableError("Secure credential storage is unavailable; install the keyring package.") from exc
        try:
            keyring.get_keyring()
        except KeyringError as exc:
            raise KeyringUnavailableError("Your operating system credential store is unavailable.") from exc
        return keyring

    def get_provider_key(self, provider: str) -> str | None:
        spec = provider_spec(provider)
        try:
            return self._backend().get_password(self.service_name, f"provider:{spec.name}")
        except Exception as exc:  # keyring backends expose varied exception types
            raise KeyringUnavailableError("Could not access the operating system credential store.") from exc

    def set_provider_key(self, provider: str, value: str) -> None:
        spec = provider_spec(provider)
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("API keys must not be blank.")
        try:
            self._backend().set_password(self.service_name, f"provider:{spec.name}", cleaned)
        except Exception as exc:
            raise KeyringUnavailableError("Could not save to the operating system credential store.") from exc

    def delete_provider_key(self, provider: str) -> bool:
        spec = provider_spec(provider)
        keyring = self._backend()
        try:
            if keyring.get_password(self.service_name, f"provider:{spec.name}") is None:
                return False
            keyring.delete_password(self.service_name, f"provider:{spec.name}")
            return True
        except Exception as exc:
            raise KeyringUnavailableError("Could not delete from the operating system credential store.") from exc

    def get_oauth_tokens(self) -> dict[str, Any] | None:
        try:
            raw = self._backend().get_password(self.service_name, OAUTH_ACCOUNT)
        except Exception as exc:
            raise KeyringUnavailableError("Could not access the operating system credential store.") from exc
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise KeyringUnavailableError("Stored OAuth credentials are invalid. Run 'agent47 logout' then log in again.") from exc
        return value if isinstance(value, dict) else None

    def set_oauth_tokens(self, tokens: dict[str, Any]) -> None:
        safe_tokens = {
            key: value
            for key, value in tokens.items()
            if key in {"access_token", "refresh_token", "expires_in", "issued_at", "scope", "token_type"}
        }
        try:
            self._backend().set_password(self.service_name, OAUTH_ACCOUNT, json.dumps(safe_tokens))
        except Exception as exc:
            raise KeyringUnavailableError("Could not save OAuth credentials to the operating system credential store.") from exc

    def delete_oauth_tokens(self) -> bool:
        keyring = self._backend()
        try:
            if keyring.get_password(self.service_name, OAUTH_ACCOUNT) is None:
                return False
            keyring.delete_password(self.service_name, OAUTH_ACCOUNT)
            return True
        except Exception as exc:
            raise KeyringUnavailableError("Could not delete OAuth credentials from the operating system credential store.") from exc
