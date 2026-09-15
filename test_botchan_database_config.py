import asyncio
import json
import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import asyncpg
from pydantic import ValidationError

from botchan.database_config import (
    ConfigSnapshot,
    GuildConfigChange,
    PostgresConfigSource,
    asyncpg_dsn,
    parse_config_row,
)

GUILD_ID = "123456789012345678"
GUILD_ROW = {
    "guild_id": GUILD_ID,
    "revision": 1,
    "document": {
        "guild_id": GUILD_ID,
        "channel_pools": [
            {
                "base_name": "Other Games",
                "min_channels": 2,
                "max_channels": 8,
                "idle_seconds": 300,
            }
        ],
    },
}


class UnexpectedError(Exception):
    pass


class FakeConnection:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows
        self.closed = False

    async def fetch(self, _query: str) -> list[dict[str, Any]]:
        return list(self.rows.values())

    async def fetchrow(self, _query: str, guild_id: str) -> dict[str, Any] | None:
        return self.rows.get(guild_id)

    def is_closed(self) -> bool:
        return self.closed

    def remove_termination_listener(self, _listener: object) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class DatabaseConfigTests(unittest.TestCase):
    def test_normalizes_sqlalchemy_asyncpg_url(self) -> None:
        self.assertEqual(
            asyncpg_dsn("postgresql+asyncpg://user:secret@db.example/botchan"),
            "postgresql://user:secret@db.example/botchan",
        )

    def test_translates_sqlalchemy_ssl_param_to_sslmode(self) -> None:
        self.assertEqual(
            asyncpg_dsn(
                "postgresql+asyncpg://user:secret@db.example/botchan"
                "?ssl=require&application_name=botchan"
            ),
            "postgresql://user:secret@db.example/botchan"
            "?application_name=botchan&sslmode=require",
        )

    def test_drops_sqlalchemy_only_params(self) -> None:
        self.assertEqual(
            asyncpg_dsn(
                "postgresql+asyncpg://user:secret@db.example/botchan"
                "?prepared_statement_cache_size=0"
            ),
            "postgresql://user:secret@db.example/botchan",
        )

    def test_rejects_non_postgres_url(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "PostgreSQL"):
            asyncpg_dsn("sqlite:///botchan.db")

    def test_parses_database_document(self) -> None:
        document = {
            "guild_id": "123456789012345678",
            "channel_pools": [
                {
                    "base_name": "Other Games",
                    "min_channels": 2,
                    "max_channels": 8,
                    "idle_seconds": 300,
                }
            ],
        }
        config = parse_config_row(
            {
                "guild_id": "123456789012345678",
                "revision": 4,
                "document": json.dumps(document),
            }
        )

        self.assertEqual(config.spec.guild_id, 123456789012345678)
        self.assertEqual(config.spec.channel_pools[0].max_channels, 8)
        self.assertEqual(config.revision, 4)

    def test_rejects_invalid_database_document(self) -> None:
        with self.assertRaises(ValidationError):
            parse_config_row(
                {
                    "guild_id": "123",
                    "revision": 1,
                    "document": {"guild_id": "123", "channel_pools": []},
                }
            )

    def test_parses_notification_contract(self) -> None:
        self.assertEqual(
            PostgresConfigSource._parse_notification(
                '{"guild_id":"123456789012345678","revision":7}'
            ),
            (123456789012345678, 7),
        )

    def test_rejects_malformed_notification(self) -> None:
        with self.assertRaises(ValueError):
            PostgresConfigSource._parse_notification(
                '{"guild_id":"123","revision":false}'
            )


class ListenerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def make_source(
        self,
        snapshot_handler: Any = None,
        change_handler: Any = None,
    ) -> PostgresConfigSource:
        source = PostgresConfigSource("postgresql://db.example/botchan")
        source._connection = cast(Any, FakeConnection({GUILD_ID: GUILD_ROW}))
        source._snapshot_handler = snapshot_handler or AsyncMock()
        source._change_handler = change_handler or AsyncMock()
        return source

    async def test_listener_survives_unexpected_change_handler_error(self) -> None:
        changes: list[GuildConfigChange] = []
        both_handled = asyncio.Event()

        async def on_change(change: GuildConfigChange) -> None:
            changes.append(change)
            if len(changes) == 2:
                both_handled.set()
            if len(changes) == 1:
                raise UnexpectedError("Discord request failed")

        snapshot_handler = AsyncMock()
        source = self.make_source(snapshot_handler, on_change)
        payload = json.dumps({"guild_id": GUILD_ID, "revision": 1})
        with (
            patch.object(
                source,
                "_connect",
                AsyncMock(side_effect=lambda: FakeConnection({GUILD_ID: GUILD_ROW})),
            ),
            self.assertLogs("botchan.config", "ERROR"),
        ):
            source._task = asyncio.create_task(source._monitor())
            source._notifications.put_nowait(payload)
            source._notifications.put_nowait(payload)
            await asyncio.wait_for(both_handled.wait(), timeout=1)

        snapshot_handler.assert_awaited_once()
        self.assertFalse(source._task.done())
        await source.close()

    async def test_listener_reconnects_after_non_postgres_error(self) -> None:
        source = self.make_source()
        failing = FakeConnection({})
        failing.fetchrow = AsyncMock(side_effect=asyncpg.InterfaceError("closed"))
        source._connection = cast(Any, failing)
        reconnected = FakeConnection({GUILD_ID: GUILD_ROW})
        reloaded = asyncio.Event()

        async def on_snapshot(_snapshot: ConfigSnapshot) -> None:
            reloaded.set()

        source._snapshot_handler = on_snapshot
        with (
            patch.object(source, "_connect", AsyncMock(return_value=reconnected)),
            self.assertLogs("botchan.config", "ERROR"),
        ):
            source._task = asyncio.create_task(source._monitor())
            source._notifications.put_nowait(
                json.dumps({"guild_id": GUILD_ID, "revision": 1})
            )
            await asyncio.wait_for(reloaded.wait(), timeout=1)

        self.assertTrue(failing.closed)
        self.assertIs(source._connection, reconnected)
        self.assertFalse(source._task.done())
        await source.close()

    async def test_reconnect_retries_after_unexpected_snapshot_error(self) -> None:
        snapshot_handler = AsyncMock(side_effect=[UnexpectedError("boom"), None])
        source = self.make_source(snapshot_handler)
        with (
            patch.object(
                source,
                "_connect",
                AsyncMock(side_effect=lambda: FakeConnection({GUILD_ID: GUILD_ROW})),
            ),
            patch("botchan.database_config.asyncio.sleep", AsyncMock()) as sleep,
            self.assertLogs("botchan.config", "ERROR"),
        ):
            await source._reconnect()

        self.assertEqual(snapshot_handler.await_count, 2)
        sleep.assert_awaited_once_with(1)
        self.assertIsNotNone(source._connection)

    async def test_close_after_listener_failure_still_closes_connection(self) -> None:
        source = self.make_source()
        connection = source._connection
        assert isinstance(connection, FakeConnection)

        async def crash() -> None:
            raise UnexpectedError("listener crashed")

        source._task = asyncio.create_task(crash())
        await asyncio.wait([source._task])
        with self.assertLogs("botchan.config", "ERROR"):
            await source.close()

        self.assertTrue(connection.closed)
        self.assertIsNone(source._task)


if __name__ == "__main__":
    unittest.main()
