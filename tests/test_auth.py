from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

import pytest

from code_agent.account.profile import AccountStore
from code_agent.auth.callback_server import LocalCallbackServer, OAuthCallback
from code_agent.auth.config import GoogleOAuthConfig, OAuthConfigurationError
from code_agent.auth.google import GoogleAuthenticator
from code_agent.auth.models import Account
from code_agent.auth.oauth import authorization_url
from code_agent.auth.pkce import code_challenge, generate_code_verifier
from code_agent.auth.session import LocalSession
from code_agent.credentials.providers import provider_spec, validate_provider_key
from code_agent.config import Settings


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


def test_pkce_challenge_is_sha256_urlsafe() -> None:
    verifier = generate_code_verifier()

    assert len(verifier) >= 43
    assert "=" not in verifier
    assert code_challenge("abc") == "ungWv48Bz-pBQUDeXa4iI7ADYaOWF3qctBD_YfIAFa0"


def test_authorization_url_uses_pkce_and_minimal_identity_scopes() -> None:
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


def test_callback_server_binds_loopback_and_receives_one_callback() -> None:
    with LocalCallbackServer() as server:
        assert server.redirect_uri.startswith("http://127.0.0.1:")
        with urlopen(server.redirect_uri + "?code=authorization-code&state=trusted", timeout=5) as response:  # noqa: S310
            assert response.status == 200
        callback = server.wait(1)

    assert callback == OAuthCallback("authorization-code", "trusted", None, None)


def test_account_store_keeps_only_non_sensitive_profile_data(tmp_path: Path) -> None:
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


def test_google_authenticator_saves_tokens_only_in_credential_store(monkeypatch, tmp_path: Path) -> None:
    class FakeCallbackServer:
        redirect_uri = "http://127.0.0.1:9999/callback"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def wait(self, _timeout):
            return OAuthCallback("code", "expected-state", None, None)

    credentials = FakeCredentials()
    session = LocalSession(accounts=AccountStore(tmp_path), credentials=credentials)  # type: ignore[arg-type]
    monkeypatch.setattr("code_agent.auth.google.LocalCallbackServer", FakeCallbackServer)
    monkeypatch.setattr("code_agent.auth.google.generate_state", lambda: "expected-state")
    monkeypatch.setattr("code_agent.auth.google.generate_code_verifier", lambda: "verifier")
    monkeypatch.setattr("code_agent.auth.google.exchange_code", lambda **_kwargs: {"access_token": "secret-token"})
    monkeypatch.setattr(
        "code_agent.auth.google.fetch_profile",
        lambda _token: {"sub": "google-id", "email": "ada@example.com", "name": "Ada"},
    )
    opened: list[str] = []
    auth = GoogleAuthenticator(GoogleOAuthConfig("client-id", None), session=session)

    account = auth.login(browser_open=lambda url: opened.append(url) or True)

    assert account.email == "ada@example.com"
    assert credentials.tokens and credentials.tokens["access_token"] == "secret-token"
    assert "secret-token" not in (tmp_path / "account.json").read_text(encoding="utf-8")
    assert opened


def test_oauth_config_requires_client_id(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)

    with pytest.raises(OAuthConfigurationError, match="GOOGLE_CLIENT_ID"):
        GoogleOAuthConfig.from_environment()


def test_provider_aliases_and_compatible_key_test_requirement() -> None:
    assert provider_spec("google").name == "gemini"
    assert validate_provider_key("compatible", "test-key") == (
        False,
        "OpenAI-compatible providers require --base-url.",
    )


def test_settings_prefers_os_backed_byok_key(monkeypatch) -> None:
    monkeypatch.setattr(
        "code_agent.credentials.keyring.CredentialStore.get_provider_key",
        lambda _self, provider: "secure-key" if provider == "gemini" else None,
    )
    settings = Settings(_env_file=None, agent_provider="gemini", gemini_api_key="env-key")

    assert settings.model_api_key == "secure-key"


def test_compatible_provider_requires_base_url() -> None:
    settings = Settings(_env_file=None, agent_provider="compatible", compatible_api_key="secure-key")

    with pytest.raises(RuntimeError, match="COMPATIBLE_BASE_URL"):
        settings.model_base_url
