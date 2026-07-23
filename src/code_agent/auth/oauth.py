from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_SCOPES = ("openid", "email", "profile")


class OAuthError(RuntimeError):
    pass


def authorization_url(*, client_id: str, redirect_uri: str, state: str, code_challenge: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
        }
    )
    return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{query}"


def exchange_code(
    *, client_id: str, client_secret: str | None, code: str, redirect_uri: str, verifier: str
) -> dict[str, Any]:
    payload: dict[str, str] = {
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if client_secret:
        payload["client_secret"] = client_secret
    return _post_form(GOOGLE_TOKEN_ENDPOINT, payload)


def refresh_access_token(*, client_id: str, client_secret: str | None, refresh_token: str) -> dict[str, Any]:
    payload = {"client_id": client_id, "refresh_token": refresh_token, "grant_type": "refresh_token"}
    if client_secret:
        payload["client_secret"] = client_secret
    return _post_form(GOOGLE_TOKEN_ENDPOINT, payload)


def fetch_profile(access_token: str) -> dict[str, Any]:
    request = Request(GOOGLE_USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed Google endpoint
            return _decode_json(response.read())
    except OSError as exc:
        raise OAuthError("Could not retrieve your Google profile. Check your network connection.") from exc


def _post_form(url: str, fields: dict[str, str]) -> dict[str, Any]:
    request = Request(
        url,
        data=urlencode(fields).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed Google endpoint
            return _decode_json(response.read())
    except OSError as exc:
        raise OAuthError("Google could not complete authentication. Check your network and try again.") from exc


def _decode_json(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OAuthError("Google returned an invalid authentication response.") from exc
    if not isinstance(value, dict):
        raise OAuthError("Google returned an invalid authentication response.")
    if value.get("error"):
        raise OAuthError("Google rejected authentication. Please try again.")
    return value
