"""RFC 7636 Proof Key for Code Exchange (PKCE) helpers.

Only S256 is supported.  There is deliberately no fallback to the ``plain``
challenge method because it provides no protection against authorization code
interception.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

CODE_CHALLENGE_METHOD = "S256"
"""The only supported challenge method.  Referenced by the authorization URL
builder and verified in tests to prevent accidental downgrade."""


def generate_code_verifier() -> str:
    """Return a high-entropy RFC 7636 code verifier (≥43 URL-safe chars, no padding).

    Uses :func:`secrets.token_urlsafe` which draws from the OS CSPRNG.
    A 64-byte random token produces an 86-character base64url string — well
    within the RFC 7636 range of 43–128 characters.
    """
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    """Derive the S256 code challenge from *verifier*.

    ``BASE64URL(SHA256(ASCII(code_verifier)))`` per RFC 7636 §4.2, with
    padding stripped.
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_state() -> str:
    """Return a cryptographically random OAuth 2.0 state parameter.

    The 32-byte token produces a 43-character base64url string that is
    validated with :func:`secrets.compare_digest` on callback to prevent
    CSRF and replay attacks.
    """
    return secrets.token_urlsafe(32)
