"""Keep the bot_guilds table in step with the servers the bot is in.

The API used to page through Discord's guild list with the bot token every
time its short cache ran out, a request per 200 servers. The bot already
learns about every server it joins, leaves or loses permissions in over the
gateway, so it writes that down here and the API reads the table instead.

The table is a view of the gateway, not a source of truth: it is rewritten in
full periodically, so a write that fails or an event the bot never received
is corrected by the next full sync.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

import asyncpg

from botchan.database_config import asyncpg_dsn

log = logging.getLogger("botchan.guilds")


class RegistryGuild(Protocol):
    """The parts of a discord.Guild the registry reads."""

    @property
    def id(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def unavailable(self) -> bool: ...

    @property
    def member_count(self) -> int | None: ...

    @property
    def icon(self) -> object | None: ...

    @property
    def me(self) -> object | None: ...


@dataclass(frozen=True)
class GuildRow:
    guild_id: str
    name: str
    icon: str | None
    permissions: int
    member_count: int | None


def guild_row(guild: RegistryGuild) -> GuildRow | None:
    """The row for a guild, or None while Discord hasn't told us enough about it.

    An unavailable guild (a Discord outage) has no name or member yet. It is
    still a guild the bot is in, so a full sync keeps its row as it was.
    """
    me = guild.me
    if guild.unavailable or me is None:
        return None
    # discord.Asset.key is the icon hash the CDN URL is built from.
    icon = getattr(guild.icon, "key", None)
    # Discord's own computation of the bot's base permissions, owner and
    # Administrator included: the same value the REST guild list reports.
    permissions = getattr(getattr(me, "guild_permissions", None), "value", 0)
    return GuildRow(
        guild_id=str(guild.id),
        name=guild.name,
        icon=icon if isinstance(icon, str) else None,
        permissions=int(permissions),
        member_count=guild.member_count,
    )


UPSERT = """
INSERT INTO bot_guilds
    (guild_id, name, icon, permissions, member_count, present, left_at, updated_at)
VALUES ($1, $2, $3, $4, $5, true, NULL, now())
ON CONFLICT (guild_id) DO UPDATE SET
    name = EXCLUDED.name,
    icon = EXCLUDED.icon,
    permissions = EXCLUDED.permissions,
    member_count = EXCLUDED.member_count,
    present = true,
    left_at = NULL,
    updated_at = now()
"""
# An unavailable guild keeps the row it had, but it has not been left.
MARK_PRESENT = """
UPDATE bot_guilds SET present = true, left_at = NULL, updated_at = now()
WHERE guild_id = ANY($1::text[]) AND NOT present
"""
MARK_LEFT = """
UPDATE bot_guilds SET present = false, left_at = now(), updated_at = now()
WHERE present AND guild_id = $1
"""
MARK_ALL_OTHERS_LEFT = """
UPDATE bot_guilds SET present = false, left_at = now(), updated_at = now()
WHERE present AND NOT (guild_id = ANY($1::text[]))
"""


def row_args(row: GuildRow) -> tuple[object, ...]:
    return (row.guild_id, row.name, row.icon, row.permissions, row.member_count)


class GuildRegistry:
    def __init__(self, database_url: str) -> None:
        self._dsn = asyncpg_dsn(database_url)
        self._pool: asyncpg.Pool | None = None
        # Each write reads the guild's state once it holds the lock, so writes
        # land in the order the gateway changed things. Without it, a full
        # sync that read the guild list before a leave could finish after the
        # leave was written and mark the guild present again.
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=2)

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("The guild registry has not been started")
        return self._pool

    async def sync(self, guilds: Callable[[], Iterable[RegistryGuild]]) -> int:
        """Rewrite the table from the bot's current guilds. Returns how many."""
        async with self._lock:
            current = list(guilds())
            ids = [str(guild.id) for guild in current]
            rows = [row for row in map(guild_row, current) if row is not None]
            async with self._require_pool().acquire() as connection, connection.transaction():
                if rows:
                    await connection.executemany(UPSERT, [row_args(row) for row in rows])
                await connection.execute(MARK_PRESENT, ids)
                await connection.execute(MARK_ALL_OTHERS_LEFT, ids)
            return len(ids)

    async def update(self, guild: RegistryGuild) -> None:
        async with self._lock:
            row = guild_row(guild)
            if row is None:
                return
            await self._require_pool().execute(UPSERT, *row_args(row))

    async def remove(self, guild_id: int) -> None:
        async with self._lock:
            await self._require_pool().execute(MARK_LEFT, str(guild_id))
