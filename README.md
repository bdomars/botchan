![BotChan avatar](botchan.png)

# BotChan

BotChan is a Discord voice-channel operator for game servers that need a small pool of overflow voice channels.

The original use case is an "Other Games" area: keep a minimum number of voice channels available, create more when groups fill them up, and remove unused extras later. The bot works like a small reconciliation loop: it observes each configured guild's current voice channels, compares them to the desired state for each configured channel pool, and applies the smallest create/delete actions needed to converge.

## How It Works

The bot manages voice channels whose names match a configured channel-pool base name:

```text
🎮│Other Games
🎮│Other Games #2
🎮│Other Games #3
```

The desired number of channels is:

```text
occupied managed channels + 1 spare channel
```

That value is clamped between the pool's `min_channels` and `max_channels`.

For example, with `min=3` and `max=10`:

- If nobody is using the channels, the bot keeps channels 1 through 3.
- If channels 1, 2, and 3 are all occupied, the bot creates channel 4.
- If channels 1 through 4 are occupied, the bot creates channel 5.
- If extra channels become empty, the bot deletes the highest-numbered empty extras after the idle timeout.

Channels numbered at or below the pool's `min_channels` are protected. With `min=3`, the bot must never delete:

```text
🎮│Other Games
🎮│Other Games #2
🎮│Other Games #3
```

## Safety Rules

- The bot only manages voice channels whose names exactly match the configured naming scheme.
- Matching channels are adopted as managed state, so restarts do not lose track of existing channels.
- Occupied channels are never deleted.
- Channels numbered `1..min` are never deleted.
- If duplicate managed channel numbers exist, such as two `Other Games #4` channels, the bot skips create/delete actions and logs a warning.
- New channels are cloned from the base channel when possible, including category, permissions, bitrate, user limit, RTC region, and video quality mode.

## Configuration

Application config is read from environment variables. Runtime guild and channel-pool specs are read from a JSON config file.

Required:

```fish
set -gx DISCORD_TOKEN "replace-with-bot-token"
set -gx BOTCHAN_CONFIG "botchan.config.json"
```

Optional:

```fish
set -gx LOG_LEVEL "INFO"
```

`BOTCHAN_CONFIG` defaults to `botchan.config.json` when unset.
`LOG_LEVEL` defaults to `INFO` when unset.

Example runtime config:

```json
{
  "guilds": [
    {
      "guild_id": 123456789012345678,
      "channel_pools": [
        {
          "base_name": "🎮│Other Games",
          "min_channels": 3,
          "max_channels": 10,
          "idle_seconds": 300
        },
        {
          "base_name": "Raid Rooms",
          "min_channels": 1,
          "max_channels": 5,
          "idle_seconds": 600
        }
      ]
    }
  ]
}
```

See `botchan.config.example.json` for a starter file.

`env.fish` contains a fish shell template for local testing:

```fish
source env.fish
uv run python -m botchan
```

Do not commit real bot tokens. If a token is ever exposed, rotate it in the Discord Developer Portal.

## Guild Configuration API and Editor

The separate FastAPI service serves the editor, authenticates administrators through
Discord OAuth2, lists servers where they have Manage Server permission, and stores
revisioned guild configuration in PostgreSQL. The page loads Vue from a CDN, so the
browser also needs a network connection.

Required API environment variables:

```text
DATABASE_URL=postgresql+asyncpg://botchan:botchan@localhost/botchan
DISCORD_CLIENT_ID=...
DISCORD_CLIENT_SECRET=...
DISCORD_TOKEN=...
DISCORD_REDIRECT_URI=http://localhost:8000/auth/discord/callback
PUBLIC_BASE_URL=http://localhost:8000
SESSION_SECRET=...
TOKEN_ENCRYPTION_KEY=...
SECURE_COOKIES=false
```

Generate the encryption key with:

```bash
uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

For local development, export the secrets above and run:

```bash
docker compose up --build
```

Or apply migrations and launch the API directly:

```bash
uv run alembic upgrade head
uv run uvicorn botchan_api.main:app --reload
```

Configuration saves use optimistic ETags and emit a PostgreSQL notification on
`botchan_config_changed` containing the guild ID and new revision. The database is
the source of truth; notifications are only reload hints and are not a durable queue.

## Discord Permissions

The bot needs enough permissions in the target guild to:

- Manage channels.

It also needs Discord gateway intents for guilds and voice states. The code enables those intents when creating the bot client.

## Development

Install and run through `uv`:

```bash
uv sync
uv run python -m botchan
```

Run checks:

```bash
uv run ty check
uv run python -m compileall -q botchan botchan_api test_*.py
uv run python -m unittest -v
node --test web/test_config_core.js
```

## Important Limitations

- The bot only manages guilds listed in the config file.
- The naming scheme is deterministic: base channel name for channel 1, then `#N` suffixes for channels 2 and up.
- The bot needs at least one existing matching channel to use as a template for creating more channels.
