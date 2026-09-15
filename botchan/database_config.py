from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import asyncpg
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from botchan.config import GuildConfig, GuildSpec

log = logging.getLogger("botchan.config")

CONFIG_CHANGED_CHANNEL = "botchan_config_changed"
MAX_RECONNECT_DELAY_SECONDS = 30
# Query parameters consumed by SQLAlchemy's asyncpg dialect. In a DSN, asyncpg
# would forward them to PostgreSQL as server settings and the connection fails.
SQLALCHEMY_ONLY_QUERY_PARAMS = frozenset(
    {
        "async_creator_fn",
        "async_fallback",
        "prepared_statement_cache_size",
        "prepared_statement_name_func",
    }
)


@dataclass(frozen=True)
class VersionedGuildConfig:
    spec: GuildSpec
    revision: int


@dataclass(frozen=True)
class ConfigSnapshot:
    guilds: dict[int, VersionedGuildConfig]
    invalid_guild_ids: set[int]


@dataclass(frozen=True)
class GuildConfigChange:
    guild_id: int
    config: VersionedGuildConfig | None
    invalid: bool = False


SnapshotHandler = Callable[[ConfigSnapshot], Awaitable[None]]
ChangeHandler = Callable[[GuildConfigChange], Awaitable[None]]


def asyncpg_dsn(database_url: str) -> str:
    url = make_url(database_url)
    if url.drivername not in {"postgresql", "postgresql+asyncpg"}:
        raise RuntimeError("DATABASE_URL must use PostgreSQL")
    query = {
        key: value
        for key, value in url.query.items()
        if key not in SQLALCHEMY_ONLY_QUERY_PARAMS
    }
    # SQLAlchemy passes ?ssl= to asyncpg.connect(ssl=...), but asyncpg's DSN
    # parser only understands the libpq name, sslmode.
    if "ssl" in query:
        query["sslmode"] = query.pop("ssl")
    return url.set(drivername="postgresql", query=query).render_as_string(
        hide_password=False
    )


def parse_config_row(row: Any) -> VersionedGuildConfig:
    raw_guild_id = row["guild_id"]
    if (
        not isinstance(raw_guild_id, str)
        or not raw_guild_id.isdigit()
        or raw_guild_id.startswith("0")
        or len(raw_guild_id) > 20
    ):
        raise ValueError("guild_id must be a Discord snowflake string")
    guild_id = int(raw_guild_id)
    document = row["document"]
    if isinstance(document, str):
        document = json.loads(document)
    if not isinstance(document, dict):
        raise ValueError("document must be an object")
    if str(document.get("guild_id")) != raw_guild_id:
        raise ValueError("document guild_id does not match its row")
    config = GuildConfig.model_validate(
        {
            "guild_id": guild_id,
            "channel_pools": document.get("channel_pools"),
        }
    )
    revision = row["revision"]
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("revision must be a positive integer")
    return VersionedGuildConfig(spec=config.to_spec(), revision=revision)


class PostgresConfigSource:
    def __init__(self, database_url: str) -> None:
        self._dsn = asyncpg_dsn(database_url)
        self._connection: asyncpg.Connection | None = None
        self._notifications: asyncio.Queue[str | None] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._snapshot_handler: SnapshotHandler | None = None
        self._change_handler: ChangeHandler | None = None
        self._closing = False

    async def start(
        self,
        snapshot_handler: SnapshotHandler,
        change_handler: ChangeHandler,
    ) -> None:
        self._snapshot_handler = snapshot_handler
        self._change_handler = change_handler
        connection = await self._connect()
        self._connection = connection
        try:
            await snapshot_handler(await self._load_snapshot(connection))
        except BaseException:
            await self._close_connection()
            raise
        self._task = asyncio.create_task(
            self._monitor(), name="botchan-postgres-config-listener"
        )

    async def close(self) -> None:
        self._closing = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("Configuration listener stopped with an error")
            self._task = None
        await self._close_connection()

    async def _connect(self) -> asyncpg.Connection:
        connection = await asyncpg.connect(self._dsn)
        connection.add_termination_listener(self._on_terminated)
        await connection.add_listener(CONFIG_CHANGED_CHANNEL, self._on_notification)
        log.info("Listening for PostgreSQL configuration changes")
        return connection

    async def _close_connection(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None and not connection.is_closed():
            connection.remove_termination_listener(self._on_terminated)
            await connection.close()

    def _on_notification(
        self,
        _connection: asyncpg.Connection,
        _pid: int,
        _channel: str,
        payload: str,
    ) -> None:
        self._notifications.put_nowait(payload)

    def _on_terminated(self, _connection: asyncpg.Connection) -> None:
        if not self._closing:
            self._notifications.put_nowait(None)

    async def _monitor(self) -> None:
        while not self._closing:
            payload = await self._notifications.get()
            if payload is None:
                await self._reconnect()
                continue
            try:
                guild_id, notified_revision = self._parse_notification(payload)
                connection = self._require_connection()
                change = await self._load_guild(connection, guild_id)
                if (
                    change.config is not None
                    and change.config.revision < notified_revision
                ):
                    raise RuntimeError(
                        f"guild {guild_id} is at revision {change.config.revision}, "
                        f"before notified revision {notified_revision}"
                    )
                await self._require_change_handler()(change)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                log.error("Invalid configuration notification %r: %s", payload, exc)
                await self._reload_snapshot_or_reconnect()
            except Exception:
                log.exception("Failed to refresh configuration after notification")
                await self._reconnect()

    async def _reload_snapshot_or_reconnect(self) -> None:
        try:
            snapshot = await self._load_snapshot(self._require_connection())
            await self._require_snapshot_handler()(snapshot)
        except Exception:
            log.exception("Failed to reload the configuration snapshot")
            await self._reconnect()

    async def _reconnect(self) -> None:
        await self._close_connection()
        delay = 1
        while not self._closing:
            try:
                connection = await self._connect()
                self._connection = connection
                snapshot = await self._load_snapshot(connection)
                await self._require_snapshot_handler()(snapshot)
                log.info("PostgreSQL configuration listener reconnected")
                return
            except Exception:
                await self._close_connection()
                log.exception(
                    "Could not reconnect configuration listener; retrying in %s seconds",
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY_SECONDS)

    async def _load_snapshot(self, connection: asyncpg.Connection) -> ConfigSnapshot:
        rows = await connection.fetch(
            "SELECT guild_id, document, revision FROM guild_configs ORDER BY guild_id"
        )
        guilds: dict[int, VersionedGuildConfig] = {}
        invalid_guild_ids: set[int] = set()
        for row in rows:
            try:
                guild_id = int(row["guild_id"])
                guilds[guild_id] = parse_config_row(row)
            except (ValidationError, TypeError, ValueError, KeyError) as exc:
                log.error("Ignoring invalid configuration row %r: %s", row["guild_id"], exc)
                try:
                    invalid_guild_ids.add(int(row["guild_id"]))
                except (TypeError, ValueError):
                    pass
        return ConfigSnapshot(guilds=guilds, invalid_guild_ids=invalid_guild_ids)

    async def _load_guild(
        self, connection: asyncpg.Connection, guild_id: int
    ) -> GuildConfigChange:
        row = await connection.fetchrow(
            "SELECT guild_id, document, revision FROM guild_configs WHERE guild_id = $1",
            str(guild_id),
        )
        if row is None:
            return GuildConfigChange(guild_id=guild_id, config=None)
        try:
            return GuildConfigChange(guild_id=guild_id, config=parse_config_row(row))
        except (ValidationError, TypeError, ValueError, KeyError) as exc:
            log.error("Ignoring invalid configuration for guild %s: %s", guild_id, exc)
            return GuildConfigChange(guild_id=guild_id, config=None, invalid=True)

    @staticmethod
    def _parse_notification(payload: str) -> tuple[int, int]:
        data = json.loads(payload)
        if not isinstance(data, dict) or set(data) != {"guild_id", "revision"}:
            raise ValueError("payload must contain guild_id and revision")
        raw_guild_id = data["guild_id"]
        revision = data["revision"]
        if (
            not isinstance(raw_guild_id, str)
            or not raw_guild_id.isdigit()
            or raw_guild_id.startswith("0")
            or len(raw_guild_id) > 20
        ):
            raise ValueError("guild_id must be a Discord snowflake string")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError("revision must be a positive integer")
        return int(raw_guild_id), revision

    def _require_connection(self) -> asyncpg.Connection:
        if self._connection is None:
            raise ConnectionError("configuration connection is not available")
        return self._connection

    def _require_snapshot_handler(self) -> SnapshotHandler:
        if self._snapshot_handler is None:
            raise RuntimeError("configuration source has not been started")
        return self._snapshot_handler

    def _require_change_handler(self) -> ChangeHandler:
        if self._change_handler is None:
            raise RuntimeError("configuration source has not been started")
        return self._change_handler
