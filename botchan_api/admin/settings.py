import os
from dataclasses import dataclass

from botchan_api.settings import Settings, _required


@dataclass(frozen=True)
class AdminSettings:
    """The admin app's own URLs on top of the secrets it shares with the user app.

    The admin app is served on its own host, so it needs its own redirect URI
    registered in the Discord Developer Portal.
    """

    base: Settings
    public_base_url: str
    discord_redirect_uri: str
    session_hours: int = 12

    @classmethod
    def from_env(cls) -> AdminSettings:
        return cls(
            base=Settings.from_env(),
            public_base_url=_required("ADMIN_PUBLIC_BASE_URL").rstrip("/"),
            discord_redirect_uri=_required("ADMIN_DISCORD_REDIRECT_URI"),
            session_hours=int(os.environ.get("ADMIN_SESSION_HOURS", "12")),
        )
