from __future__ import annotations

import os
from dataclasses import dataclass


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    discord_client_id: str
    discord_client_secret: str
    discord_bot_token: str
    discord_redirect_uri: str
    public_base_url: str
    session_secret: str
    token_encryption_key: str
    secure_cookies: bool = True
    session_days: int = 7

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=_required("DATABASE_URL"),
            discord_client_id=_required("DISCORD_CLIENT_ID"),
            discord_client_secret=_required("DISCORD_CLIENT_SECRET"),
            discord_bot_token=_required("DISCORD_TOKEN"),
            discord_redirect_uri=_required("DISCORD_REDIRECT_URI"),
            public_base_url=_required("PUBLIC_BASE_URL").rstrip("/"),
            session_secret=_required("SESSION_SECRET"),
            token_encryption_key=_required("TOKEN_ENCRYPTION_KEY"),
            secure_cookies=os.environ.get("SECURE_COOKIES", "true").lower()
            not in {"0", "false", "no"},
        )

