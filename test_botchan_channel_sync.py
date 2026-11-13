from __future__ import annotations

import itertools
import unittest
from dataclasses import replace
from typing import Any, cast
from unittest.mock import patch

import discord

from botchan.client import BotChan, ManagedGuild, ManagedPool
from botchan.config import ChannelPoolSpec

SPEC = ChannelPoolSpec(
    base_name="Other Games",
    min_channels=3,
    max_channels=10,
    idle_seconds=600,
)


class FakeCategory:
    def __init__(self, id: int) -> None:
        self.id = id


class FakeVoiceChannel:
    """Stands in for discord.VoiceChannel across create, edit and position."""

    _ids = itertools.count(1000)

    def __init__(self, name: str, guild: FakeGuild, **settings: Any) -> None:
        self.id = next(FakeVoiceChannel._ids)
        self.name = name
        self.guild = guild
        self.members: list[object] = []
        self.position = 0
        self.category = settings.get("category")
        self.overwrites = settings.get("overwrites", {})
        self.bitrate = settings.get("bitrate", 64000)
        self.user_limit = settings.get("user_limit", 0)
        self.rtc_region = settings.get("rtc_region")
        self.video_quality_mode = settings.get("video_quality_mode", 1)
        # When set, every edit snaps bitrate back, standing in for a value
        # Discord clamps or silently refuses.
        self.clamp_bitrate_to: int | None = None
        self.fail_next_edit: Exception | None = None
        self.edits = 0

    @property
    def category_id(self) -> int | None:
        return None if self.category is None else self.category.id

    async def edit(self, **settings: Any) -> FakeVoiceChannel:
        if self.fail_next_edit is not None:
            error, self.fail_next_edit = self.fail_next_edit, None
            raise error
        self.edits += 1
        settings.pop("reason", None)
        for key, value in settings.items():
            setattr(self, key, value)
        if self.clamp_bitrate_to is not None:
            self.bitrate = self.clamp_bitrate_to
        return self


class FakeGuild:
    def __init__(self) -> None:
        self.id = 42
        self.channels: list[FakeVoiceChannel] = []
        self.creates: list[tuple[str, dict[str, Any], str | None]] = []

    @property
    def voice_channels(self) -> list[FakeVoiceChannel]:
        return list(self.channels)

    def get_channel(self, channel_id: int) -> FakeVoiceChannel | None:
        return next((c for c in self.channels if c.id == channel_id), None)

    def add(self, name: str, **settings: Any) -> FakeVoiceChannel:
        channel = FakeVoiceChannel(name, self, **settings)
        self.channels.append(channel)
        return channel

    async def create_voice_channel(
        self, name: str, reason: str | None = None, **settings: Any
    ) -> FakeVoiceChannel:
        self.creates.append((name, dict(settings), reason))
        channel = FakeVoiceChannel(name, self, **settings)
        self.channels.append(channel)
        return channel


class ChannelSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = BotChan(cast(Any, object()))
        self.guild = FakeGuild()
        # An admin creates the base channel in Discord, then picks it.
        self.base = self.guild.add("Other Games")
        self.pool = ManagedPool(spec=replace(SPEC, base_channel_id=self.base.id))
        # client.py narrows on discord.VoiceChannel, so the fakes must pass it.
        patcher = patch("botchan.client.discord.VoiceChannel", FakeVoiceChannel)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def reconcile(self) -> None:
        await self.bot._reconcile_pool(
            cast(Any, self.guild), cast(Any, self.guild.voice_channels), self.pool
        )

    async def seeded_pool(self) -> tuple[FakeVoiceChannel, ...]:
        await self.reconcile()
        return tuple(self.guild.channels)

    async def test_extras_are_created_from_the_base_channel(self) -> None:
        await self.reconcile()

        self.assertEqual(
            [channel.name for channel in self.guild.channels],
            ["Other Games", "Other Games #2", "Other Games #3"],
        )
        _, settings, _ = self.guild.creates[0]
        self.assertIn("bitrate", settings)
        self.assertIn("overwrites", settings)

    async def test_base_channel_settings_reach_the_whole_pool(self) -> None:
        base, second, third = await self.seeded_pool()
        category = FakeCategory(777)
        base.category = category
        base.bitrate = 96000
        base.user_limit = 5
        base.overwrites = {"@everyone": "connect"}

        await self.reconcile()

        for channel in (second, third):
            self.assertIs(channel.category, category)
            self.assertEqual(channel.bitrate, 96000)
            self.assertEqual(channel.user_limit, 5)
            self.assertEqual(channel.overwrites, {"@everyone": "connect"})

    async def test_the_base_channel_is_never_edited(self) -> None:
        base, _, _ = await self.seeded_pool()
        base.bitrate = 96000

        await self.reconcile()

        self.assertEqual(base.edits, 0)

    async def test_a_converged_pool_issues_no_edits(self) -> None:
        base, second, third = await self.seeded_pool()
        base.bitrate = 96000
        await self.reconcile()
        before = (second.edits, third.edits)

        await self.reconcile()
        await self.reconcile()

        self.assertEqual((second.edits, third.edits), before)

    async def test_manual_drift_is_reverted(self) -> None:
        base, _, third = await self.seeded_pool()
        base.bitrate = 96000
        await self.reconcile()
        # An admin edits a pool channel directly after it was already synced.
        third.bitrate = 8000

        await self.reconcile()

        self.assertEqual(third.bitrate, 96000)

    async def test_a_setting_that_will_not_apply_is_attempted_once(self) -> None:
        base, _, third = await self.seeded_pool()
        base.bitrate = 96000
        third.clamp_bitrate_to = 8000
        before = third.edits

        for _ in range(5):
            await self.reconcile()

        self.assertEqual(third.edits - before, 1)

    async def test_a_new_base_edit_retries_a_stuck_channel(self) -> None:
        base, _, third = await self.seeded_pool()
        base.bitrate = 96000
        third.clamp_bitrate_to = 8000
        await self.reconcile()
        third.clamp_bitrate_to = None

        base.bitrate = 128000
        await self.reconcile()

        self.assertEqual(third.bitrate, 128000)

    async def test_channels_outside_the_pool_are_untouched(self) -> None:
        base, _, _ = await self.seeded_pool()
        base.bitrate = 96000
        stray = self.guild.add("General", bitrate=8000)

        await self.reconcile()

        self.assertEqual(stray.edits, 0)
        self.assertEqual(stray.bitrate, 8000)

    async def test_a_namesake_of_the_base_channel_is_not_in_the_pool(self) -> None:
        base, _, _ = await self.seeded_pool()
        base.bitrate = 96000
        namesake = self.guild.add("Other Games", bitrate=8000)
        creates = len(self.guild.creates)

        await self.reconcile()

        # Not a duplicate that blocks the pool, and not synced either.
        self.assertEqual(namesake.edits, 0)
        self.assertEqual(len(self.guild.creates), creates)
        self.assertNotIn(namesake.id, self.pool.empty_since_by_channel_id)

    async def test_sync_state_is_dropped_for_channels_that_leave_the_pool(
        self,
    ) -> None:
        base, _, third = await self.seeded_pool()
        base.bitrate = 96000
        await self.reconcile()
        self.assertIn(third.id, self.pool.last_sync_by_channel_id)

        self.guild.channels.remove(third)
        await self.reconcile()

        self.assertNotIn(third.id, self.pool.last_sync_by_channel_id)

    async def test_a_deleted_base_channel_pauses_the_pool(self) -> None:
        base, second, _ = await self.seeded_pool()
        self.guild.channels.remove(base)
        second.bitrate = 8000
        creates, edits = len(self.guild.creates), second.edits

        with self.assertLogs("botchan", "WARNING") as logs:
            await self.reconcile()
            await self.reconcile()

        self.assertEqual(len(self.guild.creates), creates)
        self.assertEqual(second.edits, edits)
        self.assertIsNotNone(self.pool.paused_reason)
        # Logged when the pool pauses, not on every pass.
        self.assertEqual(len(logs.output), 1)
        self.assertIn("base channel was deleted", logs.output[0])

    async def test_picking_a_new_base_channel_resumes_the_pool(self) -> None:
        base, _, _ = await self.seeded_pool()
        self.guild.channels.remove(base)
        with self.assertLogs("botchan", "WARNING"):
            await self.reconcile()

        replacement = self.guild.add("Other Games")
        self.pool.spec = replace(self.pool.spec, base_channel_id=replacement.id)
        await self.reconcile()

        self.assertIsNone(self.pool.paused_reason)
        self.assertEqual(
            sorted(channel.name for channel in self.guild.channels),
            ["Other Games", "Other Games #2", "Other Games #3"],
        )

    async def test_a_base_name_too_long_for_its_extras_pauses_the_pool(self) -> None:
        # "#10" would push the longest extra past Discord's 100 characters.
        self.base.name = "x" * 97

        with self.assertLogs("botchan", "WARNING") as logs:
            await self.reconcile()

        self.assertEqual(self.guild.creates, [])
        self.assertIn("character limit", logs.output[0])

    async def test_renaming_the_base_channel_renames_the_extras(self) -> None:
        base, second, third = await self.seeded_pool()
        creates = len(self.guild.creates)

        base.name = "Raid Rooms"
        await self.reconcile()
        await self.reconcile()

        self.assertEqual(second.name, "Raid Rooms #2")
        self.assertEqual(third.name, "Raid Rooms #3")
        self.assertEqual(len(self.guild.creates), creates)
        self.assertEqual(self.pool.pending_renames, {})

    async def test_a_rate_limited_rename_is_retried_on_a_later_pass(self) -> None:
        base, second, third = await self.seeded_pool()
        third.fail_next_edit = discord.RateLimited(600.0)
        creates = len(self.guild.creates)

        base.name = "Raid Rooms"
        with self.assertLogs("botchan", "WARNING"):
            await self.reconcile()
        self.assertEqual(second.name, "Raid Rooms #2")
        self.assertEqual(third.name, "Other Games #3")

        # Still waiting on the rename, so nothing else happens yet.
        await self.reconcile()

        self.assertEqual(third.name, "Raid Rooms #3")
        self.assertEqual(len(self.guild.creates), creates)

    async def test_a_pool_saved_by_name_finds_its_base_by_name(self) -> None:
        self.pool = ManagedPool(spec=SPEC)

        await self.reconcile()

        self.assertEqual(
            [channel.name for channel in self.guild.channels],
            ["Other Games", "Other Games #2", "Other Games #3"],
        )

    async def test_a_pool_saved_by_name_without_its_base_pauses(self) -> None:
        self.guild.channels.remove(self.base)
        self.pool = ManagedPool(spec=SPEC)

        with self.assertLogs("botchan", "WARNING"):
            await self.reconcile()

        self.assertEqual(self.guild.channels, [])


class ChannelEventTests(unittest.IsolatedAsyncioTestCase):
    """The listeners that decide when a pool is worth re-examining."""

    def setUp(self) -> None:
        self.bot = BotChan(cast(Any, object()))
        self.guild = FakeGuild()
        self.pool = ManagedPool(spec=SPEC)
        self.bot.managed_guilds = {
            self.guild.id: ManagedGuild(guild_id=self.guild.id, pools=[self.pool])
        }
        self.woken: list[int] = []

        async def record(guild_id: int) -> None:
            self.woken.append(guild_id)

        cast(Any, self.bot).reconcile_guild_id = record
        # client.py narrows on discord.VoiceChannel, so the fakes must pass it.
        patcher = patch("botchan.client.discord.VoiceChannel", FakeVoiceChannel)
        patcher.start()
        self.addCleanup(patcher.stop)

    def channel(self, name: str, **settings: Any) -> FakeVoiceChannel:
        return FakeVoiceChannel(name, self.guild, **settings)

    async def test_renaming_a_channel_out_of_the_pool_wakes_the_bot(self) -> None:
        before = self.channel("Other Games")
        after = self.channel("Silly Games")

        await self.bot.on_guild_channel_update(cast(Any, before), cast(Any, after))

        self.assertEqual(self.woken, [self.guild.id])

    async def test_renaming_a_channel_into_the_pool_wakes_the_bot(self) -> None:
        before = self.channel("Chill Zone")
        after = self.channel("Other Games #4")

        await self.bot.on_guild_channel_update(cast(Any, before), cast(Any, after))

        self.assertEqual(self.woken, [self.guild.id])

    async def test_a_settings_change_on_a_pool_channel_wakes_the_bot(self) -> None:
        before = self.channel("Other Games", bitrate=64000)
        after = self.channel("Other Games", bitrate=96000)

        await self.bot.on_guild_channel_update(cast(Any, before), cast(Any, after))

        self.assertEqual(self.woken, [self.guild.id])

    async def test_an_unrelated_change_is_ignored(self) -> None:
        before = self.channel("General", bitrate=64000)
        after = self.channel("General", bitrate=96000)

        await self.bot.on_guild_channel_update(cast(Any, before), cast(Any, after))

        self.assertEqual(self.woken, [])

    async def test_a_no_op_update_is_ignored(self) -> None:
        before = self.channel("Other Games", bitrate=96000)
        after = self.channel("Other Games", bitrate=96000)

        await self.bot.on_guild_channel_update(cast(Any, before), cast(Any, after))

        self.assertEqual(self.woken, [])

    async def test_deleting_a_pool_channel_wakes_the_bot(self) -> None:
        await self.bot.on_guild_channel_delete(cast(Any, self.channel("Other Games")))

        self.assertEqual(self.woken, [self.guild.id])

    async def test_deleting_an_unrelated_channel_is_ignored(self) -> None:
        await self.bot.on_guild_channel_delete(cast(Any, self.channel("General")))

        self.assertEqual(self.woken, [])

    async def test_renaming_the_base_channel_wakes_the_bot(self) -> None:
        base = self.guild.add("Raid Rooms")
        self.pool.spec = replace(SPEC, base_channel_id=base.id)
        before = self.channel("Raid Rooms")
        before.id = base.id

        await self.bot.on_guild_channel_update(cast(Any, before), cast(Any, base))
        base.name = "Dungeon Rooms"
        renamed = self.channel("Dungeon Rooms")
        renamed.id = base.id
        await self.bot.on_guild_channel_update(cast(Any, before), cast(Any, renamed))

        # The first update changed nothing; the rename does.
        self.assertEqual(self.woken, [self.guild.id])

    async def test_extras_follow_the_base_channels_current_name(self) -> None:
        base = self.guild.add("Raid Rooms")
        self.pool.spec = replace(SPEC, base_channel_id=base.id)

        await self.bot.on_guild_channel_delete(cast(Any, self.channel("Raid Rooms #3")))
        await self.bot.on_guild_channel_delete(cast(Any, self.channel("Other Games #3")))

        self.assertEqual(self.woken, [self.guild.id])

    async def test_deleting_the_base_channel_wakes_the_bot(self) -> None:
        base = self.guild.add("Raid Rooms")
        self.pool.spec = replace(SPEC, base_channel_id=base.id)
        self.guild.channels.remove(base)

        await self.bot.on_guild_channel_delete(cast(Any, base))

        self.assertEqual(self.woken, [self.guild.id])

    async def test_events_for_unmanaged_guilds_are_ignored(self) -> None:
        self.bot.managed_guilds = {}

        await self.bot.on_guild_channel_delete(cast(Any, self.channel("Other Games")))

        self.assertEqual(self.woken, [])


if __name__ == "__main__":
    unittest.main()
