from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OAuthSession(Base):
    __tablename__ = "oauth_sessions"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    discord_user_id: Mapped[str] = mapped_column(String(20), index=True)
    username: Mapped[str] = mapped_column(String(128))
    global_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    avatar: Mapped[str | None] = mapped_column(String(128), nullable=True)
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str] = mapped_column(Text)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    csrf_token: Mapped[str] = mapped_column(String(128))
    guild_cache: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    guild_cache_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class GuildConfigRow(Base):
    __tablename__ = "guild_configs"

    guild_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(Integer)
    editor_discord_user_id: Mapped[str] = mapped_column(String(20))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GuildConfigVersion(Base):
    __tablename__ = "guild_config_versions"

    guild_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("guild_configs.guild_id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB)
    editor_discord_user_id: Mapped[str] = mapped_column(String(20), index=True)
    editor_name: Mapped[str] = mapped_column(String(128))
    # Set when a BotChan operator, not one of the guild's own managers, saved it.
    as_operator: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Operator(Base):
    """A Discord user allowed into the admin app. Managed with botchan_api.admin.cli."""

    __tablename__ = "operators"

    discord_user_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AdminSession(Base):
    """An admin app login. It holds no Discord tokens: guild data comes from the bot."""

    __tablename__ = "admin_sessions"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    discord_user_id: Mapped[str] = mapped_column(String(20), index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    csrf_token: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
