![BotChan avatar](botchan.png)

# BotChan

BotChan is a Discord voice-channel operator for game servers that need a small pool of overflow voice channels.

The original use case is an "Other Games" area: keep a minimum number of voice channels available, create more when groups fill them up, and remove unused extras later. The bot works like a small reconciliation loop: it observes the guild's current voice channels, compares them to the desired state, and applies the smallest create/delete actions needed to converge.

## How It Works

The bot manages voice channels whose names match a configured base name:

```text
🎮│Other Games
🎮│Other Games #2
🎮│Other Games #3
```

The desired number of channels is:

```text
occupied managed channels + 1 spare channel
```

That value is clamped between `OTHER_GAMES_MIN_CHANNELS` and `OTHER_GAMES_MAX_CHANNELS`.

For example, with `min=3` and `max=10`:

- If nobody is using the channels, the bot keeps channels 1 through 3.
- If channels 1, 2, and 3 are all occupied, the bot creates channel 4.
- If channels 1 through 4 are occupied, the bot creates channel 5.
- If extra channels become empty, the bot deletes the highest-numbered empty extras after the idle timeout.

Channels numbered at or below `OTHER_GAMES_MIN_CHANNELS` are protected. With `min=3`, the bot must never delete:

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

Configuration is read from environment variables.

Required:

```fish
set -gx DISCORD_TOKEN "replace-with-bot-token"
set -gx GUILD_ID "replace-with-guild-id"
```

Optional:

```fish
set -gx OTHER_GAMES_BASE_NAME "🎮│Other Games"
set -gx OTHER_GAMES_MIN_CHANNELS "3"
set -gx OTHER_GAMES_MAX_CHANNELS "10"
set -gx OTHER_GAMES_IDLE_SECONDS "300"
```

`env.fish` contains a fish shell template for local testing:

```fish
source env.fish
uv run python main.py
```

Do not commit real bot tokens. If a token is ever exposed, rotate it in the Discord Developer Portal.

## Discord Permissions

The bot needs enough permissions in the target guild to:

- View channels.
- Read voice state.
- Manage channels.
- Create voice channels.
- Delete voice channels.

It also needs Discord gateway intents for guilds and voice states. The code enables those intents when creating the bot client.

## Development

Install and run through `uv`:

```bash
uv sync
uv run python main.py
```

Run checks:

```bash
uv run ty check
uv run python -m py_compile bot.py test_bot.py main.py
uv run python -m unittest -v
```

## Important Limitations

- The bot currently manages one guild, selected by `GUILD_ID`.
- The naming scheme is deterministic: base channel name for channel 1, then `#N` suffixes for channels 2 and up.
- The bot needs at least one existing matching channel to use as a template for creating more channels.
