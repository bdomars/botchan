from __future__ import annotations

from typing import Any

import httpx

from botchan_api.settings import Settings


DISCORD_API = "https://discord.com/api/v10"


class DiscordAPIError(RuntimeError):
    pass


class DiscordClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=15)

    async def _json(self, response: httpx.Response) -> Any:
        if response.is_error:
            raise DiscordAPIError(f"Discord returned HTTP {response.status_code}")
        return response.json()

    async def exchange_code(self, code: str) -> dict[str, Any]:
        response = await self.client.post(
            f"{DISCORD_API}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.settings.discord_redirect_uri,
            },
            auth=(self.settings.discord_client_id, self.settings.discord_client_secret),
        )
        return await self._json(response)

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        response = await self.client.post(
            f"{DISCORD_API}/oauth2/token",
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(self.settings.discord_client_id, self.settings.discord_client_secret),
        )
        return await self._json(response)

    async def revoke(self, token: str) -> None:
        response = await self.client.post(
            f"{DISCORD_API}/oauth2/token/revoke",
            data={"token": token, "token_type_hint": "access_token"},
            auth=(self.settings.discord_client_id, self.settings.discord_client_secret),
        )
        if response.is_error:
            raise DiscordAPIError(f"Discord returned HTTP {response.status_code}")

    async def current_user(self, access_token: str) -> dict[str, Any]:
        response = await self.client.get(
            f"{DISCORD_API}/users/@me", headers={"Authorization": f"Bearer {access_token}"}
        )
        return await self._json(response)

    async def user_guilds(self, access_token: str) -> list[dict[str, Any]]:
        response = await self.client.get(
            f"{DISCORD_API}/users/@me/guilds",
            params={"limit": 200},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        return await self._json(response)

    async def bot_guild_ids(self) -> set[str]:
        result: set[str] = set()
        after: str | None = None
        while True:
            params = {"limit": 200}
            if after is not None:
                params["after"] = after
            response = await self.client.get(
                f"{DISCORD_API}/users/@me/guilds",
                params=params,
                headers={"Authorization": f"Bot {self.settings.discord_bot_token}"},
            )
            page = await self._json(response)
            result.update(str(guild["id"]) for guild in page)
            if len(page) < 200:
                return result
            after = str(page[-1]["id"])
