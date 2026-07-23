"""Low-level Google OAuth 2.0 HTTP helpers.

These functions perform direct HTTP against fixed Google endpoints using only
the standard library.  Secrets (tokens, authorization codes) are never included
in exception messages, log output, or stack traces.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .pkce import CODE_CHALLENGE_METHOD

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_SCOPES = ("openid", "email", "profile")


class OAuthError(RuntimeError):
    """Raised for any OAuth protocol failure.

    Messages are safe to display to end users — they never contain tokens,
    authorization codes, or other secret material.
    """


class OAuthRefreshError(OAuthError):
    """Raised specifically when a refresh token is rejected by Google.

    Callers should clear the stored session and prompt for re-authentication.
    """


def authorization_url(
    *, client_id: str, redirect_uri: str, state: str, code_challenge: str
) -> str:
    """Build a Google authorization endpoint URL with PKCE (S256 only)."""
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": CODE_CHALLENGE_METHOD,
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{query}"


def exchange_code(
    *,
    client_id: str,
    client_secret: str | None,
    code: str,
    redirect_uri: str,
    verifier: str,
) -> dict[str, Any]:
    """Exchange an authorization code for tokens at the Google token endpoint."""
    payload: dict[str, str] = {
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if client_secret:
        payload["client_secret"] = client_secret
    return _post_token(GOOGLE_TOKEN_ENDPOINT, payload)


def refresh_access_token(
    *, client_id: str, client_secret: str | None, refresh_token: str
) -> dict[str, Any]:
    """Use a refresh token to obtain a new access token.

    Raises :class:`OAuthRefreshError` when Google rejects the refresh token
    (e.g. revoked, expired, or invalidated).
    """
    payload: dict[str, str] = {
        "client_id": client_id,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    if client_secret:
        payload["client_secret"] = client_secret
    return _post_token(GOOGLE_TOKEN_ENDPOINT, payload)


def fetch_profile(access_token: str) -> dict[str, Any]:
    """Fetch the Google user profile using the access token."""
    request = Request(
        GOOGLE_USERINFO_ENDPOINT,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed Google endpoint
            return _decode_json(response.read())
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise OAuthError(
                "Google rejected the access token.  Your session may have expired.  "
                "Run 'agent47 login' to sign in again."
            ) from exc
        raise OAuthError(
            "Google could not return your profile.  Check your network connection."
        ) from exc
    except OSError as exc:
        raise OAuthError(
            "Could not retrieve your Google profile.  Check your network connection."
        ) from exc


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _post_token(url: str, fields: dict[str, str]) -> dict[str, Any]:
    """POST to the Google token endpoint and decode the JSON response.

    Distinguishes between network errors, HTTP errors with specific OAuth
    semantics, and generic failures so callers can react appropriately.
    """
    request = Request(
        url,
        data=urlencode(fields).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed Google endpoint
            return _decode_json(response.read())
    except HTTPError as exc:
        # Read the error body for machine-readable OAuth error codes.
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            body = {}

        error_code = body.get("error", "")
        description = body.get("error_description", "")

        # Refresh token rejected — caller should clear the session.
        if error_code == "invalid_grant":
            raise OAuthRefreshError(
                "Your Google sign-in session has expired or been revoked.  "
                "Run 'agent47 login' to sign in again."
            ) from exc

        if exc.code in {400, 401, 403}:
            # Build a safe message from the description (Google descriptions
            # never contain user secrets, only error classifications).
            safe_detail = f"  ({description})" if description else ""
            raise OAuthError(
                f"Google rejected the authentication request.{safe_detail}  "
                "Please try again."
            ) from exc

        raise OAuthError(
            "Google could not complete authentication.  Check your network and try again."
        ) from exc
    except OSError as exc:
        raise OAuthError(
            "Google could not complete authentication.  Check your network and try again."
        ) from exc


def _decode_json(payload: bytes) -> dict[str, Any]:
    """Decode and validate a JSON response body from Google."""
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OAuthError("Google returned an invalid authentication response.") from exc
    if not isinstance(value, dict):
        raise OAuthError("Google returned an invalid authentication response.")
    error = value.get("error")
    if error:
        description = value.get("error_description", "")
        if error == "invalid_grant":
            raise OAuthRefreshError(
                "Your Google sign-in session has expired or been revoked.  "
                "Run 'agent47 login' to sign in again."
            )
        safe_detail = f"  ({description})" if description else ""
        raise OAuthError(f"Google rejected authentication.{safe_detail}  Please try again.")
    return value
