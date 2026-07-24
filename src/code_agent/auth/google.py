"""Google OAuth authenticator — orchestrates the full local sign-in flow.

Coordinates PKCE generation, browser launch, localhost callback, token
exchange, profile fetch, and secure session persistence.  All failure modes
produce safe, actionable error messages without leaking secrets.
"""

from __future__ import annotations

import secrets
import sys
import webbrowser
from datetime import UTC, datetime

from ..account.profile import AccountStore
from ..credentials.keyring import CredentialStore
from ..local_server import LocalBrowserServer, OAuthHandler
from .config import GoogleOAuthConfig
from .models import Account
from .oauth import (
    OAuthError,
    OAuthRefreshError,
    authorization_url,
    exchange_code,
    fetch_profile,
    refresh_access_token,
)
from .pkce import code_challenge, generate_code_verifier, generate_state
from .session import LocalSession


class GoogleAuthenticator:
    """Local-first Google OAuth authenticator using Authorization Code + PKCE.

    Thread-safety: instances are not thread-safe.  Each CLI invocation
    creates its own authenticator.
    """

    def __init__(
        self,
        config: GoogleOAuthConfig | None = None,
        session: LocalSession | None = None,
    ) -> None:
        self.config = config or GoogleOAuthConfig.from_environment()
        self.session = session or LocalSession(AccountStore(), CredentialStore())

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def login(self, *, browser_open=webbrowser.open) -> Account:
        """Run the full OAuth Authorization Code + PKCE flow.

        1. Generate PKCE verifier + challenge and a random state.
        2. Start a single-use localhost callback server.
        3. Open the browser to the Google authorization URL.
        4. Wait for the callback with timeout.
        5. Validate state (timing-safe comparison).
        6. Exchange authorization code for tokens.
        7. Fetch user profile.
        8. Persist account metadata (no secrets) and tokens (keyring only).

        When the browser cannot be opened, the authorization URL is printed
        to stderr so users can open it manually.
        """
        verifier = generate_code_verifier()
        state = generate_state()
        with LocalBrowserServer(handler=OAuthHandler()) as callback_server:
            url = authorization_url(
                client_id=self.config.client_id,
                redirect_uri=callback_server.redirect_uri,
                state=state,
                code_challenge=code_challenge(verifier),
            )

            if not browser_open(url):
                # Print the URL so the user can open it manually in any browser.
                print(  # noqa: T201
                    "\nCould not open your browser automatically.\n"
                    "Open this URL manually to sign in:\n\n"
                    f"  {url}\n",
                    file=sys.stderr,
                )

            callback = callback_server.wait(self.config.timeout_seconds)
            redirect_uri = callback_server.redirect_uri

            if callback is None:
                raise OAuthError(
                    "Google sign-in timed out.  "
                    "Close the browser tab and run 'agent47 login' to try again."
                )

            try:
                # Timing-safe state comparison to prevent CSRF.
                if not callback.state or not secrets.compare_digest(callback.state, state):
                    raise OAuthError(
                        "Google sign-in was rejected because its security state did not match.  "
                        "This may indicate a CSRF attempt.  Please try again."
                    )

                if callback.error:
                    if callback.error == "access_denied":
                        raise OAuthError("Google sign-in was cancelled by the user.")
                    desc = f"  ({callback.error_description})" if callback.error_description else ""
                    raise OAuthError(
                        f"Google sign-in did not complete.{desc}  Please try again."
                    )

                if not callback.code:
                    raise OAuthError(
                        "Google returned an incomplete sign-in response.  Please try again."
                    )

                tokens = exchange_code(
                    client_id=self.config.client_id,
                    client_secret=self.config.client_secret,
                    code=callback.code,
                    redirect_uri=redirect_uri,
                    verifier=verifier,
                )

                access_token = tokens.get("access_token")
                if not isinstance(access_token, str) or not access_token:
                    raise OAuthError("Google did not return an access token.  Please try again.")

                tokens["issued_at"] = datetime.now(UTC).timestamp()
                profile = fetch_profile(access_token)

                # Signal the local server to render the success page
                callback_server.send_success(profile.get("name", "Unknown"), profile.get("email", "Unknown"))
            except OAuthError as exc:
                # Signal the local server to render the error page
                callback_server.send_error("Authentication Failed", str(exc))
                raise

        # Server is stopped before we persist credentials locally.
        previous = self.session.account()
        account = Account.from_google_profile(
            profile, created_at=previous.created_at if previous else None
        )
        self.session.save(account, tokens)
        return account

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def access_token(self) -> str | None:
        """Return a current access token, refreshing transparently when needed.

        Returns ``None`` when no session exists or when the refresh token has
        been revoked (the caller should prompt for re-authentication).
        """
        tokens = self.session.credentials.get_oauth_tokens()
        if not tokens:
            return None

        access_token = tokens.get("access_token")
        expires_in = tokens.get("expires_in")
        issued_at = tokens.get("issued_at")

        # Check whether the access token is still valid (with a 60-second
        # safety margin to avoid using tokens that expire mid-request).
        expired = (
            isinstance(expires_in, (int, float))
            and isinstance(issued_at, (int, float))
            and datetime.now(UTC).timestamp() >= float(issued_at) + float(expires_in) - 60
        )

        if not expired and isinstance(access_token, str) and access_token:
            return access_token

        # Try to refresh.
        refresh_token = tokens.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            return None

        try:
            refreshed = refresh_access_token(
                client_id=self.config.client_id,
                client_secret=self.config.client_secret,
                refresh_token=refresh_token,
            )
        except OAuthRefreshError:
            # The refresh token has been revoked or expired.  Clear the
            # local session so the next caller triggers re-authentication.
            self.session.clear()
            return None
        except OAuthError:
            # Transient network error — don't clear the session, but we
            # can't provide a valid token right now.
            return None

        # Preserve the refresh token if the server didn't issue a new one.
        refreshed.setdefault("refresh_token", refresh_token)
        refreshed["issued_at"] = datetime.now(UTC).timestamp()
        self.session.credentials.set_oauth_tokens(refreshed)

        value = refreshed.get("access_token")
        return value if isinstance(value, str) and value else None

    # ------------------------------------------------------------------
    # Explicit refresh
    # ------------------------------------------------------------------

    def refresh(self) -> str | None:
        """Force-refresh the access token.  Returns the new token or None."""
        tokens = self.session.credentials.get_oauth_tokens()
        if not tokens:
            return None

        refresh_token = tokens.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            return None

        try:
            refreshed = refresh_access_token(
                client_id=self.config.client_id,
                client_secret=self.config.client_secret,
                refresh_token=refresh_token,
            )
        except OAuthRefreshError:
            self.session.clear()
            return None
        except OAuthError:
            return None

        refreshed.setdefault("refresh_token", refresh_token)
        refreshed["issued_at"] = datetime.now(UTC).timestamp()
        self.session.credentials.set_oauth_tokens(refreshed)

        value = refreshed.get("access_token")
        return value if isinstance(value, str) and value else None

    # ------------------------------------------------------------------
    # Repair
    # ------------------------------------------------------------------

    def repair(self) -> list[str]:
        """Attempt to recover from broken authentication state.

        Returns a list of human-readable actions taken.  If nothing is wrong,
        returns an empty list.
        """
        actions: list[str] = []

        # 1. Check account metadata.
        account = self.session.account()
        tokens = self.session.credentials.get_oauth_tokens()

        # Tokens exist but no account profile → stale state.
        if tokens and not account:
            access_token = tokens.get("access_token")
            if isinstance(access_token, str) and access_token:
                try:
                    profile = fetch_profile(access_token)
                    recovered = Account.from_google_profile(profile)
                    self.session.accounts.save(recovered)
                    actions.append("Recovered account profile from access token.")
                except OAuthError:
                    pass

            if not self.session.account():
                # Try refreshing to get a valid access token.
                new_token = self.refresh()
                if new_token:
                    try:
                        profile = fetch_profile(new_token)
                        recovered = Account.from_google_profile(profile)
                        self.session.accounts.save(recovered)
                        actions.append("Recovered account profile after token refresh.")
                    except OAuthError:
                        pass

            if not self.session.account():
                self.session.credentials.delete_oauth_tokens()
                actions.append("Cleared orphaned tokens (no recoverable profile).")

        # Account exists but no tokens → remove stale profile.
        if account and not tokens:
            self.session.accounts.delete()
            actions.append("Removed stale account profile (no tokens found).")

        # Both exist — try to validate the session is still live.
        if account and tokens:
            refresh_token = tokens.get("refresh_token")
            if not isinstance(refresh_token, str) or not refresh_token:
                # No refresh token — session will expire soon.
                actions.append("Warning: no refresh token stored.  Session will expire.")
            else:
                # Verify we can still refresh.
                access_token = tokens.get("access_token")
                expires_in = tokens.get("expires_in")
                issued_at = tokens.get("issued_at")
                expired = (
                    isinstance(expires_in, (int, float))
                    and isinstance(issued_at, (int, float))
                    and datetime.now(UTC).timestamp()
                    >= float(issued_at) + float(expires_in) - 60
                )
                if expired or not (isinstance(access_token, str) and access_token):
                    new_token = self.refresh()
                    if new_token:
                        actions.append("Refreshed expired access token.")
                    else:
                        self.session.clear()
                        actions.append(
                            "Session could not be refreshed.  "
                            "Run 'agent47 login' to sign in again."
                        )

        return actions
