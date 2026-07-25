from __future__ import annotations

import os
from dataclasses import dataclass

from .oauth_constants import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET

class OAuthConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GoogleOAuthConfig:
    client_id: str
    client_secret: str | None
    timeout_seconds: float = 180.0

    @classmethod
    def from_environment(cls) -> "GoogleOAuthConfig":
        client_id = (
            os.environ.get("GOOGLE_CLIENT_ID", "").strip()
            or GOOGLE_CLIENT_ID
        )
        if not client_id:
            raise OAuthConfigurationError(
                "Developer Error: No Google OAuth Client ID provided in environment or constants."
            )
        raw_timeout = os.environ.get("GOOGLE_OAUTH_TIMEOUT_SECONDS", "180")
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise OAuthConfigurationError("GOOGLE_OAUTH_TIMEOUT_SECONDS must be a number.") from exc
        if not 1 <= timeout <= 900:
            raise OAuthConfigurationError("GOOGLE_OAUTH_TIMEOUT_SECONDS must be between 1 and 900.")
        
        secret = (
            os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
            or GOOGLE_CLIENT_SECRET
        ) or None
        
        return cls(client_id=client_id, client_secret=secret, timeout_seconds=timeout)
