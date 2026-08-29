from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

import discord
from discord.ext import commands, tasks

from botchan.config import ChannelPoolSpec, RuntimeConfig
from botchan.reconciliation import (
    ReconcilePlan,
    base_channel,
    parse_channel_number,
    plan_reconcile,
    refresh_empty_timers,
)

log = logging.getLogger("botchan")

CLEANUP_INTERVAL_SECONDS = 30


@dataclass
class ManagedPool:
    spec: ChannelPoolSpec
    empty_since_by_channel_id: dict[int, float] = field(default_factory=dict)


@dataclass
class ManagedGuild:
    guild_id: int
    pools: list[ManagedPool]


class BotChan(commands.Bot):
    def __init__(self, config: RuntimeConfig) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True

        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.managed_guilds = {
            guild_id: ManagedGuild(
                guild_id=guild_id,
                pools=[
                    ManagedPool(spec=pool_spec)
                    for pool_spec in guild_spec.channel_pools
                ],
            )
            for guild_id, guild_spec in config.guilds.items()
        }
        self._reconcile_lock = asyncio.Lock()

    async def setup_hook(self) -> None:
        self.cleanup_empty_channels.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)
        await self.reconcile_all_guilds()

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        del member
        if before.channel is None and after.channel is None:
            return
        channel = after.channel or before.channel
        if channel is None:
            return
        managed_guild = self.managed_guilds.get(channel.guild.id)
        if managed_guild is None:
            return
        await self.reconcile_guild(managed_guild)

    async def reconcile_all_guilds(self) -> None:
        async with self._reconcile_lock:
            for managed_guild in self.managed_guilds.values():
                await self._reconcile_guild_unlocked(managed_guild)

    async def reconcile_guild(self, managed_guild: ManagedGuild) -> None:
        async with self._reconcile_lock:
            await self._reconcile_guild_unlocked(managed_guild)

    async def _reconcile_guild_unlocked(self, managed_guild: ManagedGuild) -> None:
        guild = self.get_guild(managed_guild.guild_id)
        if guild is None:
            log.error("Guild %s was not found", managed_guild.guild_id)
            return

        channels = list(guild.voice_channels)
        for pool in managed_guild.pools:
            await self._reconcile_pool(guild, channels, pool)

    def _plan_pool_changes(
        self,
        action: str,
        guild: discord.Guild,
        channels: list[discord.VoiceChannel],
        pool: ManagedPool,
    ) -> ReconcilePlan | None:
        now = time.monotonic()
        refresh_empty_timers(
            channels,
            pool.empty_since_by_channel_id,
            now,
            pool.spec,
        )

        plan = plan_reconcile(
            channels,
            pool.empty_since_by_channel_id,
            now,
            pool.spec,
        )
        if plan.blocked_reason is not None:
            log.warning(
                "Skipping %s for guild %s pool %s: %s",
                action,
                guild.id,
                pool.spec.base_name,
                plan.blocked_reason,
            )
            return None

        return plan

    async def _reconcile_pool(
        self,
        guild: discord.Guild,
        channels: list[discord.VoiceChannel],
        pool: ManagedPool,
    ) -> None:
        plan = self._plan_pool_changes("reconcile", guild, channels, pool)
        if plan is None:
            return

        template = base_channel(channels, pool.spec)
        if plan.create_numbers and template is None:
            log.error(
                "No matching %s channel exists in guild %s to use as a creation template",
                pool.spec.base_name,
                guild.id,
            )
            return

        for number in plan.create_numbers:
            if not isinstance(template, discord.VoiceChannel):
                return
            await self._create_channel(guild, pool.spec, template, number, channels)

        await self._delete_channels(pool, plan.delete_channel_ids)

    @tasks.loop(seconds=CLEANUP_INTERVAL_SECONDS)
    async def cleanup_empty_channels(self) -> None:
        async with self._reconcile_lock:
            for managed_guild in self.managed_guilds.values():
                guild = self.get_guild(managed_guild.guild_id)
                if guild is None:
                    log.error("Guild %s was not found", managed_guild.guild_id)
                    continue
                channels = list(guild.voice_channels)
                for pool in managed_guild.pools:
                    plan = self._plan_pool_changes("cleanup", guild, channels, pool)
                    if plan is None:
                        continue
                    await self._delete_channels(pool, plan.delete_channel_ids)

    @cleanup_empty_channels.before_loop
    async def before_cleanup_empty_channels(self) -> None:
        await self.wait_until_ready()

    async def _create_channel(
        self,
        guild: discord.Guild,
        spec: ChannelPoolSpec,
        template: discord.VoiceChannel,
        number: int,
        channels: Iterable[discord.VoiceChannel],
    ) -> None:
        try:
            new_channel = await guild.create_voice_channel(
                name=spec.channel_name(number),
                category=template.category,
                overwrites=template.overwrites,
                bitrate=template.bitrate,
                user_limit=template.user_limit,
                rtc_region=template.rtc_region,
                video_quality_mode=template.video_quality_mode,
                reason=f"All {spec.base_name} voice channels are occupied",
            )
            await self._position_after_highest_managed(new_channel, channels, spec)
            log.info("Created channel %s in guild %s", new_channel.name, guild.id)
        except discord.DiscordException:
            log.exception(
                "Failed to create %s in guild %s", spec.channel_name(number), guild.id
            )

    async def _delete_channels(
        self, pool: ManagedPool, channel_ids: Iterable[int]
    ) -> None:
        for channel_id in channel_ids:
            channel = self.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                pool.empty_since_by_channel_id.pop(channel_id, None)
                continue
            try:
                await channel.delete(
                    reason="Managed voice channel exceeded desired count"
                )
                pool.empty_since_by_channel_id.pop(channel_id, None)
                log.info(
                    "Deleted channel %s from guild %s pool %s",
                    channel.name,
                    channel.guild.id,
                    pool.spec.base_name,
                )
            except discord.DiscordException:
                log.exception("Failed to delete channel %s", channel.name)

    async def _position_after_highest_managed(
        self,
        new_channel: discord.VoiceChannel,
        channels: Iterable[discord.VoiceChannel],
        spec: ChannelPoolSpec,
    ) -> None:
        highest_channel: discord.VoiceChannel | None = None
        highest_number = 0
        for channel in channels:
            number = parse_channel_number(channel.name, spec)
            if number is None:
                continue
            if number > highest_number:
                highest_number = number
                highest_channel = channel

        if highest_channel is None:
            return

        try:
            await new_channel.edit(position=highest_channel.position + 1)
        except discord.DiscordException:
            log.exception("Failed to position channel %s", new_channel.name)
