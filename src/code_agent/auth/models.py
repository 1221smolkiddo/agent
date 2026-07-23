from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Account:
    """Non-sensitive identity metadata kept in the local config directory."""

    user_id: str
    name: str
    email: str
    picture_url: str | None
    provider: str
    created_at: str
    last_login_at: str

    @classmethod
    def from_google_profile(
        cls, profile: dict[str, Any], *, created_at: str | None = None
    ) -> "Account":
        user_id = str(profile.get("sub") or "").strip()
        email = str(profile.get("email") or "").strip()
        if not user_id or not email:
            raise ValueError("Google did not return a usable account identity.")
        now = datetime.now(UTC).isoformat()
        return cls(
            user_id=user_id,
            name=str(profile.get("name") or email),
            email=email,
            picture_url=str(profile["picture"]) if profile.get("picture") else None,
            provider="google",
            created_at=created_at or now,
            last_login_at=now,
        )

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Account":
        return cls(
            user_id=str(raw["user_id"]),
            name=str(raw["name"]),
            email=str(raw["email"]),
            picture_url=str(raw["picture_url"]) if raw.get("picture_url") else None,
            provider=str(raw["provider"]),
            created_at=str(raw["created_at"]),
            last_login_at=str(raw["last_login_at"]),
        )
