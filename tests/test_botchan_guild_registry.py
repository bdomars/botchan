import os
import unittest
from types import SimpleNamespace

from botchan.guild_registry import guild_row

TEST_DATABASE_URL = os.environ.get("BOTCHAN_TEST_DATABASE_URL")


def guild(**overrides):
    values = {
        "id": 111,
        "name": "Test Guild",
        "unavailable": False,
        "member_count": 42,
        "icon": SimpleNamespace(key="abc123"),
        "me": SimpleNamespace(guild_permissions=SimpleNamespace(value=(1 << 4) | (1 << 28))),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class GuildRowTests(unittest.TestCase):
    def test_maps_a_guild_to_its_row(self) -> None:
        row = guild_row(guild())

        assert row is not None
        self.assertEqual(row.guild_id, "111")
        self.assertEqual(row.name, "Test Guild")
        self.assertEqual(row.icon, "abc123")
        self.assertEqual(row.permissions, (1 << 4) | (1 << 28))
        self.assertEqual(row.member_count, 42)

    def test_a_guild_without_an_icon(self) -> None:
        row = guild_row(guild(icon=None))

        assert row is not None
        self.assertIsNone(row.icon)

    def test_an_unavailable_guild_has_no_row(self) -> None:
        # Discord sends no name or member for it during an outage.
        self.assertIsNone(guild_row(guild(unavailable=True)))
        self.assertIsNone(guild_row(guild(me=None)))


@unittest.skipUnless(TEST_DATABASE_URL, "BOTCHAN_TEST_DATABASE_URL is not set")
class GuildRegistryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        import asyncpg

        from botchan.database_config import asyncpg_dsn
        from botchan.guild_registry import GuildRegistry

        assert TEST_DATABASE_URL is not None
        self.connection = await asyncpg.connect(asyncpg_dsn(TEST_DATABASE_URL))
        await self.connection.execute("DELETE FROM bot_guilds")
        self.registry = GuildRegistry(TEST_DATABASE_URL)
        await self.registry.start()

    async def asyncTearDown(self) -> None:
        await self.registry.close()
        await self.connection.close()

    async def rows(self) -> dict[str, tuple]:
        return {
            row["guild_id"]: (row["name"], row["present"], row["left_at"] is not None)
            for row in await self.connection.fetch(
                "SELECT guild_id, name, present, left_at FROM bot_guilds"
            )
        }

    async def test_sync_records_current_guilds_and_marks_the_rest_left(self) -> None:
        count = await self.registry.sync(lambda: [guild(id=111), guild(id=222, name="Other")])
        self.assertEqual(count, 2)

        await self.registry.sync(lambda: [guild(id=222, name="Renamed")])

        self.assertEqual(
            await self.rows(),
            {"111": ("Test Guild", False, True), "222": ("Renamed", True, False)},
        )

    async def test_an_unavailable_guild_keeps_its_row_and_stays_present(self) -> None:
        await self.registry.sync(lambda: [guild(id=111)])

        await self.registry.sync(lambda: [guild(id=111, name=None, unavailable=True)])

        self.assertEqual(await self.rows(), {"111": ("Test Guild", True, False)})

    async def test_events_join_leave_and_rejoin(self) -> None:
        await self.registry.update(guild(id=111))
        await self.registry.remove(111)
        self.assertEqual(await self.rows(), {"111": ("Test Guild", False, True)})

        await self.registry.update(guild(id=111, name="Back Again"))

        self.assertEqual(await self.rows(), {"111": ("Back Again", True, False)})
        permissions = await self.connection.fetchval(
            "SELECT permissions FROM bot_guilds WHERE guild_id = '111'"
        )
        self.assertEqual(permissions, (1 << 4) | (1 << 28))


if __name__ == "__main__":
    unittest.main()
