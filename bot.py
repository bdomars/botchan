from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from collections.abc import Iterable, Sequence
from typing import Protocol

import discord
from discord.ext import commands, tasks


log = logging.getLogger("botchan")

DEFAULT_BASE_CHANNEL_NAME = "Other Games"
DEFAULT_MIN_CHANNELS = 3
DEFAULT_MAX_CHANNELS = 10
DEFAULT_IDLE_SECONDS = 10 * 60
CLEANUP_INTERVAL_SECONDS = 30


class ManagedVoiceChannel(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def members(self) -> Sequence[object]: ...


@dataclass(frozen=True)
class BotConfig:
    token: str
    guild_id: int
    base_name: str = DEFAULT_BASE_CHANNEL_NAME
    min_channels: int = DEFAULT_MIN_CHANNELS
    max_channels: int = DEFAULT_MAX_CHANNELS
    idle_seconds: int = DEFAULT_IDLE_SECONDS


@dataclass(frozen=True)
class ChannelSpec:
    base_name: str
    min_channels: int
    max_channels: int
    idle_seconds: int

    @property
    def channel_re(self) -> re.Pattern[str]:
        return re.compile(rf"^{re.escape(self.base_name)}(?: #(?P<number>\d+))?$")

    def channel_name(self, number: int) -> str:
        if number == 1:
            return self.base_name
        return f"{self.base_name} #{number}"


@dataclass(frozen=True)
class ReconcilePlan:
    create_numbers: list[int] = field(default_factory=list)
    delete_channel_ids: list[int] = field(default_factory=list)
    blocked_reason: str | None = None


def load_config() -> BotConfig:
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    guild_id = os.environ.get("GUILD_ID", "").strip()
    base_name = os.environ.get("OTHER_GAMES_BASE_NAME", DEFAULT_BASE_CHANNEL_NAME).strip()
    min_channels = int(os.environ.get("OTHER_GAMES_MIN_CHANNELS", str(DEFAULT_MIN_CHANNELS)))
    max_channels = int(os.environ.get("OTHER_GAMES_MAX_CHANNELS", str(DEFAULT_MAX_CHANNELS)))
    idle_seconds = int(os.environ.get("OTHER_GAMES_IDLE_SECONDS", str(DEFAULT_IDLE_SECONDS)))

    if not token:
        raise RuntimeError("DISCORD_TOKEN is required")
    if not guild_id:
        raise RuntimeError("GUILD_ID is required")
    if not base_name:
        raise RuntimeError("OTHER_GAMES_BASE_NAME must not be empty")
    if min_channels < 1:
        raise RuntimeError("OTHER_GAMES_MIN_CHANNELS must be at least 1")
    if max_channels < min_channels:
        raise RuntimeError("OTHER_GAMES_MAX_CHANNELS must be greater than or equal to min channels")
    if idle_seconds < 0:
        raise RuntimeError("OTHER_GAMES_IDLE_SECONDS must not be negative")

    return BotConfig(
        token=token,
        guild_id=int(guild_id),
        base_name=base_name,
        min_channels=min_channels,
        max_channels=max_channels,
        idle_seconds=idle_seconds,
    )


def parse_channel_number(name: str, spec: ChannelSpec) -> int | None:
    match = spec.channel_re.fullmatch(name)
    if not match:
        return None
    number = match.group("number")
    if number is None:
        return 1
    parsed_number = int(number)
    return parsed_number if parsed_number > 1 else None


def channel_is_occupied(channel: ManagedVoiceChannel) -> bool:
    return len(channel.members) > 0


def canonical_managed_channels(
    channels: Iterable[ManagedVoiceChannel], spec: ChannelSpec
) -> dict[int, ManagedVoiceChannel]:
    managed: dict[int, ManagedVoiceChannel] = {}
    for channel in channels:
        number = parse_channel_number(channel.name, spec)
        if number is None:
            continue
        if number not in managed:
            managed[number] = channel
    return managed


def duplicate_extra_numbers(
    channels: Iterable[ManagedVoiceChannel], spec: ChannelSpec
) -> set[int]:
    seen: set[int] = set()
    duplicates: set[int] = set()
    for channel in channels:
        number = parse_channel_number(channel.name, spec)
        if number is None:
            continue
        if number in seen:
            duplicates.add(number)
        seen.add(number)
    return duplicates


def desired_channel_count(channels: Iterable[ManagedVoiceChannel], spec: ChannelSpec) -> int:
    occupied_count = sum(1 for channel in channels if channel_is_occupied(channel))
    return min(max(occupied_count + 1, spec.min_channels), spec.max_channels)


def plan_reconcile(
    channels: Iterable[ManagedVoiceChannel],
    empty_since_by_channel_id: dict[int, float],
    now: float,
    spec: ChannelSpec,
) -> ReconcilePlan:
    channel_list = list(channels)
    duplicate_numbers = duplicate_extra_numbers(channel_list, spec)
    if duplicate_numbers:
        duplicates = ", ".join(str(number) for number in sorted(duplicate_numbers))
        return ReconcilePlan(blocked_reason=f"duplicate channel numbers: {duplicates}")

    managed = canonical_managed_channels(channel_list, spec)
    desired_count = desired_channel_count(managed.values(), spec)
    current_count = len(managed)

    if current_count < desired_count:
        missing_numbers = [
            number for number in range(1, desired_count + 1) if number not in managed
        ]
        next_number = max(managed, default=0) + 1
        while len(missing_numbers) < desired_count - current_count:
            missing_numbers.append(next_number)
            next_number += 1
        return ReconcilePlan(create_numbers=missing_numbers[: desired_count - current_count])

    if current_count <= desired_count:
        return ReconcilePlan()

    excess_count = current_count - desired_count
    delete_channel_ids: list[int] = []
    for number, channel in sorted(managed.items(), reverse=True):
        if len(delete_channel_ids) >= excess_count:
            break
        if number <= spec.min_channels:
            continue
        if channel_is_occupied(channel):
            continue
        empty_since = empty_since_by_channel_id.get(channel.id)
        if empty_since is not None and now - empty_since >= spec.idle_seconds:
            delete_channel_ids.append(channel.id)

    return ReconcilePlan(delete_channel_ids=delete_channel_ids)


class OtherGamesBot(commands.Bot):
    def __init__(self, config: BotConfig) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True

        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config
        self.spec = ChannelSpec(
            base_name=config.base_name,
            min_channels=config.min_channels,
            max_channels=config.max_channels,
            idle_seconds=config.idle_seconds,
        )
        self.empty_since_by_channel_id: dict[int, float] = {}
        self._reconcile_lock = asyncio.Lock()

    async def setup_hook(self) -> None:
        self.cleanup_empty_channels.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)
        await self.reconcile_other_games_channels()

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if before.channel is None and after.channel is None:
            return
        await self.reconcile_other_games_channels()

    def target_guild(self) -> discord.Guild | None:
        return self.get_guild(self.config.guild_id)

    def voice_channels(self) -> list[discord.VoiceChannel]:
        guild = self.target_guild()
        return list(guild.voice_channels) if guild else []

    async def reconcile_other_games_channels(self) -> None:
        async with self._reconcile_lock:
            channels = self.voice_channels()
            self._refresh_empty_timers(channels)

            plan = plan_reconcile(channels, self.empty_since_by_channel_id, time.monotonic(), self.spec)
            if plan.blocked_reason is not None:
                log.warning("Skipping reconcile: %s", plan.blocked_reason)
                return

            template = self._base_channel(channels)
            if plan.create_numbers and template is None:
                log.error(
                    "No matching %s channel exists to use as a creation template",
                    self.config.base_name,
                )
                return

            for number in plan.create_numbers:
                if template is None:
                    return
                await self._create_channel(template, number, channels)

            await self._delete_channels(plan.delete_channel_ids)

    @tasks.loop(seconds=CLEANUP_INTERVAL_SECONDS)
    async def cleanup_empty_channels(self) -> None:
        channels = self.voice_channels()
        self._refresh_empty_timers(channels)

        plan = plan_reconcile(channels, self.empty_since_by_channel_id, time.monotonic(), self.spec)
        if plan.blocked_reason is not None:
            log.warning("Skipping cleanup: %s", plan.blocked_reason)
            return
        await self._delete_channels(plan.delete_channel_ids)

    @cleanup_empty_channels.before_loop
    async def before_cleanup_empty_channels(self) -> None:
        await self.wait_until_ready()

    def _refresh_empty_timers(self, channels: Iterable[discord.VoiceChannel]) -> None:
        now = time.monotonic()
        current_ids = set()

        for channel in channels:
            number = parse_channel_number(channel.name, self.spec)
            if number is None:
                continue
            current_ids.add(channel.id)

            if channel_is_occupied(channel):
                self.empty_since_by_channel_id.pop(channel.id, None)
            else:
                self.empty_since_by_channel_id.setdefault(channel.id, now)

        stale_ids = set(self.empty_since_by_channel_id) - current_ids
        for channel_id in stale_ids:
            self.empty_since_by_channel_id.pop(channel_id, None)

    def _base_channel(
        self, channels: Iterable[discord.VoiceChannel]
    ) -> discord.VoiceChannel | None:
        managed: dict[int, discord.VoiceChannel] = {}
        for channel in channels:
            number = parse_channel_number(channel.name, self.spec)
            if number is not None and number not in managed:
                managed[number] = channel

        return managed.get(1) or next(
            (channel for _, channel in sorted(managed.items())),
            None,
        )

    async def _create_channel(
        self,
        template: discord.VoiceChannel,
        number: int,
        channels: Iterable[discord.VoiceChannel],
    ) -> None:
        guild = self.target_guild()
        if guild is None:
            log.error("Guild %s was not found", self.config.guild_id)
            return

        try:
            new_channel = await guild.create_voice_channel(
                name=self.spec.channel_name(number),
                category=template.category,
                overwrites=template.overwrites,
                bitrate=template.bitrate,
                user_limit=template.user_limit,
                rtc_region=template.rtc_region,
                video_quality_mode=template.video_quality_mode,
                reason="All Other Games voice channels are occupied",
            )
            await self._position_after_highest_managed(new_channel, channels)
            log.info("Created channel %s", new_channel.name)
        except discord.DiscordException:
            log.exception("Failed to create %s", self.spec.channel_name(number))

    async def _delete_channels(self, channel_ids: Iterable[int]) -> None:
        for channel_id in channel_ids:
            channel = self.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                self.empty_since_by_channel_id.pop(channel_id, None)
                continue
            try:
                await channel.delete(reason="Managed voice channel exceeded desired count")
                self.empty_since_by_channel_id.pop(channel_id, None)
                log.info("Deleted channel %s", channel.name)
            except discord.DiscordException:
                log.exception("Failed to delete channel %s", channel.name)

    async def _position_after_highest_managed(
        self,
        new_channel: discord.VoiceChannel,
        channels: Iterable[discord.VoiceChannel],
    ) -> None:
        highest_channel: discord.VoiceChannel | None = None
        highest_number = 0
        for channel in channels:
            number = parse_channel_number(channel.name, self.spec)
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
            LOG.exception("Failed to position channel %s", new_channel.name)


def main() -> None:
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    config = load_config()
    bot = OtherGamesBot(config)
    bot.run(config.token, log_level=log_level, root_logger=True)


if __name__ == "__main__":
    main()
