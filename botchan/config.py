from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

DEFAULT_RUNTIME_CONFIG_PATH = "botchan.config.json"
# Discord rejects longer channel names.
MAX_CHANNEL_NAME_LENGTH = 100
# A short idle timeout turns everyone hopping in and out of a voice channel
# into a channel created and deleted again, cluttering the channel list and
# the audit log. Extras always wait at least this long before deletion.
MIN_IDLE_SECONDS = 60


def is_snowflake(value: str) -> bool:
    return value.isdigit() and value[0] != "0" and len(value) <= 20


@dataclass(frozen=True)
class RuntimeConfig:
    guilds: dict[int, GuildSpec]


@dataclass(frozen=True)
class GuildSpec:
    guild_id: int
    channel_pools: list[ChannelPoolSpec]


@dataclass(frozen=True)
class ChannelPoolSpec:
    # For a pool anchored to a channel this is the name the editor last saw;
    # the bot uses the base channel's live name instead.
    base_name: str
    min_channels: int
    max_channels: int
    idle_seconds: int
    # None for pools saved before the editor picked channels: those find
    # their base channel by name.
    base_channel_id: int | None = None

    @property
    def channel_re(self) -> re.Pattern[str]:
        return re.compile(rf"^{re.escape(self.base_name)}(?: #(?P<number>\d+))?$")

    def channel_name(self, number: int) -> str:
        return self.base_name if number == 1 else f"{self.base_name} #{number}"


class ChannelPoolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    base_name: str = "Other Games"
    base_channel_id: str | None = None
    min_channels: int = Field(default=3, ge=1)
    max_channels: int = Field(default=10, ge=1)
    idle_seconds: int = Field(default=10 * 60, ge=0)

    @field_validator("base_name")
    @classmethod
    def strip_base_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped

    @field_validator("base_channel_id")
    @classmethod
    def validate_base_channel_id(cls, value: str | None) -> str | None:
        if value is not None and not is_snowflake(value):
            raise ValueError("must be a Discord snowflake string")
        return value

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.max_channels < self.min_channels:
            raise ValueError("max_channels must be greater than or equal to min_channels")
        return self

    def to_spec(self) -> ChannelPoolSpec:
        return ChannelPoolSpec(
            base_name=self.base_name,
            min_channels=self.min_channels,
            max_channels=self.max_channels,
            # Configs saved before the floor existed still load; they just wait longer.
            idle_seconds=max(self.idle_seconds, MIN_IDLE_SECONDS),
            base_channel_id=(
                int(self.base_channel_id) if self.base_channel_id is not None else None
            ),
        )


class GuildConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    guild_id: int
    channel_pools: list[ChannelPoolConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_pool_base_names(self) -> Self:
        seen: set[str] = set()
        for pool in self.channel_pools:
            if pool.base_name in seen:
                raise ValueError(
                    f"{pool.base_name!r} duplicates another pool in guild {self.guild_id}"
                )
            seen.add(pool.base_name)
        seen_ids: set[str] = set()
        for pool in self.channel_pools:
            if pool.base_channel_id is None:
                continue
            if pool.base_channel_id in seen_ids:
                raise ValueError(
                    f"channel {pool.base_channel_id} is the base of more than one pool "
                    f"in guild {self.guild_id}"
                )
            seen_ids.add(pool.base_channel_id)
        return self

    def to_spec(self) -> GuildSpec:
        return GuildSpec(
            guild_id=self.guild_id,
            channel_pools=[pool.to_spec() for pool in self.channel_pools],
        )


class RuntimeConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    guilds: list[GuildConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_guild_ids(self) -> Self:
        seen: set[int] = set()
        for guild in self.guilds:
            if guild.guild_id in seen:
                raise ValueError(f"Duplicate guild_id: {guild.guild_id}")
            seen.add(guild.guild_id)
        return self


def parse_runtime_config_data(data: object) -> RuntimeConfig:
    try:
        config = RuntimeConfigFile.model_validate(data)
    except ValidationError as exc:
        raise RuntimeError(f"Invalid config: {exc}") from exc

    return RuntimeConfig(guilds={guild.guild_id: guild.to_spec() for guild in config.guilds})


def load_runtime_config() -> RuntimeConfig:
    config_path = Path(os.environ.get("BOTCHAN_CONFIG", DEFAULT_RUNTIME_CONFIG_PATH))
    return load_runtime_config_file(config_path)


def load_runtime_config_file(path: Path) -> RuntimeConfig:
    try:
        with path.open(encoding="utf-8") as config_file:
            data = json.load(config_file)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Config file is not valid JSON: {path}") from exc

    return parse_runtime_config_data(data)
