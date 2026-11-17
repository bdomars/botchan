"""Discord guild facts both the user-facing and admin apps need."""

from __future__ import annotations

import logging
from collections.abc import Collection
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from botchan_api.common import error
from botchan_api.discord_client import DiscordAPIError
from botchan_api.models import BotGuild

MANAGE_GUILD = 1 << 5
ADMINISTRATOR = 1 << 3
MANAGE_CHANNELS = 1 << 4
# Copying permission overwrites from a pool's base channel to the rest of the
# pool is a permission edit, which Discord gates behind Manage Roles.
MANAGE_ROLES = 1 << 28
BOT_PERMISSIONS = MANAGE_CHANNELS | MANAGE_ROLES
# Discord channel types.
VOICE_CHANNEL = 2
CATEGORY_CHANNEL = 4
log = logging.getLogger("botchan.api")


def valid_snowflake(value: str) -> bool:
    return value.isdigit() and value[0] != "0" and len(value) <= 20


def has_bot_permissions(granted: int) -> bool:
    """Whether a guild grants the bot everything it needs to manage pools."""
    if granted & ADMINISTRATOR:
        return True
    return granted & BOT_PERMISSIONS == BOT_PERMISSIONS


def icon_url(guild_id: str, icon: str | None) -> str | None:
    if not icon:
        return None
    return f"https://cdn.discordapp.com/icons/{guild_id}/{icon}.png?size=64"


def install_url(client_id: str, guild_id: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "scope": "bot",
            "permissions": str(BOT_PERMISSIONS),
            "guild_id": guild_id,
            "disable_guild_select": "true",
        }
    )
    return f"https://discord.com/oauth2/authorize?{query}"


def voice_channel_list(channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A guild's voice channels in the order Discord's sidebar shows them."""
    categories = {
        str(channel["id"]): channel
        for channel in channels
        if channel.get("type") == CATEGORY_CHANNEL
    }

    def parent(channel: dict[str, Any]) -> dict[str, Any] | None:
        return categories.get(str(channel.get("parent_id")))

    def sort_key(channel: dict[str, Any]) -> tuple[int, int, int, int]:
        category = parent(channel)
        # Channels outside any category sit above every category.
        return (
            -1 if category is None else int(category.get("position", 0)),
            0 if category is None else int(category["id"]),
            int(channel.get("position", 0)),
            int(channel["id"]),
        )

    voice = sorted(
        (channel for channel in channels if channel.get("type") == VOICE_CHANNEL),
        key=sort_key,
    )
    result = []
    for channel in voice:
        category = parent(channel)
        result.append(
            {
                "id": str(channel["id"]),
                "name": channel["name"],
                "category": None if category is None else category["name"],
            }
        )
    return result


async def guild_voice_channels(discord: Any, guild_id: str) -> list[dict[str, Any]]:
    try:
        return voice_channel_list(await discord.guild_channels(guild_id))
    except DiscordAPIError as exc:
        log.exception("Loading channels for guild %s failed", guild_id)
        raise error(
            503, "DISCORD_UNAVAILABLE", "The server's channels could not be loaded."
        ) from exc


async def installed_guilds(
    db: AsyncSession, guild_ids: Collection[str] | None = None
) -> dict[str, BotGuild]:
    """The guilds the bot is in, as it last recorded them, by guild ID.

    The bot keeps this table current from the gateway, so it needs no Discord
    request. Pass ``guild_ids`` to look up only those.
    """
    query = select(BotGuild).where(BotGuild.present)
    if guild_ids is not None:
        query = query.where(BotGuild.guild_id.in_(list(guild_ids)))
    return {guild.guild_id: guild for guild in (await db.scalars(query)).all()}


async def is_installed(db: AsyncSession, guild_id: str) -> bool:
    return guild_id in await installed_guilds(db, [guild_id])
