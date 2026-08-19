from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

