from __future__ import annotations

import base64
import hashlib
import secrets


def generate_code_verifier() -> str:
    """Return a high-entropy RFC 7636 verifier without padding."""
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_state() -> str:
    return secrets.token_urlsafe(32)
