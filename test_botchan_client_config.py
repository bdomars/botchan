import asyncio
import signal
import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from discord.ext import commands

from botchan.client import BotChan
from botchan.config import ChannelPoolSpec, GuildSpec
from botchan.database_config import (
    ConfigSnapshot,
    GuildConfigChange,
    VersionedGuildConfig,
)


def versioned(guild_id: int, revision: int, *, maximum: int = 10):
    return VersionedGuildConfig(
        spec=GuildSpec(
            guild_id=guild_id,
            channel_pools=[
                ChannelPoolSpec(
                    base_name="Other Games",
                    min_channels=3,
                    max_channels=maximum,
                    idle_seconds=600,
                )
            ],
        ),
        revision=revision,
    )


class BotConfigRefreshTests(unittest.IsolatedAsyncioTestCase):
    def make_bot(self) -> BotChan:
        return BotChan(cast(Any, object()))

    async def test_snapshot_adds_and_removes_guilds(self) -> None:
        bot = self.make_bot()
        await bot._apply_config_snapshot(
            ConfigSnapshot(guilds={111: versioned(111, 1)}, invalid_guild_ids=set())
        )
        self.assertEqual(set(bot.managed_guilds), {111})

        await bot._apply_config_snapshot(
            ConfigSnapshot(guilds={222: versioned(222, 1)}, invalid_guild_ids=set())
        )
        self.assertEqual(set(bot.managed_guilds), {222})

    async def test_update_preserves_pool_timer_and_ignores_stale_revision(self) -> None:
        bot = self.make_bot()
        await bot._apply_config_snapshot(
            ConfigSnapshot(guilds={111: versioned(111, 2)}, invalid_guild_ids=set())
        )
        timers = bot.managed_guilds[111].pools[0].empty_since_by_channel_id
        timers[1234] = 50.0

        await bot._apply_config_change(
            GuildConfigChange(guild_id=111, config=versioned(111, 3, maximum=5))
        )
        self.assertIs(
            bot.managed_guilds[111].pools[0].empty_since_by_channel_id, timers
        )
        self.assertEqual(bot.managed_guilds[111].pools[0].spec.max_channels, 5)

        await bot._apply_config_change(
            GuildConfigChange(guild_id=111, config=versioned(111, 2, maximum=9))
        )
        self.assertEqual(bot.managed_guilds[111].pools[0].spec.max_channels, 5)

    async def test_invalid_refresh_retains_last_known_good_config(self) -> None:
        bot = self.make_bot()
        await bot._apply_config_snapshot(
            ConfigSnapshot(guilds={111: versioned(111, 1)}, invalid_guild_ids=set())
        )

        await bot._apply_config_snapshot(
            ConfigSnapshot(guilds={}, invalid_guild_ids={111})
        )
        await bot._apply_config_change(
            GuildConfigChange(guild_id=111, config=None, invalid=True)
        )

        self.assertEqual(set(bot.managed_guilds), {111})

    async def test_deleted_config_stops_management_without_reconciliation(self) -> None:
        bot = self.make_bot()
        await bot._apply_config_snapshot(
            ConfigSnapshot(guilds={111: versioned(111, 1)}, invalid_guild_ids=set())
        )

        await bot._apply_config_change(GuildConfigChange(guild_id=111, config=None))

        self.assertEqual(bot.managed_guilds, {})

    async def test_close_closes_discord_client_when_config_source_fails(self) -> None:
        config_source = AsyncMock()
        config_source.close.side_effect = RuntimeError("database close failed")
        bot = BotChan(config_source)

        with (
            patch.object(commands.Bot, "close", AsyncMock()) as discord_close,
            self.assertRaises(RuntimeError),
        ):
            await bot.close()

        discord_close.assert_awaited_once()

    async def test_setup_hook_registers_sigterm_handler(self) -> None:
        bot = BotChan(AsyncMock())

        with (
            patch.object(bot.cleanup_empty_channels, "start"),
            patch.object(
                asyncio.get_running_loop(), "add_signal_handler"
            ) as add_signal_handler,
        ):
            await bot.setup_hook()

        add_signal_handler.assert_called_once_with(signal.SIGTERM, bot._on_sigterm)

    async def test_sigterm_closes_bot_once(self) -> None:
        config_source = AsyncMock()
        bot = BotChan(config_source)

        with patch.object(commands.Bot, "close", AsyncMock()) as discord_close:
            bot._on_sigterm()
            bot._on_sigterm()
            assert bot._shutdown_task is not None
            await bot._shutdown_task

        config_source.close.assert_awaited_once()
        discord_close.assert_awaited_once()


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


class GuildLockTests(unittest.IsolatedAsyncioTestCase):
    async def make_bot(self, *guild_ids: int) -> BotChan:
        bot = BotChan(cast(Any, object()))
        await bot._apply_config_snapshot(
            ConfigSnapshot(
                guilds={guild_id: versioned(guild_id, 1) for guild_id in guild_ids},
                invalid_guild_ids=set(),
            )
        )
        return bot

    async def test_passes_for_the_same_guild_run_one_at_a_time(self) -> None:
        bot = await self.make_bot(111)
        release = asyncio.Event()
        entered: list[int] = []

        async def blocking_pass(managed_guild: Any) -> None:
            entered.append(managed_guild.guild_id)
            await release.wait()

        with patch.object(bot, "_reconcile_guild_unlocked", new=blocking_pass):
            first = asyncio.create_task(bot.reconcile_guild_id(111))
            second = asyncio.create_task(bot.reconcile_guild_id(111))
            await settle()
            self.assertEqual(entered, [111])

            release.set()
            await asyncio.gather(first, second)

        self.assertEqual(entered, [111, 111])

    async def test_slow_guild_does_not_block_other_guilds(self) -> None:
        bot = await self.make_bot(111, 222)
        release = asyncio.Event()
        finished: list[int] = []

        async def pass_blocking_111(managed_guild: Any) -> None:
            if managed_guild.guild_id == 111:
                await release.wait()
            finished.append(managed_guild.guild_id)

        with patch.object(bot, "_reconcile_guild_unlocked", new=pass_blocking_111):
            blocked = asyncio.create_task(bot.reconcile_guild_id(111))
            await settle()
            await asyncio.wait_for(bot.reconcile_guild_id(222), timeout=1)
            self.assertEqual(finished, [222])
            self.assertFalse(blocked.done())

            release.set()
            await blocked

        self.assertEqual(finished, [222, 111])

    async def test_waiting_pass_skips_guild_whose_config_was_deleted(self) -> None:
        bot = await self.make_bot(111)
        release = asyncio.Event()
        entered: list[int] = []

        async def blocking_pass(managed_guild: Any) -> None:
            entered.append(managed_guild.guild_id)
            await release.wait()

        with patch.object(bot, "_reconcile_guild_unlocked", new=blocking_pass):
            first = asyncio.create_task(bot.reconcile_guild_id(111))
            await settle()
            second = asyncio.create_task(bot.reconcile_guild_id(111))
            await settle()
            await bot._apply_config_change(GuildConfigChange(guild_id=111, config=None))

            release.set()
            await asyncio.gather(first, second)

        self.assertEqual(entered, [111])

    async def test_sweep_tolerates_config_deleted_mid_sweep(self) -> None:
        bot = await self.make_bot(111, 222)
        cleaned: list[int] = []

        async def cleanup(managed_guild: Any) -> None:
            cleaned.append(managed_guild.guild_id)
            if managed_guild.guild_id == 111:
                await bot._apply_config_change(
                    GuildConfigChange(guild_id=222, config=None)
                )

        with patch.object(bot, "_cleanup_guild_unlocked", new=cleanup):
            await bot.cleanup_empty_channels()

        self.assertEqual(cleaned, [111])

    async def test_voice_events_in_unmanaged_guilds_create_no_lock(self) -> None:
        bot = await self.make_bot(111)

        await bot.reconcile_guild_id(999)

        self.assertNotIn(999, bot._guild_locks)


if __name__ == "__main__":
    unittest.main()
