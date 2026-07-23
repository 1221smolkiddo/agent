from __future__ import annotations

import secrets
import webbrowser
from datetime import UTC, datetime

from ..account.profile import AccountStore
from ..credentials.keyring import CredentialStore
from .callback_server import LocalCallbackServer
from .config import GoogleOAuthConfig
from .models import Account
from .oauth import OAuthError, authorization_url, exchange_code, fetch_profile, refresh_access_token
from .pkce import code_challenge, generate_code_verifier, generate_state
from .session import LocalSession


class GoogleAuthenticator:
    def __init__(
        self,
        config: GoogleOAuthConfig | None = None,
        session: LocalSession | None = None,
    ) -> None:
        self.config = config or GoogleOAuthConfig.from_environment()
        self.session = session or LocalSession(AccountStore(), CredentialStore())

    def login(self, *, browser_open=webbrowser.open) -> Account:
        verifier = generate_code_verifier()
        state = generate_state()
        with LocalCallbackServer() as callback_server:
            url = authorization_url(
                client_id=self.config.client_id,
                redirect_uri=callback_server.redirect_uri,
                state=state,
                code_challenge=code_challenge(verifier),
            )
            if not browser_open(url):
                raise OAuthError("Could not open your browser. Copy the login URL from a future interactive prompt and retry.")
            callback = callback_server.wait(self.config.timeout_seconds)
            redirect_uri = callback_server.redirect_uri
        # Stop the local listener before any network token exchange.
        if callback is None:
            raise OAuthError("Google sign-in timed out. Close the browser tab and try again.")
        if not callback.state or not secrets.compare_digest(callback.state, state):
            raise OAuthError("Google sign-in was rejected because its security state did not match.")
        if callback.error:
            if callback.error == "access_denied":
                raise OAuthError("Google sign-in was cancelled.")
            raise OAuthError("Google sign-in did not complete. Please try again.")
        if not callback.code:
            raise OAuthError("Google returned an incomplete sign-in response. Please try again.")
        tokens = exchange_code(
            client_id=self.config.client_id,
            client_secret=self.config.client_secret,
            code=callback.code,
            redirect_uri=redirect_uri,
            verifier=verifier,
        )
        access_token = tokens.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthError("Google did not return an access token. Please try again.")
        tokens["issued_at"] = datetime.now(UTC).timestamp()
        profile = fetch_profile(access_token)
        previous = self.session.account()
        account = Account.from_google_profile(profile, created_at=previous.created_at if previous else None)
        self.session.save(account, tokens)
        return account

    def access_token(self) -> str | None:
        """Return a current access token, refreshing it in the keyring when required."""
        tokens = self.session.credentials.get_oauth_tokens()
        if not tokens:
            return None
        access_token = tokens.get("access_token")
        expires_in = tokens.get("expires_in")
        issued_at = tokens.get("issued_at")
        expired = (
            isinstance(expires_in, (int, float))
            and isinstance(issued_at, (int, float))
            and datetime.now(UTC).timestamp() >= float(issued_at) + float(expires_in) - 60
        )
        if not expired and isinstance(access_token, str) and access_token:
            return access_token
        refresh_token = tokens.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            return None
        refreshed = refresh_access_token(
            client_id=self.config.client_id,
            client_secret=self.config.client_secret,
            refresh_token=refresh_token,
        )
        refreshed.setdefault("refresh_token", refresh_token)
        refreshed["issued_at"] = datetime.now(UTC).timestamp()
        self.session.credentials.set_oauth_tokens(refreshed)
        value = refreshed.get("access_token")
        return value if isinstance(value, str) and value else None
