from __future__ import annotations

import asyncio
import logging
import signal
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

import discord
from discord.ext import commands, tasks

from botchan.config import ChannelPoolSpec, GuildSpec
from botchan.database_config import (
    ConfigSnapshot,
    GuildConfigChange,
    PostgresConfigSource,
)
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
    def __init__(self, config_source: PostgresConfigSource) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True

        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.managed_guilds: dict[int, ManagedGuild] = {}
        self._config_revisions: dict[int, int] = {}
        self._config_source = config_source
        # Serializes passes within a guild, so two concurrent passes never both
        # create the same channel number. Locks are never removed: a pass may
        # still be waiting on one after its guild's config is deleted.
        self._guild_locks: dict[int, asyncio.Lock] = {}
        self._shutdown_task: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        await self._config_source.start(
            self._apply_config_snapshot,
            self._apply_config_change,
        )
        self.cleanup_empty_channels.start()
        # In a container the bot runs as PID 1, which ignores SIGTERM unless a
        # handler is installed, so `podman stop` would otherwise SIGKILL it.
        asyncio.get_running_loop().add_signal_handler(
            signal.SIGTERM, self._on_sigterm
        )

    def _on_sigterm(self) -> None:
        if self._shutdown_task is None:
            log.info("Received SIGTERM, shutting down")
            self._shutdown_task = asyncio.create_task(self.close())

    async def close(self) -> None:
        self.cleanup_empty_channels.cancel()
        try:
            await self._config_source.close()
        finally:
            await super().close()

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
        await self.reconcile_guild_id(channel.guild.id)

    def _guild_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._guild_locks.get(guild_id)
        if lock is None:
            lock = self._guild_locks[guild_id] = asyncio.Lock()
        return lock

    async def reconcile_all_guilds(self) -> None:
        # Copy the IDs: config changes can edit managed_guilds while we await.
        for guild_id in list(self.managed_guilds):
            await self.reconcile_guild_id(guild_id)

    async def reconcile_guild_id(self, guild_id: int) -> None:
        if guild_id not in self.managed_guilds:
            return
        async with self._guild_lock(guild_id):
            # Look up again: the config may have changed while we waited.
            managed_guild = self.managed_guilds.get(guild_id)
            if managed_guild is not None:
                await self._reconcile_guild_unlocked(managed_guild)

    async def _apply_config_snapshot(self, snapshot: ConfigSnapshot) -> None:
        changed_guild_ids: list[int] = []
        new_guilds: dict[int, ManagedGuild] = {}
        new_revisions: dict[int, int] = {}

        for guild_id, versioned_config in snapshot.guilds.items():
            existing = self.managed_guilds.get(guild_id)
            new_guilds[guild_id] = self._build_managed_guild(
                versioned_config.spec, existing
            )
            new_revisions[guild_id] = versioned_config.revision
            if self._config_revisions.get(guild_id) != versioned_config.revision:
                changed_guild_ids.append(guild_id)

        for guild_id in snapshot.invalid_guild_ids:
            existing = self.managed_guilds.get(guild_id)
            revision = self._config_revisions.get(guild_id)
            if existing is not None and revision is not None:
                new_guilds[guild_id] = existing
                new_revisions[guild_id] = revision

        self.managed_guilds = new_guilds
        self._config_revisions = new_revisions
        log.info("Loaded configuration for %s guilds", len(new_guilds))

        if self.is_ready():
            for guild_id in changed_guild_ids:
                await self.reconcile_guild_id(guild_id)

    async def _apply_config_change(self, change: GuildConfigChange) -> None:
        if change.invalid:
            return
        if change.config is None:
            self.managed_guilds.pop(change.guild_id, None)
            self._config_revisions.pop(change.guild_id, None)
            log.info("Stopped managing guild %s", change.guild_id)
            return

        current_revision = self._config_revisions.get(change.guild_id, 0)
        if current_revision >= change.config.revision:
            return
        self.managed_guilds[change.guild_id] = self._build_managed_guild(
            change.config.spec,
            self.managed_guilds.get(change.guild_id),
        )
        self._config_revisions[change.guild_id] = change.config.revision
        log.info(
            "Loaded guild %s configuration revision %s",
            change.guild_id,
            change.config.revision,
        )
        if self.is_ready():
            await self.reconcile_guild_id(change.guild_id)

    @staticmethod
    def _build_managed_guild(
        spec: GuildSpec, existing: ManagedGuild | None
    ) -> ManagedGuild:
        previous_pools = (
            {pool.spec.base_name: pool for pool in existing.pools}
            if existing is not None
            else {}
        )
        pools = []
        for pool_spec in spec.channel_pools:
            previous = previous_pools.get(pool_spec.base_name)
            pools.append(
                ManagedPool(
                    spec=pool_spec,
                    empty_since_by_channel_id=(
                        previous.empty_since_by_channel_id
                        if previous is not None
                        else {}
                    ),
                )
            )
        return ManagedGuild(guild_id=spec.guild_id, pools=pools)

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
        for guild_id in list(self.managed_guilds):
            async with self._guild_lock(guild_id):
                managed_guild = self.managed_guilds.get(guild_id)
                if managed_guild is not None:
                    await self._cleanup_guild_unlocked(managed_guild)

    async def _cleanup_guild_unlocked(self, managed_guild: ManagedGuild) -> None:
        guild = self.get_guild(managed_guild.guild_id)
        if guild is None:
            log.error("Guild %s was not found", managed_guild.guild_id)
            return
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
