from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import time
from dataclasses import dataclass, field
from collections.abc import Iterable, Sequence
from typing import Protocol, Self

import discord
from discord.ext import commands, tasks
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


log = logging.getLogger("botchan")

DEFAULT_BASE_CHANNEL_NAME = "Other Games"
DEFAULT_MIN_CHANNELS = 3
DEFAULT_MAX_CHANNELS = 10
DEFAULT_IDLE_SECONDS = 10 * 60
CLEANUP_INTERVAL_SECONDS = 30
DEFAULT_CONFIG_PATH = "botchan.config.json"


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
    guilds: dict[int, GuildSpec]


@dataclass(frozen=True)
class GuildSpec:
    guild_id: int
    channel_pools: list[ChannelPoolSpec]


@dataclass(frozen=True)
class ChannelPoolSpec:
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


ChannelSpec = ChannelPoolSpec


@dataclass
class ManagedPool:
    spec: ChannelPoolSpec
    empty_since_by_channel_id: dict[int, float] = field(default_factory=dict)


@dataclass
class ManagedGuild:
    guild_id: int
    pools: list[ManagedPool]


@dataclass(frozen=True)
class ReconcilePlan:
    create_numbers: list[int] = field(default_factory=list)
    delete_channel_ids: list[int] = field(default_factory=list)
    blocked_reason: str | None = None


class ConfigRepository(Protocol):
    def load(self) -> BotConfig: ...


class JsonConfigRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> BotConfig:
        return load_config_file(self.path)


def load_config() -> BotConfig:
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is required")

    config_path = Path(os.environ.get("BOTCHAN_CONFIG", DEFAULT_CONFIG_PATH))
    config = JsonConfigRepository(config_path).load()
    return BotConfig(token=token, guilds=config.guilds)


def load_config_file(path: Path) -> BotConfig:
    try:
        with path.open(encoding="utf-8") as config_file:
            data = json.load(config_file)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Config file is not valid JSON: {path}") from exc

    return parse_config_data(data, token="")


class ChannelPoolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    base_name: str = DEFAULT_BASE_CHANNEL_NAME
    min_channels: int = Field(default=DEFAULT_MIN_CHANNELS, ge=1)
    max_channels: int = DEFAULT_MAX_CHANNELS
    idle_seconds: int = Field(default=DEFAULT_IDLE_SECONDS, ge=0)

    @field_validator("base_name")
    @classmethod
    def strip_base_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.max_channels < self.min_channels:
            raise ValueError(
                "max_channels must be greater than or equal to min_channels"
            )
        return self

    def to_spec(self) -> ChannelPoolSpec:
        return ChannelPoolSpec(
            base_name=self.base_name,
            min_channels=self.min_channels,
            max_channels=self.max_channels,
            idle_seconds=self.idle_seconds,
        )


class GuildConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    guild_id: int
    channel_pools: list[ChannelPoolConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_pool_base_names(self) -> Self:
        seen_base_names: set[str] = set()
        for pool in self.channel_pools:
            if pool.base_name in seen_base_names:
                raise ValueError(
                    f"{pool.base_name!r} duplicates another pool in guild "
                    f"{self.guild_id}"
                )
            seen_base_names.add(pool.base_name)
        return self

    def to_spec(self) -> GuildSpec:
        return GuildSpec(
            guild_id=self.guild_id,
            channel_pools=[pool.to_spec() for pool in self.channel_pools],
        )


class FileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    guilds: list[GuildConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_guild_ids(self) -> Self:
        seen_guild_ids: set[int] = set()
        for guild in self.guilds:
            if guild.guild_id in seen_guild_ids:
                raise ValueError(f"Duplicate guild_id: {guild.guild_id}")
            seen_guild_ids.add(guild.guild_id)
        return self


def parse_config_data(data: object, token: str = "") -> BotConfig:
    try:
        config = FileConfig.model_validate(data)
    except ValidationError as exc:
        raise RuntimeError(f"Invalid config: {exc}") from exc

    guilds = {guild.guild_id: guild.to_spec() for guild in config.guilds}
    return BotConfig(token=token, guilds=guilds)


def parse_channel_number(name: str, spec: ChannelPoolSpec) -> int | None:
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
    channels: Iterable[ManagedVoiceChannel], spec: ChannelPoolSpec
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
    channels: Iterable[ManagedVoiceChannel], spec: ChannelPoolSpec
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


def desired_channel_count(channels: Iterable[ManagedVoiceChannel], spec: ChannelPoolSpec) -> int:
    occupied_count = sum(1 for channel in channels if channel_is_occupied(channel))
    return min(max(occupied_count + 1, spec.min_channels), spec.max_channels)


def plan_reconcile(
    channels: Iterable[ManagedVoiceChannel],
    empty_since_by_channel_id: dict[int, float],
    now: float,
    spec: ChannelPoolSpec,
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


def refresh_empty_timers(
    channels: Iterable[ManagedVoiceChannel],
    empty_since_by_channel_id: dict[int, float],
    now: float,
    spec: ChannelPoolSpec,
) -> None:
    current_ids = set()

    for channel in channels:
        number = parse_channel_number(channel.name, spec)
        if number is None:
            continue
        current_ids.add(channel.id)

        if channel_is_occupied(channel):
            empty_since_by_channel_id.pop(channel.id, None)
        else:
            empty_since_by_channel_id.setdefault(channel.id, now)

    stale_ids = set(empty_since_by_channel_id) - current_ids
    for channel_id in stale_ids:
        empty_since_by_channel_id.pop(channel_id, None)


def base_channel(
    channels: Iterable[ManagedVoiceChannel], spec: ChannelPoolSpec
) -> ManagedVoiceChannel | None:
    managed: dict[int, ManagedVoiceChannel] = {}
    for channel in channels:
        number = parse_channel_number(channel.name, spec)
        if number is not None and number not in managed:
            managed[number] = channel

    return managed.get(1) or next(
        (channel for _, channel in sorted(managed.items())),
        None,
    )


class OtherGamesBot(commands.Bot):
    def __init__(self, config: BotConfig) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True

        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config
        self.managed_guilds = {
            guild_id: ManagedGuild(
                guild_id=guild_id,
                pools=[ManagedPool(spec=pool_spec) for pool_spec in guild_spec.channel_pools],
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

    async def _reconcile_pool(
        self,
        guild: discord.Guild,
        channels: list[discord.VoiceChannel],
        pool: ManagedPool,
    ) -> None:
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
                "Skipping reconcile for guild %s pool %s: %s",
                guild.id,
                pool.spec.base_name,
                plan.blocked_reason,
            )
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
                            "Skipping cleanup for guild %s pool %s: %s",
                            guild.id,
                            pool.spec.base_name,
                            plan.blocked_reason,
                        )
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
            log.exception("Failed to create %s in guild %s", spec.channel_name(number), guild.id)

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


def main() -> None:
    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = logging.getLevelNamesMapping().get(log_level_name)
    if log_level is None:
        raise RuntimeError(f"Unknown LOG_LEVEL: {log_level_name}")

    config = load_config()
    bot = OtherGamesBot(config)
    bot.run(config.token, log_level=log_level, root_logger=True)


if __name__ == "__main__":
    main()
