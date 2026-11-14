"""Reading and writing guild configurations, shared by the user and admin apps."""

from __future__ import annotations

import json
from typing import Any, Self

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from botchan.config import MIN_IDLE_SECONDS, ChannelPoolConfig, GuildConfig
from botchan.reconciliation import pool_name_problems
from botchan_api.common import error, now
from botchan_api.guilds import guild_voice_channels
from botchan_api.models import GuildConfigRow, GuildConfigVersion


class ConfigWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    channel_pools: list[ChannelPoolConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_base_channels(self) -> Self:
        # Names are filled in from Discord on save; the channel is the identity.
        ids = [pool.base_channel_id for pool in self.channel_pools]
        if any(channel_id is None for channel_id in ids):
            raise ValueError("every channel pool needs a base_channel_id")
        if len(ids) != len(set(ids)):
            raise ValueError("a channel can be the base of only one pool")
        if any(pool.idle_seconds < MIN_IDLE_SECONDS for pool in self.channel_pools):
            raise ValueError(f"idle_seconds must be at least {MIN_IDLE_SECONDS}")
        return self


def config_etag(revision: int) -> str:
    return f'"guild-config-{revision}"'


def config_response(guild_id: str, row: GuildConfigRow | None) -> JSONResponse:
    """A guild's current configuration, with the ETag a later save must send."""
    if row is None:
        return JSONResponse(
            {"guild_id": guild_id, "channel_pools": [], "revision": None, "updated_at": None}
        )
    response = JSONResponse(
        {
            "guild_id": guild_id,
            "channel_pools": row.document["channel_pools"],
            "revision": row.revision,
            "updated_at": row.updated_at.isoformat(),
        }
    )
    response.headers["ETag"] = config_etag(row.revision)
    return response


async def save_config(
    db: AsyncSession,
    discord: Any,
    guild_id: str,
    body: ConfigWrite,
    *,
    editor_id: str,
    editor_name: str,
    if_match: str | None,
    if_none_match: str | None,
    as_operator: bool = False,
) -> JSONResponse:
    """Validate and store a new revision, and tell the bot about it.

    The caller has already decided that the editor may change this guild.
    """
    channel_names = {
        channel["id"]: channel["name"] for channel in await guild_voice_channels(discord, guild_id)
    }
    pools = []
    for pool in body.channel_pools:
        name = channel_names.get(pool.base_channel_id or "")
        if name is None:
            raise error(
                422,
                "BASE_CHANNEL_NOT_FOUND",
                "A pool's base channel is no longer a voice channel in this server. "
                "Pick another one.",
            )
        # The stored name is what the editor shows; the bot always uses the
        # channel's current name.
        pools.append(pool.model_copy(update={"base_name": name}))
    problems = pool_name_problems([pool.to_spec() for pool in pools])
    if problems:
        raise error(422, "POOL_NAMES_CONFLICT", " ".join(problems))
    try:
        validated = GuildConfig.model_validate(
            {"guild_id": int(guild_id), "channel_pools": [pool.model_dump() for pool in pools]}
        )
    except ValidationError as exc:
        raise error(422, "INVALID_POOLS", str(exc)) from exc
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:guild_id, 0))"),
        {"guild_id": guild_id},
    )
    row = await db.scalar(
        select(GuildConfigRow).where(GuildConfigRow.guild_id == guild_id).with_for_update()
    )
    if row is None:
        if if_none_match != "*":
            if if_match is not None:
                raise error(412, "CONFIG_STALE", "The guild configuration has changed.")
            raise error(428, "PRECONDITION_REQUIRED", "Reload before saving this guild.")
        revision = 1
    else:
        if if_match is None and if_none_match is None:
            raise error(428, "PRECONDITION_REQUIRED", "Reload before saving this guild.")
        if if_match != config_etag(row.revision):
            raise error(412, "CONFIG_STALE", "The guild configuration has changed.")
        revision = row.revision + 1

    timestamp = now()
    document = {
        "guild_id": guild_id,
        "channel_pools": [pool.model_dump() for pool in validated.channel_pools],
    }
    if row is None:
        row = GuildConfigRow(
            guild_id=guild_id,
            document=document,
            revision=revision,
            editor_discord_user_id=editor_id,
            updated_at=timestamp,
        )
        db.add(row)
        await db.flush()
    else:
        row.document = document
        row.revision = revision
        row.editor_discord_user_id = editor_id
        row.updated_at = timestamp
    db.add(
        GuildConfigVersion(
            guild_id=guild_id,
            revision=revision,
            document=document,
            editor_discord_user_id=editor_id,
            editor_name=editor_name,
            as_operator=as_operator,
            created_at=timestamp,
        )
    )
    payload = json.dumps({"guild_id": guild_id, "revision": revision}, separators=(",", ":"))
    await db.execute(
        text("SELECT pg_notify('botchan_config_changed', :payload)"), {"payload": payload}
    )
    await db.commit()
    return config_response(guild_id, row)
