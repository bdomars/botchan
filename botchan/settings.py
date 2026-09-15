import logging
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BotConfig:
    token: str
    database_url: str
    log_level: int
    git_rev: str


def load_bot_config() -> BotConfig:
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is required")

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = logging.getLevelNamesMapping().get(log_level_name)
    if log_level is None:
        raise RuntimeError(f"Unknown LOG_LEVEL: {log_level_name}")

    git_rev = os.environ.get("BOTCHAN_GIT_REV", "unknown")
    return BotConfig(
        token=token,
        database_url=database_url,
        log_level=log_level,
        git_rev=git_rev,
    )
