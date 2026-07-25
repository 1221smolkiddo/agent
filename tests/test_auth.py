"""Comprehensive auth system tests — production hardening coverage.

Tests cover:
- PKCE generation (verifier entropy, challenge derivation, S256 only)
- State validation (mismatch, empty, replay)
- Callback server (loopback binding, replay rejection, timeout, non-GET, malformed)
- Browser failure (URL printed to stderr)
- Logout cleanup (profile + tokens)
- Refresh token flow (success + revoked)
- Missing keyring
- Port unavailable error
- Auth repair
"""

from __future__ import annotations

import hashlib
import base64
import re
import time
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import pytest

from code_agent.account.profile import AccountStore
from code_agent.local_server import LocalBrowserServer, OAuthHandler, OAuthCallback, _render_page
from code_agent.auth.config import GoogleOAuthConfig, OAuthConfigurationError
from code_agent.auth.google import GoogleAuthenticator
from code_agent.auth.models import Account
from code_agent.auth.oauth import OAuthError, OAuthRefreshError, authorization_url
from code_agent.auth.pkce import (
    CODE_CHALLENGE_METHOD,
    code_challenge,
    generate_code_verifier,
    generate_state,
)
from code_agent.auth.session import LocalSession
from code_agent.credentials.providers import provider_spec, validate_provider_key
from code_agent.credentials.keyring import KeyringUnavailableError
from code_agent.config import Settings


# ======================================================================
# Fakes
# ======================================================================


class FakeCredentials:
    def __init__(self) -> None:
        self.tokens: dict[str, object] | None = None

    def get_oauth_tokens(self):
        return self.tokens

    def set_oauth_tokens(self, tokens):
        self.tokens = dict(tokens)

    def delete_oauth_tokens(self):
        had_tokens = self.tokens is not None
        self.tokens = None
        return had_tokens


class FakeCallbackServer:
    redirect_uri = "http://127.0.0.1:9999/callback"

    def __init__(self, callback=None):
        self._callback = callback

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def wait(self, _timeout):
        return self._callback
        
    def send_error(self, title, message):
        pass

    def send_success(self, name, email):
        pass


# ======================================================================
# PKCE tests
# ======================================================================


class TestPKCE:
    def test_verifier_length_and_entropy(self) -> None:
        """Verifier must be ≥43 base64url characters (RFC 7636 §4.1)."""
        verifier = generate_code_verifier()
        assert len(verifier) >= 43
        assert len(verifier) <= 128
        # No padding characters.
        assert "=" not in verifier
        # Only URL-safe base64 characters.
        assert re.match(r"^[A-Za-z0-9_-]+$", verifier)

    def test_verifier_is_unique(self) -> None:
        """Each verifier must be unique (CSPRNG)."""
        verifiers = {generate_code_verifier() for _ in range(100)}
        assert len(verifiers) == 100

    def test_challenge_is_sha256_urlsafe(self) -> None:
        """code_challenge must be BASE64URL(SHA256(verifier)) without padding."""
        verifier = generate_code_verifier()
        expected_digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(expected_digest).rstrip(b"=").decode("ascii")
        assert code_challenge(verifier) == expected

    def test_challenge_known_vector(self) -> None:
        """Known test vector: SHA256("abc") = ungWv48B..."""
        assert code_challenge("abc") == "ungWv48Bz-pBQUDeXa4iI7ADYaOWF3qctBD_YfIAFa0"

    def test_challenge_method_is_s256(self) -> None:
        """CODE_CHALLENGE_METHOD must be S256, not plain."""
        assert CODE_CHALLENGE_METHOD == "S256"

    def test_state_is_cryptographic(self) -> None:
        """State must be unique and URL-safe."""
        state = generate_state()
        assert len(state) >= 32
        assert "=" not in state
        states = {generate_state() for _ in range(100)}
        assert len(states) == 100


# ======================================================================
# Authorization URL tests
# ======================================================================


class TestAuthorizationURL:
    def test_uses_pkce_s256_and_minimal_scopes(self) -> None:
        query = parse_qs(
            urlparse(
                authorization_url(
                    client_id="client-id",
                    redirect_uri="http://127.0.0.1:1234/callback",
                    state="state-value",
                    code_challenge="challenge-value",
                )
            ).query
        )
        assert query["response_type"] == ["code"]
        assert query["code_challenge_method"] == ["S256"]
        assert query["scope"] == ["openid email profile"]
        assert query["state"] == ["state-value"]
        assert query["code_challenge"] == ["challenge-value"]
        assert query["access_type"] == ["offline"]

    def test_no_plain_challenge_method(self) -> None:
        url = authorization_url(
            client_id="c",
            redirect_uri="http://127.0.0.1:1/callback",
            state="s",
            code_challenge="ch",
        )
        assert "plain" not in url.lower()
        assert "S256" in url


# ======================================================================
# Callback server tests
# ======================================================================


class TestCallbackServer:
    def test_binds_loopback_only(self) -> None:
        with LocalBrowserServer(handler=OAuthHandler()) as server:
            assert server.redirect_uri.startswith("http://127.0.0.1:")
            assert "0.0.0.0" not in server.redirect_uri

    def test_uses_dynamic_port(self) -> None:
        """Port must be >0 (auto-allocated)."""
        with LocalBrowserServer(handler=OAuthHandler()) as server:
            port = int(server.redirect_uri.split(":")[2].split("/")[0])
            assert port > 0

    def test_receives_one_callback(self) -> None:
        with LocalBrowserServer(handler=OAuthHandler()) as server:
            server.send_success("Test", "t@t.com")
            with urlopen(  # noqa: S310
                server.redirect_uri + "?code=authorization-code&state=trusted",
                timeout=5,
            ) as response:
                assert response.status == 200
            callback = server.wait(1)
        assert callback == OAuthCallback("authorization-code", "trusted", None, None)

    def test_rejects_non_callback_path(self) -> None:
        with LocalBrowserServer(handler=OAuthHandler()) as server:
            base = server.redirect_uri.replace("/callback", "")
            try:
                from urllib.error import HTTPError

                with urlopen(base + "/wrong?code=x&state=y", timeout=5):  # noqa: S310
                    pass
                pytest.fail("Expected 404")
            except HTTPError as exc:
                assert exc.code == 404

    def test_rejects_repeated_callbacks(self) -> None:
        """Second callback must be rejected with 409 to prevent replay."""
        with LocalBrowserServer(handler=OAuthHandler()) as server:
            server.send_success("Test", "t@t.com")
            # First request — accepted.
            with urlopen(  # noqa: S310
                server.redirect_uri + "?code=first&state=s", timeout=5
            ) as resp:
                assert resp.status == 200
            # Second request — rejected.
            try:
                from urllib.error import HTTPError

                with urlopen(  # noqa: S310
                    server.redirect_uri + "?code=second&state=s", timeout=5
                ):
                    pass
                pytest.fail("Expected 409")
            except HTTPError as exc:
                assert exc.code == 409

    def test_timeout_returns_none(self) -> None:
        with LocalBrowserServer(handler=OAuthHandler()) as server:
            result = server.wait(0.01)
        assert result is None

    def test_close_before_start_is_safe(self) -> None:
        server = LocalBrowserServer(handler=OAuthHandler())
        server.close()

    def test_close_is_idempotent(self) -> None:
        server = LocalBrowserServer(handler=OAuthHandler())
        server.start()
        server.close()
        server.close()

    def test_close_after_failed_start_is_safe(self, monkeypatch) -> None:
        server = LocalBrowserServer(handler=OAuthHandler())

        def fail_start() -> None:
            raise RuntimeError("thread could not start")

        monkeypatch.setattr(server._thread, "start", fail_start)
        with pytest.raises(RuntimeError, match="thread could not start"):
            server.start()
        server.close()

    def test_rejects_post_method(self) -> None:
        with LocalBrowserServer(handler=OAuthHandler()) as server:
            try:
                from urllib.error import HTTPError

                req = Request(
                    server.redirect_uri + "?code=x&state=y",
                    data=b"",
                    method="POST",
                )
                with urlopen(req, timeout=5):  # noqa: S310
                    pass
                pytest.fail("Expected 405")
            except HTTPError as exc:
                assert exc.code == 405

    def test_malformed_callback_query(self) -> None:
        """Missing code and state should still produce a valid OAuthCallback."""
        with LocalBrowserServer(handler=OAuthHandler()) as server:
            server.send_success("Test", "t@t.com")
            with urlopen(server.redirect_uri + "?random=garbage", timeout=5) as resp:  # noqa: S310
                assert resp.status == 200
            callback = server.wait(1)
        assert callback is not None
        assert callback.code is None
        assert callback.state is None

    def test_synchronization_event_blocks_and_delivers_success(self) -> None:
        """Verify the callback handler blocks until the main thread signals success."""
        import concurrent.futures

        with LocalBrowserServer(handler=OAuthHandler()) as server:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                request_started = threading.Event()

                def make_request():
                    request_started.set()
                    return urlopen(server.redirect_uri + "?code=sync&state=trusted", timeout=5)

                # 1. Start the HTTP request in a background thread.
                future = executor.submit(make_request)
                assert request_started.wait(timeout=2)
                
                # 2. Main thread waits for the code to be extracted.
                callback = server.wait(2)
                assert callback is not None
                assert callback.code == "sync"
                
                # 3. Verify the HTTP request is STILL blocked waiting for response event.
                assert not future.done()
                
                # 4. Signal success to unblock the HTTP request.
                server.send_success("Sync Test", "sync@test.com")
                
                # 5. Verify the HTTP request now completes successfully.
                response = future.result(timeout=2)
                assert response.status == 200
                html = response.read().decode("utf-8")
                assert "Sync Test" in html
                assert "sync@test.com" in html

    def test_synchronization_event_blocks_and_delivers_error(self) -> None:
        """Verify the callback handler can also block and deliver an error page."""
        import concurrent.futures
        from urllib.error import HTTPError

        with LocalBrowserServer(handler=OAuthHandler()) as server:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                request_started = threading.Event()

                def make_request():
                    request_started.set()
                    return urlopen(server.redirect_uri + "?code=err&state=trusted", timeout=5)

                future = executor.submit(make_request)
                assert request_started.wait(timeout=2)
                
                callback = server.wait(2)
                assert callback is not None
                
                assert not future.done()
                
                server.send_error("Test Failure", "Invalid state detected.")
                
                # The HTTPError is raised because urlopen treats 400 as an error.
                # Let's catch it and verify the HTML body.
                try:
                    future.result(timeout=2)
                    pytest.fail("Expected HTTPError 400")
                except HTTPError as exc:
                    assert exc.code == 400
                    html = exc.read().decode("utf-8")
                    assert "Test Failure" in html
                    assert "Invalid state detected." in html


# ======================================================================
# State validation tests
# ======================================================================


class TestStateValidation:
    def _make_authenticator(
        self, monkeypatch, tmp_path, callback_state, expected_state="expected-state"
    ):
        class TestCallbackServer:
            redirect_uri = "http://127.0.0.1:9999/callback"

            def __init__(self, handler=None):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def wait(self, _timeout):
                return OAuthCallback("code", callback_state, None, None)

            def send_error(self, title, message):
                pass

            def send_success(self, name, email):
                pass

        credentials = FakeCredentials()
        session = LocalSession(accounts=AccountStore(tmp_path), credentials=credentials)
        monkeypatch.setattr("code_agent.auth.google.LocalBrowserServer", TestCallbackServer)
        monkeypatch.setattr(
            "code_agent.auth.google.generate_state", lambda: expected_state
        )
        monkeypatch.setattr(
            "code_agent.auth.google.generate_code_verifier", lambda: "verifier"
        )
        return GoogleAuthenticator(GoogleOAuthConfig("client-id", None), session=session)

    def test_rejects_state_mismatch(self, monkeypatch, tmp_path) -> None:
        auth = self._make_authenticator(monkeypatch, tmp_path, "wrong-state")
        with pytest.raises(OAuthError, match="security state did not match"):
            auth.login(browser_open=lambda url: True)

    def test_rejects_empty_state(self, monkeypatch, tmp_path) -> None:
        auth = self._make_authenticator(monkeypatch, tmp_path, "")
        with pytest.raises(OAuthError, match="security state did not match"):
            auth.login(browser_open=lambda url: True)

    def test_rejects_none_state(self, monkeypatch, tmp_path) -> None:
        auth = self._make_authenticator(monkeypatch, tmp_path, None)
        with pytest.raises(OAuthError, match="security state did not match"):
            auth.login(browser_open=lambda url: True)


# ======================================================================
# Browser failure test
# ======================================================================


class TestBrowserFailure:
    def test_login_continues_when_browser_fails(self, monkeypatch, tmp_path) -> None:
        """When browser_open returns False, login should still wait for callback."""

        class NoopServer:
            redirect_uri = "http://127.0.0.1:9999/callback"

            def __init__(self, handler=None):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def wait(self, _timeout):
                return OAuthCallback("code", "expected-state", None, None)
                
            def send_error(self, title, message):
                pass

            def send_success(self, name, email):
                pass

        credentials = FakeCredentials()
        session = LocalSession(
            accounts=AccountStore(tmp_path), credentials=credentials
        )
        monkeypatch.setattr("code_agent.auth.google.LocalBrowserServer", NoopServer)
        monkeypatch.setattr(
            "code_agent.auth.google.generate_state", lambda: "expected-state"
        )
        monkeypatch.setattr(
            "code_agent.auth.google.generate_code_verifier", lambda: "verifier"
        )
        monkeypatch.setattr(
            "code_agent.auth.google.exchange_code",
            lambda **_kw: {"access_token": "tok"},
        )
        monkeypatch.setattr(
            "code_agent.auth.google.fetch_profile",
            lambda _t: {"sub": "id", "email": "e@x.com", "name": "E"},
        )

        auth = GoogleAuthenticator(
            GoogleOAuthConfig("client-id", None), session=session
        )
        # browser_open returns False — login should still succeed because
        # the callback server received the code.
        account = auth.login(browser_open=lambda url: False)
        assert account.email == "e@x.com"


# ======================================================================
# Google Authenticator integration tests
# ======================================================================


class TrackingCallbackServer:
    redirect_uri = "http://127.0.0.1:9999/callback"

    def __init__(self, callback=None):
        self._callback = callback
        self.success_calls = []
        self.error_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def wait(self, _timeout):
        return self._callback

    def send_error(self, title, message):
        self.error_calls.append((title, message))

    def send_success(self, name, email):
        self.success_calls.append((name, email))


class TestGoogleAuthenticator:
    def test_saves_tokens_only_in_credential_store(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        credentials = FakeCredentials()
        session = LocalSession(
            accounts=AccountStore(tmp_path), credentials=credentials
        )
        monkeypatch.setattr(
            "code_agent.auth.google.LocalBrowserServer",
            lambda handler=None: FakeCallbackServer(
                OAuthCallback("code", "expected-state", None, None)
            ),
        )
        monkeypatch.setattr(
            "code_agent.auth.google.generate_state", lambda: "expected-state"
        )
        monkeypatch.setattr(
            "code_agent.auth.google.generate_code_verifier", lambda: "verifier"
        )
        monkeypatch.setattr(
            "code_agent.auth.google.exchange_code",
            lambda **_kwargs: {"access_token": "secret-token"},
        )
        monkeypatch.setattr(
            "code_agent.auth.google.fetch_profile",
            lambda _token: {
                "sub": "google-id",
                "email": "ada@example.com",
                "name": "Ada",
            },
        )
        auth = GoogleAuthenticator(
            GoogleOAuthConfig("client-id", None), session=session
        )

        account = auth.login(browser_open=lambda url: True)

        assert account.email == "ada@example.com"
        assert credentials.tokens and credentials.tokens["access_token"] == "secret-token"
        assert "secret-token" not in (tmp_path / "account.json").read_text(
            encoding="utf-8"
        )

    def test_login_successful_persists_before_send_success(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Verify successful login stores credentials and calls send_success."""
        server = TrackingCallbackServer(
            OAuthCallback("code", "expected-state", None, None)
        )
        events: list[str] = []

        class OrderedCredentials(FakeCredentials):
            def set_oauth_tokens(self, tokens):
                events.append("save")
                super().set_oauth_tokens(tokens)

        class OrderedServer(TrackingCallbackServer):
            def send_success(self, name, email):
                events.append("success")
                super().send_success(name, email)

        server = OrderedServer(OAuthCallback("code", "expected-state", None, None))
        credentials = OrderedCredentials()
        session = LocalSession(
            accounts=AccountStore(tmp_path), credentials=credentials
        )
        monkeypatch.setattr("code_agent.auth.google.LocalBrowserServer", lambda handler=None: server)
        monkeypatch.setattr("code_agent.auth.google.generate_state", lambda: "expected-state")
        monkeypatch.setattr("code_agent.auth.google.generate_code_verifier", lambda: "verifier")
        monkeypatch.setattr("code_agent.auth.google.exchange_code", lambda **_kw: {"access_token": "tok"})
        monkeypatch.setattr(
            "code_agent.auth.google.fetch_profile",
            lambda _t: {"sub": "id", "email": "user@example.com", "name": "User Name"},
        )
        auth = GoogleAuthenticator(GoogleOAuthConfig("client-id", None), session=session)

        account = auth.login(browser_open=lambda url: True)

        assert account.email == "user@example.com"
        assert credentials.tokens and credentials.tokens["access_token"] == "tok"
        assert server.success_calls == [("User Name", "user@example.com")]
        assert server.error_calls == []
        assert events == ["save", "success"]

    def test_login_storage_failure_sends_browser_error_and_reraises(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Verify storage failure calls send_error, does not call send_success, and re-raises exception."""
        server = TrackingCallbackServer(
            OAuthCallback("code", "expected-state", None, None)
        )

        class FailingCredentials(FakeCredentials):
            def set_oauth_tokens(self, tokens):
                raise KeyringUnavailableError("Keyring is locked by OS")

        credentials = FailingCredentials()
        session = LocalSession(
            accounts=AccountStore(tmp_path), credentials=credentials
        )
        monkeypatch.setattr("code_agent.auth.google.LocalBrowserServer", lambda handler=None: server)
        monkeypatch.setattr("code_agent.auth.google.generate_state", lambda: "expected-state")
        monkeypatch.setattr("code_agent.auth.google.generate_code_verifier", lambda: "verifier")
        monkeypatch.setattr("code_agent.auth.google.exchange_code", lambda **_kw: {"access_token": "tok"})
        monkeypatch.setattr(
            "code_agent.auth.google.fetch_profile",
            lambda _t: {"sub": "id", "email": "user@example.com", "name": "User Name"},
        )
        auth = GoogleAuthenticator(GoogleOAuthConfig("client-id", None), session=session)

        with pytest.raises(KeyringUnavailableError, match="Keyring is locked by OS"):
            auth.login(browser_open=lambda url: True)

        assert server.success_calls == []
        assert len(server.error_calls) == 1
        assert server.error_calls[0][0] == "Storage Failed"
        assert "Keyring is locked by OS" in server.error_calls[0][1]

    def test_login_account_save_failure_rolls_back_tokens_and_sends_browser_error(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        server = TrackingCallbackServer(OAuthCallback("code", "expected-state", None, None))

        class FailingAccountStore(AccountStore):
            def save(self, account):
                raise OSError("disk is read-only")

        credentials = FakeCredentials()
        session = LocalSession(accounts=FailingAccountStore(tmp_path), credentials=credentials)
        monkeypatch.setattr("code_agent.auth.google.LocalBrowserServer", lambda handler=None: server)
        monkeypatch.setattr("code_agent.auth.google.generate_state", lambda: "expected-state")
        monkeypatch.setattr("code_agent.auth.google.generate_code_verifier", lambda: "verifier")
        monkeypatch.setattr("code_agent.auth.google.exchange_code", lambda **_kw: {"access_token": "tok"})
        monkeypatch.setattr(
            "code_agent.auth.google.fetch_profile",
            lambda _t: {"sub": "id", "email": "user@example.com", "name": "User Name"},
        )

        with pytest.raises(OSError, match="disk is read-only"):
            GoogleAuthenticator(GoogleOAuthConfig("client-id", None), session=session).login(
                browser_open=lambda _url: True
            )

        assert credentials.tokens is None
        assert server.success_calls == []
        assert server.error_calls == [("Storage Failed", "Could not save credentials: disk is read-only")]

    def test_login_oauth_failure_sends_browser_error_and_reraises(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Verify OAuth failure (e.g. state mismatch) calls send_error and re-raises OAuthError."""
        server = TrackingCallbackServer(
            OAuthCallback("code", "wrong-state", None, None)
        )
        credentials = FakeCredentials()
        session = LocalSession(
            accounts=AccountStore(tmp_path), credentials=credentials
        )
        monkeypatch.setattr("code_agent.auth.google.LocalBrowserServer", lambda handler=None: server)
        monkeypatch.setattr("code_agent.auth.google.generate_state", lambda: "expected-state")
        monkeypatch.setattr("code_agent.auth.google.generate_code_verifier", lambda: "verifier")
        auth = GoogleAuthenticator(GoogleOAuthConfig("client-id", None), session=session)

        with pytest.raises(OAuthError, match="security state did not match"):
            auth.login(browser_open=lambda url: True)

        assert server.success_calls == []
        assert len(server.error_calls) == 1
        assert server.error_calls[0][0] == "Authentication Failed"
        assert "security state did not match" in server.error_calls[0][1]


# ======================================================================
# Logout cleanup tests
# ======================================================================


class TestLogoutCleanup:
    def test_logout_clears_profile_and_tokens(self, tmp_path) -> None:
        account = Account(
            user_id="id",
            name="Test",
            email="test@example.com",
            picture_url=None,
            provider="google",
            created_at="2026-01-01",
            last_login_at="2026-01-01",
        )
        credentials = FakeCredentials()
        credentials.tokens = {"access_token": "tok", "refresh_token": "ref"}
        session = LocalSession(
            accounts=AccountStore(tmp_path), credentials=credentials
        )
        session.save(account, {"access_token": "tok", "refresh_token": "ref"})

        profile_deleted, tokens_deleted = session.clear()

        assert profile_deleted
        assert tokens_deleted
        assert session.account() is None
        assert credentials.tokens is None


class TestTemplateEscaping:
    def test_dynamic_values_are_rendered_as_text(self) -> None:
        rendered = _render_page(
            "success.html",
            "success.css",
            None,
            {"name": '<script>alert("x")</script>', "email": 'a"b@example.com'},
        )

        assert "<script>alert" not in rendered
        assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in rendered
        assert "a&quot;b@example.com" in rendered


# ======================================================================
# Refresh token flow tests
# ======================================================================


class TestRefreshTokenFlow:
    def test_refresh_updates_stored_tokens(self, monkeypatch, tmp_path) -> None:
        credentials = FakeCredentials()
        credentials.tokens = {
            "access_token": "old-token",
            "refresh_token": "valid-refresh",
            "expires_in": 3600,
            "issued_at": 0,  # Already expired.
        }
        session = LocalSession(
            accounts=AccountStore(tmp_path), credentials=credentials
        )
        monkeypatch.setattr(
            "code_agent.auth.google.refresh_access_token",
            lambda **_kw: {"access_token": "new-token"},
        )
        auth = GoogleAuthenticator(
            GoogleOAuthConfig("client-id", None), session=session
        )

        token = auth.access_token()

        assert token == "new-token"
        assert credentials.tokens["access_token"] == "new-token"
        assert credentials.tokens["refresh_token"] == "valid-refresh"

    def test_invalid_refresh_clears_session(self, monkeypatch, tmp_path) -> None:
        account = Account(
            user_id="id",
            name="Test",
            email="test@example.com",
            picture_url=None,
            provider="google",
            created_at="2026-01-01",
            last_login_at="2026-01-01",
        )
        credentials = FakeCredentials()
        credentials.tokens = {
            "access_token": "old-token",
            "refresh_token": "revoked-refresh",
            "expires_in": 3600,
            "issued_at": 0,
        }
        accounts = AccountStore(tmp_path)
        accounts.save(account)
        session = LocalSession(accounts=accounts, credentials=credentials)

        def raise_refresh(**_kw):
            raise OAuthRefreshError("revoked")

        monkeypatch.setattr(
            "code_agent.auth.google.refresh_access_token", raise_refresh
        )
        auth = GoogleAuthenticator(
            GoogleOAuthConfig("client-id", None), session=session
        )

        result = auth.access_token()

        assert result is None
        # Session should be cleared.
        assert credentials.tokens is None
        assert session.account() is None


# ======================================================================
# Auth repair tests
# ======================================================================


class TestAuthRepair:
    def test_repair_healthy_session(self, monkeypatch, tmp_path) -> None:
        """Healthy session → no actions."""
        account = Account(
            user_id="id",
            name="Test",
            email="test@example.com",
            picture_url=None,
            provider="google",
            created_at="2026-01-01",
            last_login_at="2026-01-01",
        )
        credentials = FakeCredentials()
        credentials.tokens = {
            "access_token": "valid-token",
            "refresh_token": "valid-refresh",
            "expires_in": 99999,
            "issued_at": time.time(),
        }
        accounts = AccountStore(tmp_path)
        accounts.save(account)
        session = LocalSession(accounts=accounts, credentials=credentials)
        auth = GoogleAuthenticator(
            GoogleOAuthConfig("client-id", None), session=session
        )

        actions = auth.repair()
        assert actions == []

    def test_repair_stale_profile_no_tokens(self, tmp_path) -> None:
        """Account exists but no tokens → remove stale profile."""
        account = Account(
            user_id="id",
            name="Test",
            email="test@example.com",
            picture_url=None,
            provider="google",
            created_at="2026-01-01",
            last_login_at="2026-01-01",
        )
        credentials = FakeCredentials()
        credentials.tokens = None
        accounts = AccountStore(tmp_path)
        accounts.save(account)
        session = LocalSession(accounts=accounts, credentials=credentials)
        auth = GoogleAuthenticator(
            GoogleOAuthConfig("client-id", None), session=session
        )

        actions = auth.repair()
        assert any("stale account profile" in a.lower() for a in actions)
        assert session.account() is None


# ======================================================================
# Exchange code tests
# ======================================================================

class TestExchangeCode:
    def test_exchange_code_includes_client_secret_for_google_desktop_client(self, monkeypatch) -> None:
        captured_payload = {}
        
        def mock_post_token(url: str, fields: dict) -> dict:
            nonlocal captured_payload
            captured_payload = fields
            return {"access_token": "token"}
            
        monkeypatch.setattr("code_agent.auth.oauth._post_token", mock_post_token)
        
        from code_agent.auth.oauth import exchange_code
        
        exchange_code(
            client_id="test-client-id",
            client_secret="test-client-secret",
            code="test-code",
            redirect_uri="http://127.0.0.1:8080",
            verifier="test-verifier"
        )
        
        assert captured_payload["client_id"] == "test-client-id"
        assert captured_payload["client_secret"] == "test-client-secret"
        
    def test_exchange_code_omits_client_secret_if_none(self, monkeypatch) -> None:
        captured_payload = {}
        
        def mock_post_token(url: str, fields: dict) -> dict:
            nonlocal captured_payload
            captured_payload = fields
            return {"access_token": "token"}
            
        monkeypatch.setattr("code_agent.auth.oauth._post_token", mock_post_token)
        
        from code_agent.auth.oauth import exchange_code
        
        exchange_code(
            client_id="test-client-id",
            client_secret=None,
            code="test-code",
            redirect_uri="http://127.0.0.1:8080",
            verifier="test-verifier"
        )
        
        assert "client_secret" not in captured_payload

# ======================================================================
# Config tests
# ======================================================================

class TestOAuthConfig:
    def test_requires_client_id(self, monkeypatch) -> None:
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.setattr("code_agent.auth.config.GOOGLE_CLIENT_ID", "")
        with pytest.raises(OAuthConfigurationError, match="Developer Error: No Google OAuth Client ID"):
            GoogleOAuthConfig.from_environment()

    def test_packaged_client_id_is_used_after_install(self, monkeypatch) -> None:
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.setattr("code_agent.auth.config.GOOGLE_CLIENT_ID", "packaged-client-id")
        config = GoogleOAuthConfig.from_environment()
        assert config.client_id == "packaged-client-id"

    def test_uses_env_override_client_id(self, monkeypatch) -> None:
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "env-client-id")
        monkeypatch.setattr("code_agent.auth.config.GOOGLE_CLIENT_ID", "packaged-client-id")
        config = GoogleOAuthConfig.from_environment()
        assert config.client_id == "env-client-id"

    def test_uses_embedded_client_secret_when_env_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
        monkeypatch.setattr("code_agent.auth.config.GOOGLE_CLIENT_SECRET", "packaged-secret")
        config = GoogleOAuthConfig.from_environment()
        assert config.client_secret == "packaged-secret"

    def test_env_client_secret_overrides_embedded_secret(self, monkeypatch) -> None:
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "env-secret")
        monkeypatch.setattr("code_agent.auth.config.GOOGLE_CLIENT_SECRET", "packaged-secret")
        config = GoogleOAuthConfig.from_environment()
        assert config.client_secret == "env-secret"

    def test_timeout_bounds(self, monkeypatch) -> None:
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client")
        monkeypatch.setenv("GOOGLE_OAUTH_TIMEOUT_SECONDS", "0")
        with pytest.raises(OAuthConfigurationError, match="between 1 and 900"):
            GoogleOAuthConfig.from_environment()

    def test_timeout_non_numeric(self, monkeypatch) -> None:
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client")
        monkeypatch.setenv("GOOGLE_OAUTH_TIMEOUT_SECONDS", "abc")
        with pytest.raises(OAuthConfigurationError, match="must be a number"):
            GoogleOAuthConfig.from_environment()


# ======================================================================
# Provider tests
# ======================================================================


class TestProviders:
    def test_provider_aliases(self) -> None:
        assert provider_spec("google").name == "gemini"
        assert provider_spec("google-gemini").name == "gemini"

    def test_compatible_requires_base_url(self) -> None:
        assert validate_provider_key("compatible", "test-key") == (
            False,
            "OpenAI-compatible providers require --base-url.",
        )

    def test_settings_prefers_keyring_key(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "code_agent.credentials.keyring.CredentialStore.get_provider_key",
            lambda _self, provider: "secure-key" if provider == "gemini" else None,
        )
        settings = Settings(
            _env_file=None, agent_provider="gemini", gemini_api_key="env-key"
        )
        assert settings.model_api_key == "secure-key"

    def test_compatible_provider_requires_base_url(self) -> None:
        settings = Settings(
            _env_file=None,
            agent_provider="compatible",
            compatible_api_key="secure-key",
        )
        with pytest.raises(RuntimeError, match="COMPATIBLE_BASE_URL"):
            settings.model_base_url


# ======================================================================
# Account store tests
# ======================================================================


class TestAccountStore:
    def test_keeps_only_non_sensitive_data(self, tmp_path: Path) -> None:
        account = Account(
            user_id="google-id",
            name="Ada Lovelace",
            email="ada@example.com",
            picture_url=None,
            provider="google",
            created_at="2026-01-01T00:00:00+00:00",
            last_login_at="2026-01-01T00:00:00+00:00",
        )
        store = AccountStore(tmp_path)
        store.save(account)

        assert store.load() == account
        assert "token" not in store.path.read_text(encoding="utf-8").lower()
