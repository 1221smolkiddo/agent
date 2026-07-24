from __future__ import annotations

from ..account.profile import AccountStore
from ..credentials.keyring import CredentialStore
from .models import Account


class LocalSession:
    """Coordinates non-sensitive profile metadata and keyring-held OAuth tokens."""

    def __init__(
        self, accounts: AccountStore | None = None, credentials: CredentialStore | None = None
    ) -> None:
        self.accounts = accounts or AccountStore()
        self.credentials = credentials or CredentialStore()

    def account(self) -> Account | None:
        return self.accounts.load()

    def signed_in(self) -> bool:
        return self.account() is not None and self.credentials.get_oauth_tokens() is not None

    def save(self, account: Account, tokens: dict[str, object]) -> None:
        self.credentials.set_oauth_tokens(tokens)
        try:
            self.accounts.save(account)
        except Exception as save_error:
            # The keyring write has already succeeded.  Do not leave a usable
            # OAuth session behind when its corresponding account metadata
            # could not be persisted.
            try:
                self.credentials.delete_oauth_tokens()
            except Exception as cleanup_error:
                # Account persistence is the operation that failed; retain it
                # as the raised exception while preserving cleanup context.
                raise save_error from cleanup_error
            raise

    def clear(self) -> tuple[bool, bool]:
        return self.accounts.delete(), self.credentials.delete_oauth_tokens()
