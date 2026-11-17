from __future__ import annotations

import asyncio
import logging
import signal
import time
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

import discord
from discord.ext import commands, tasks

from botchan.config import MAX_CHANNEL_NAME_LENGTH, ChannelPoolSpec, GuildSpec
from botchan.database_config import (
    ConfigSnapshot,
    GuildConfigChange,
    PostgresConfigSource,
)
from botchan.guild_registry import GuildRegistry
from botchan.rate_limits import rate_limit_trace
from botchan.reconciliation import (
    ReconcilePlan,
    base_channel,
    canonical_managed_channels,
    channels_needing_sync,
    longest_name_too_long,
    parse_channel_number,
    plan_reconcile,
    pool_members,
    refresh_empty_timers,
    settings_fingerprint,
)

log = logging.getLogger("botchan")

CLEANUP_INTERVAL_SECONDS = 30
# Guild joins, leaves and permission changes are written as they happen; the
# full rewrite catches what the gateway doesn't report (member counts, roles
# given to the bot) and any write that failed.
GUILD_REGISTRY_SYNC_MINUTES = 10
# discord.py waits out rate limits by itself. A rename can wait ten minutes
# (two per channel per ten minutes), and waiting inside a pass would hold the
# guild lock that long, so renames and creates give up after this and a later
# pass tries again.
#
# Not discord.py's max_ratelimit_timeout: that refuses every request in a
# bucket whose window resets later than the timeout, even with requests left.
# Channel creation's window is a day long, so it allowed one create per day.
REQUEST_DEADLINE_SECONDS = 30
# How long a guild waits to create channels again after a create gave up.
CREATE_RETRY_SECONDS = 10 * 60

# (settings requested, settings the channel had once the edit returned)
type SyncRecord = tuple[tuple[object, ...], tuple[object, ...]]


@dataclass
class ManagedPool:
    spec: ChannelPoolSpec
    empty_since_by_channel_id: dict[int, float] = field(default_factory=dict)
    # Per channel, the last sync we attempted: what we asked for, and what the
    # channel looked like once the edit returned. Every edit we make comes back
    # as a channel update event, so a value Discord clamps or rejects would
    # otherwise never match the base channel and we would edit forever.
    # Comparing the observed half too means a channel an admin has since
    # changed is still resynced, because it no longer looks the way we left it.
    last_sync_by_channel_id: dict[int, SyncRecord] = field(default_factory=dict)
    # The base channel's name as of the last pass. Extras are found by name, so
    # when the base is renamed they must follow before anything else happens.
    last_base_name: str | None = None
    # Extras still to be renamed after the base channel was: id -> number.
    pending_renames: dict[int, int] = field(default_factory=dict)
    # Why the pool is currently paused, kept to log only the transitions.
    paused_reason: str | None = None


@dataclass
class ManagedGuild:
    guild_id: int
    pools: list[ManagedPool]


@dataclass(frozen=True)
class ResolvedPool:
    # The pool's spec named after its base channel's current name.
    spec: ChannelPoolSpec
    base: discord.VoiceChannel
    channels: list[discord.VoiceChannel]


def pool_key(spec: ChannelPoolSpec) -> int | str:
    return spec.base_channel_id if spec.base_channel_id is not None else spec.base_name


# AutoShardedBot runs every shard in this one process. Discord requires shards
# from 2,500 guilds; below that it recommends a single one, so this behaves
# like a plain Bot until then.
class BotChan(commands.AutoShardedBot):
    def __init__(
        self,
        config_source: PostgresConfigSource,
        guild_registry: GuildRegistry | None = None,
    ) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            http_trace=rate_limit_trace(),
        )
        self.managed_guilds: dict[int, ManagedGuild] = {}
        self._config_revisions: dict[int, int] = {}
        self._config_source = config_source
        self._guild_registry = guild_registry
        # Serializes passes within a guild, so two concurrent passes never both
        # create the same channel number. Locks are never removed: a pass may
        # still be waiting on one after its guild's config is deleted.
        self._guild_locks: dict[int, asyncio.Lock] = {}
        self._shutdown_task: asyncio.Task[None] | None = None
        # Per guild, the monotonic time until which no pool tries to create
        # channels, after Discord held a create back past the deadline.
        self._creates_blocked_until: dict[int, float] = {}
        self._monotonic = time.monotonic

    async def setup_hook(self) -> None:
        await self._config_source.start(
            self._apply_config_snapshot,
            self._apply_config_change,
        )
        if self._guild_registry is not None:
            await self._guild_registry.start()
            self.sync_guild_registry.start()
        self.cleanup_empty_channels.start()
        # In a container the bot runs as PID 1, which ignores SIGTERM unless a
        # handler is installed, so `podman stop` would otherwise SIGKILL it.
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, self._on_sigterm)

    def _on_sigterm(self) -> None:
        if self._shutdown_task is None:
            log.info("Received SIGTERM, shutting down")
            self._shutdown_task = asyncio.create_task(self.close())

    async def close(self) -> None:
        self.cleanup_empty_channels.cancel()
        self.sync_guild_registry.cancel()
        try:
            await self._config_source.close()
            if self._guild_registry is not None:
                await self._guild_registry.close()
        finally:
            await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)
        await self.reconcile_all_guilds()

    @tasks.loop(minutes=GUILD_REGISTRY_SYNC_MINUTES)
    async def sync_guild_registry(self) -> None:
        if self._guild_registry is None:
            return
        # discord.py ends the loop for good on most exceptions.
        try:
            count = await self._guild_registry.sync(lambda: self.guilds)
        except Exception:
            log.exception("Writing the guild list to the database failed")
        else:
            log.info("In %s guilds", count)

    @sync_guild_registry.before_loop
    async def before_sync_guild_registry(self) -> None:
        await self.wait_until_ready()

    async def _record_guild(self, guild: discord.Guild) -> None:
        if self._guild_registry is None:
            return
        # The next full sync retries a failed write; it must not fail the event.
        try:
            await self._guild_registry.update(guild)
        except Exception:
            log.exception("Recording guild %s failed", guild.id)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Joined guild %s", guild.id)
        await self._record_guild(guild)

    async def on_guild_available(self, guild: discord.Guild) -> None:
        await self._record_guild(guild)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info("Left guild %s", guild.id)
        if self._guild_registry is None:
            return
        try:
            await self._guild_registry.remove(guild.id)
        except Exception:
            log.exception("Recording that guild %s was left failed", guild.id)

    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        if (before.name, before.icon) != (after.name, after.icon):
            await self._record_guild(after)

    # The bot's permissions come from its roles. Re-authorizing the bot with
    # new permissions edits its managed role, so role events are the ones that
    # matter; roles handed to the bot later are picked up by the full sync.
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        me = after.guild.me
        if before.permissions != after.permissions and me is not None and after in me.roles:
            await self._record_guild(after.guild)

    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await self._record_guild(role.guild)

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

    @staticmethod
    def _naming_spec(guild: discord.Guild, spec: ChannelPoolSpec) -> ChannelPoolSpec | None:
        """The pool's spec named after its base channel, or None if that is gone."""
        if spec.base_channel_id is None:
            return spec
        base = guild.get_channel(spec.base_channel_id)
        if not isinstance(base, discord.VoiceChannel):
            return None
        return replace(spec, base_name=base.name)

    def _touches_a_pool(
        self,
        guild: discord.Guild,
        managed_guild: ManagedGuild,
        channel_id: int,
        *names: str,
    ) -> bool:
        for pool in managed_guild.pools:
            if channel_id == pool.spec.base_channel_id:
                return True
            spec = self._naming_spec(guild, pool.spec)
            if spec is not None and any(
                parse_channel_number(name, spec) is not None for name in names
            ):
                return True
        return False

    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ) -> None:
        if not isinstance(after, discord.VoiceChannel):
            return
        managed_guild = self.managed_guilds.get(after.guild.id)
        if managed_guild is None:
            return

        was = before.name if isinstance(before, discord.VoiceChannel) else after.name

        # Both names matter: a rename can carry a channel into a pool or out of
        # one, and leaving drops the pool below its minimum just as surely as a
        # deletion would.
        if not self._touches_a_pool(after.guild, managed_guild, after.id, was, after.name):
            return

        # A rename alone is a change even when no synced setting moved.
        if (
            isinstance(before, discord.VoiceChannel)
            and was == after.name
            and settings_fingerprint(before) == settings_fingerprint(after)
        ):
            return

        await self.reconcile_guild_id(after.guild.id)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if not isinstance(channel, discord.VoiceChannel):
            return
        managed_guild = self.managed_guilds.get(channel.guild.id)
        if managed_guild is None:
            return
        if not self._touches_a_pool(channel.guild, managed_guild, channel.id, channel.name):
            return

        # Our own deletions land here too. Those leave the pool at its desired
        # count, so the pass they trigger finds nothing to do. Deleting a base
        # channel pauses its pool.
        await self.reconcile_guild_id(channel.guild.id)

    def _guild_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._guild_locks.get(guild_id)
        if lock is None:
            lock = self._guild_locks[guild_id] = asyncio.Lock()
        return lock

    async def reconcile_all_guilds(self) -> None:
        # Copy the IDs: config changes can edit managed_guilds while we await.
        await self._reconcile_guilds(list(self.managed_guilds))

    async def _reconcile_guilds(self, guild_ids: list[int]) -> None:
        for guild_id in guild_ids:
            # One failing guild must not skip the rest of the pass.
            try:
                await self.reconcile_guild_id(guild_id)
            except Exception:
                log.exception("Reconcile failed for guild %s", guild_id)

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
            new_guilds[guild_id] = self._build_managed_guild(versioned_config.spec, existing)
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
            await self._reconcile_guilds(changed_guild_ids)

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
            await self._reconcile_guilds([change.guild_id])

    @staticmethod
    def _build_managed_guild(spec: GuildSpec, existing: ManagedGuild | None) -> ManagedGuild:
        previous_pools = (
            {pool_key(pool.spec): pool for pool in existing.pools} if existing is not None else {}
        )
        pools = []
        for pool_spec in spec.channel_pools:
            previous = previous_pools.get(pool_key(pool_spec))
            if previous is None:
                pools.append(ManagedPool(spec=pool_spec))
            else:
                pools.append(replace(previous, spec=pool_spec))
        return ManagedGuild(guild_id=spec.guild_id, pools=pools)

    async def _reconcile_guild_unlocked(self, managed_guild: ManagedGuild) -> None:
        guild = self.get_guild(managed_guild.guild_id)
        if guild is None:
            log.error("Guild %s was not found", managed_guild.guild_id)
            return

        channels = list(guild.voice_channels)
        for pool in managed_guild.pools:
            await self._reconcile_pool(guild, channels, pool)

    def _set_paused(self, guild: discord.Guild, pool: ManagedPool, reason: str | None) -> None:
        if reason == pool.paused_reason:
            return
        if reason is not None:
            log.warning("Pausing guild %s pool %s: %s", guild.id, pool.spec.base_name, reason)
        else:
            log.info("Resuming guild %s pool %s", guild.id, pool.spec.base_name)
        pool.paused_reason = reason

    def _resolve_pool(
        self,
        guild: discord.Guild,
        channels: list[discord.VoiceChannel],
        pool: ManagedPool,
    ) -> ResolvedPool | None:
        """Find the pool's base channel and members, or pause the pool."""
        spec = pool.spec
        if spec.base_channel_id is not None:
            found = guild.get_channel(spec.base_channel_id)
            base = found if isinstance(found, discord.VoiceChannel) else None
            missing = "its base channel was deleted; pick a new one in the editor"
        else:
            # Saved before the editor picked channels: the base is found by name.
            base = canonical_managed_channels(channels, spec).get(1)
            missing = f"no voice channel is named {spec.base_name!r}"
        if base is None:
            self._set_paused(guild, pool, missing)
            return None

        live = replace(spec, base_name=base.name)
        if longest_name_too_long(live):
            self._set_paused(
                guild,
                pool,
                f"{live.channel_name(live.max_channels)!r} would exceed Discord's "
                f"{MAX_CHANNEL_NAME_LENGTH} character limit; shorten the base channel's name",
            )
            return None
        self._set_paused(guild, pool, None)

        if (
            spec.base_channel_id is not None
            and pool.last_base_name is not None
            and pool.last_base_name != base.name
        ):
            self._queue_extra_renames(channels, pool, replace(spec, base_name=pool.last_base_name))
        pool.last_base_name = base.name

        if spec.base_channel_id is not None:
            members = pool_members(channels, base, live)
        else:
            # Keep every match, so duplicates still block the pass.
            members = [c for c in channels if parse_channel_number(c.name, live) is not None]
        return ResolvedPool(spec=live, base=base, channels=members)

    @staticmethod
    def _queue_extra_renames(
        channels: Iterable[discord.VoiceChannel],
        pool: ManagedPool,
        old_spec: ChannelPoolSpec,
    ) -> None:
        for number, channel in canonical_managed_channels(channels, old_spec).items():
            if number > 1:
                pool.pending_renames.setdefault(channel.id, number)

    async def _apply_pending_renames(
        self, guild: discord.Guild, pool: ManagedPool, spec: ChannelPoolSpec
    ) -> bool:
        """Rename extras after their base channel. True if the pass must stop.

        The guild snapshot still has the old names until Discord's update
        events land, so planning in the same pass would miss the renamed
        extras and create new ones. Those events start the next pass.
        """
        if not pool.pending_renames:
            return False
        renamed_any = False
        for channel_id, number in list(pool.pending_renames.items()):
            channel = guild.get_channel(channel_id)
            target = spec.channel_name(number)
            if not isinstance(channel, discord.VoiceChannel) or channel.name == target:
                pool.pending_renames.pop(channel_id, None)
                continue
            renamed_any = True
            try:
                await asyncio.wait_for(
                    channel.edit(
                        name=target, reason=f"The {spec.base_name} base channel was renamed"
                    ),
                    REQUEST_DEADLINE_SECONDS,
                )
                log.info("Renamed %s to %s in guild %s", channel.name, target, guild.id)
            except TimeoutError:
                log.warning(
                    "Renaming %s in guild %s is rate limited; retrying later",
                    channel.name,
                    guild.id,
                )
                continue
            except discord.DiscordException:
                # Give up on this one: it keeps its old name and leaves the pool.
                log.exception("Failed to rename %s in guild %s", channel.name, guild.id)
            pool.pending_renames.pop(channel_id, None)
        return renamed_any or bool(pool.pending_renames)

    def _plan_pool_changes(
        self,
        action: str,
        guild: discord.Guild,
        resolved: ResolvedPool,
        pool: ManagedPool,
    ) -> ReconcilePlan | None:
        now = time.monotonic()
        refresh_empty_timers(
            resolved.channels,
            pool.empty_since_by_channel_id,
            now,
            resolved.spec,
        )

        plan = plan_reconcile(
            resolved.channels,
            pool.empty_since_by_channel_id,
            now,
            resolved.spec,
        )
        if plan.blocked_reason is not None:
            log.warning(
                "Skipping %s for guild %s pool %s: %s",
                action,
                guild.id,
                resolved.spec.base_name,
                plan.blocked_reason,
            )
            return None

        return plan

    async def _reconcile_pool(
        self,
        guild: discord.Guild,
        channels: list[discord.VoiceChannel],
        pool: ManagedPool,
        *,
        create: bool = True,
    ) -> None:
        resolved = self._resolve_pool(guild, channels, pool)
        if resolved is None:
            return
        if await self._apply_pending_renames(guild, pool, resolved.spec):
            return

        plan = self._plan_pool_changes("reconcile" if create else "cleanup", guild, resolved, pool)
        if plan is None:
            return

        # Track created channels locally: the guild snapshot is taken once per
        # pass, so without this each new channel would be positioned as if the
        # others did not exist.
        working = list(resolved.channels)
        if create and plan.create_numbers and not self._creates_blocked(guild.id):
            for number in plan.create_numbers:
                new_channel = await self._create_channel(
                    guild, resolved.spec, resolved.base, number, working
                )
                if new_channel is None:
                    break
                working.append(new_channel)

        await self._delete_channels(pool, plan.delete_channel_ids)

        deleted = set(plan.delete_channel_ids)
        await self._sync_pool_settings(
            guild, [c for c in working if c.id not in deleted], pool, resolved.spec
        )

    def _creates_blocked(self, guild_id: int) -> bool:
        until = self._creates_blocked_until.get(guild_id)
        if until is None:
            return False
        if self._monotonic() < until:
            return True
        del self._creates_blocked_until[guild_id]
        log.info("Creating channels in guild %s again", guild_id)
        return False

    @tasks.loop(seconds=CLEANUP_INTERVAL_SECONDS)
    async def cleanup_empty_channels(self) -> None:
        started = time.monotonic()
        guild_ids = list(self.managed_guilds)
        await self._cleanup_guilds(guild_ids)
        # A pass longer than the interval means one process no longer keeps
        # up with its guilds: the signal to look at splitting the shards.
        elapsed = time.monotonic() - started
        if elapsed > CLEANUP_INTERVAL_SECONDS:
            log.warning("Cleanup pass over %s guilds took %.1f seconds", len(guild_ids), elapsed)

    async def _cleanup_guilds(self, guild_ids: list[int]) -> None:
        for guild_id in guild_ids:
            # discord.py stops the loop for good on most exceptions, so one
            # failing guild must not escape and end cleanup for every guild.
            try:
                async with self._guild_lock(guild_id):
                    managed_guild = self.managed_guilds.get(guild_id)
                    if managed_guild is not None:
                        await self._cleanup_guild_unlocked(managed_guild)
            except Exception:
                log.exception("Cleanup failed for guild %s", guild_id)

    async def _cleanup_guild_unlocked(self, managed_guild: ManagedGuild) -> None:
        guild = self.get_guild(managed_guild.guild_id)
        if guild is None:
            log.error("Guild %s was not found", managed_guild.guild_id)
            return
        channels = list(guild.voice_channels)
        for pool in managed_guild.pools:
            await self._reconcile_pool(guild, channels, pool, create=False)

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
    ) -> discord.VoiceChannel | None:
        try:
            new_channel = await asyncio.wait_for(
                guild.create_voice_channel(
                    name=spec.channel_name(number),
                    category=template.category,
                    overwrites=template.overwrites,
                    bitrate=template.bitrate,
                    user_limit=template.user_limit,
                    rtc_region=template.rtc_region,
                    video_quality_mode=template.video_quality_mode,
                    reason=f"All {spec.base_name} voice channels are occupied",
                ),
                REQUEST_DEADLINE_SECONDS,
            )
        except TimeoutError:
            # Held back by a rate limit, most likely. Asking again on every
            # voice event would only wait again, so pause creating for a while.
            self._creates_blocked_until[guild.id] = self._monotonic() + CREATE_RETRY_SECONDS
            log.warning(
                "Creating %s in guild %s took over %s seconds; trying again in %s minutes",
                spec.channel_name(number),
                guild.id,
                REQUEST_DEADLINE_SECONDS,
                CREATE_RETRY_SECONDS // 60,
            )
            return None
        except discord.DiscordException:
            log.exception("Failed to create %s in guild %s", spec.channel_name(number), guild.id)
            return None

        await self._position_after_highest_managed(new_channel, channels, spec)
        log.info("Created channel %s in guild %s", new_channel.name, guild.id)
        return new_channel

    async def _sync_pool_settings(
        self,
        guild: discord.Guild,
        channels: list[discord.VoiceChannel],
        pool: ManagedPool,
        spec: ChannelPoolSpec,
    ) -> None:
        template = base_channel(channels, spec)
        if template is None:
            pool.last_sync_by_channel_id.clear()
            return

        managed_ids = {
            channel.id for channel in canonical_managed_channels(channels, spec).values()
        }
        for channel_id in set(pool.last_sync_by_channel_id) - managed_ids:
            pool.last_sync_by_channel_id.pop(channel_id, None)

        desired = settings_fingerprint(template)
        for channel in channels_needing_sync(channels, spec):
            # Nothing has moved since our last attempt on this channel, so
            # repeating it would produce the same result and loop forever.
            if pool.last_sync_by_channel_id.get(channel.id) == (
                desired,
                settings_fingerprint(channel),
            ):
                continue

            applied = channel
            try:
                edited = await channel.edit(
                    category=template.category,
                    overwrites=template.overwrites,
                    bitrate=template.bitrate,
                    user_limit=template.user_limit,
                    rtc_region=template.rtc_region,
                    video_quality_mode=template.video_quality_mode,
                    reason=f"Sync with the {spec.base_name} base channel",
                )
                if edited is not None:
                    # discord.py 2.x returns the edited channel rather than
                    # updating in place; the cached one lags until the gateway
                    # event lands.
                    applied = edited
                log.info(
                    "Synced channel %s in guild %s with %s",
                    channel.name,
                    guild.id,
                    template.name,
                )
            except discord.Forbidden:
                log.warning(
                    "Cannot sync %s in guild %s: BotChan needs Manage Channels and "
                    "Manage Roles to copy permissions from %s",
                    channel.name,
                    guild.id,
                    template.name,
                )
            except discord.DiscordException:
                log.exception("Failed to sync channel %s in guild %s", channel.name, guild.id)

            pool.last_sync_by_channel_id[channel.id] = (
                desired,
                settings_fingerprint(applied),
            )

    async def _delete_channels(self, pool: ManagedPool, channel_ids: Iterable[int]) -> None:
        for channel_id in channel_ids:
            channel = self.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                pool.empty_since_by_channel_id.pop(channel_id, None)
                continue
            try:
                await channel.delete(reason="Managed voice channel exceeded desired count")
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
