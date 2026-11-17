![BotChan avatar](botchan.png)

# BotChan

BotChan is a Discord voice-channel operator for game servers that need a small pool of overflow voice channels.

The original use case is an "Other Games" area: keep a minimum number of voice channels available, create more when groups fill them up, and remove unused extras later. The bot works like a small reconciliation loop: it observes each configured guild's current voice channels, compares them to the desired state for each configured channel pool, and applies the smallest create/delete actions needed to converge.

## How It Works

### Setting up a pool

A pool grows from a voice channel you create in Discord. Create the channel,
put it where you want it and set it up however you like: category,
permissions, bitrate, user limit, region, video quality. Then add a pool in
the editor and pick that channel as its base channel.

The bot copies the base channel's settings onto every other channel in the
pool and keeps them there, so the base channel is the only one you ever
configure. Change it later and the rest of the pool follows within a second.

The pool takes its name from the base channel, too. Rename the base channel in
Discord and the bot renames the rest of the pool to match.

If the base channel is deleted, the pool pauses: the bot stops creating,
deleting and syncing its channels, and the editor asks you to pick a new base
channel.

### Keeping the pool sized

Besides the base channel, the bot manages voice channels named after it:

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

- The bot only manages a pool's base channel and the voice channels named exactly `<base channel name> #N`.
- Matching channels are adopted as managed state, so restarts do not lose track of existing channels.
- Another channel that merely has the same name as a base channel is not part of the pool.
- Occupied channels are never deleted.
- Channels numbered `1..min` are never deleted.
- If duplicate managed channel numbers exist, such as two `Other Games #4` channels, the bot skips create/delete actions and logs a warning.
- The base channel is never edited, renamed or deleted by the bot.
- A pool whose base channel is gone is paused, not rebuilt.
- New channels are cloned from the base channel, including category, permissions, bitrate, user limit, RTC region, and video quality mode.
- Those settings are kept in sync afterwards: edit the base channel and the rest of the pool follows.
- A setting the bot cannot apply is attempted once, not retried in a loop.

## Configuration

Application config is read from environment variables. Runtime guild and channel-pool
specs are stored in PostgreSQL and edited through the configuration API.

Required:

```fish
set -gx DISCORD_TOKEN "replace-with-bot-token"
set -gx DATABASE_URL "postgresql+asyncpg://botchan:botchan@localhost/botchan"
```

Optional:

```fish
set -gx LOG_LEVEL "INFO"
```

`LOG_LEVEL` defaults to `INFO` when unset.

The stored configuration for a guild has this shape:

```json
{
  "guild_id": "123456789012345678",
  "channel_pools": [
    {
      "base_channel_id": "223456789012345678",
      "base_name": "🎮│Other Games",
      "min_channels": 3,
      "max_channels": 10,
      "idle_seconds": 300
    },
    {
      "base_channel_id": "223456789012345679",
      "base_name": "Raid Rooms",
      "min_channels": 1,
      "max_channels": 5,
      "idle_seconds": 600
    }
  ]
}
```

`base_name` is the base channel's name when the pool was last saved, and only
serves as a label: the bot always uses the channel's current name. Pools saved
before channels were picked have no `base_channel_id`. The bot finds their base
channel by name, and the editor matches them to it so the next save adds the ID.

Apply migrations before starting the bot locally:

```bash
uv run alembic upgrade head
uv run python -m botchan
```

Do not commit real bot tokens. If a token is ever exposed, rotate it in the Discord Developer Portal.

## Guild Configuration API and Editor

The separate FastAPI service serves the editor, authenticates administrators through
Discord OAuth2, lists servers where they have Manage Server permission, and stores
revisioned guild configuration in PostgreSQL. The editor is a Vue app built ahead of
time and served by the API itself (see [Frontends](#frontends)).

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

Or build the editor, apply migrations and launch the API directly:

```bash
(cd web && npm ci && npm run build)
uv run alembic upgrade head
uv run uvicorn botchan_api.main:app --reload
```

The service exposes two unauthenticated probe endpoints: `/healthz` reports that
the process is serving, and `/readyz` also checks that the database answers,
returning 503 when it does not.

Configuration saves use optimistic ETags and emit a PostgreSQL notification on
`botchan_config_changed` containing the guild ID and new revision. The database is
the source of truth; notifications are only reload hints and are not a durable queue.
The bot loads a complete snapshot at startup and after listener reconnections.

Which servers BotChan is in, and with what permissions, flows the other way. The
bot writes them to the `bot_guilds` table as it joins and leaves servers and as
its roles change, and rewrites the whole table every ten minutes to catch
anything it missed. Both APIs read that table instead of asking Discord, so a
server the bot was just added to can take a moment to show as installed. A
server the bot leaves keeps its row, marked as left.

## Admin API

A second, operator-only app, `botchan_api.admin.main:app`, lets the people who run
a BotChan instance see and edit every guild's configuration, including guilds they
are not members of and configs left behind after the bot was removed. It ships in
the same image as the configuration API but runs as its own process on its own host.

- Operators log in with Discord (identity only; no Discord token is kept). Only
  users in the `operators` table get a session, and that is checked again on every
  request.
- Nobody is an operator until you grant it:

  ```bash
  uv run python -m botchan_api.admin.cli grant <discord user id> --note "me"
  uv run python -m botchan_api.admin.cli list
  uv run python -m botchan_api.admin.cli revoke <discord user id>
  ```

- Saves made here are recorded in the guild's history with `as_operator` set.
- It serves an operator frontend at `/` (built from `web_admin/`): an overview with
  install and config counts plus recent changes across all guilds, a guild search
  (by name or ID prefix, paged on the server), and a page per guild with the config
  as read-only JSON that you switch to editing, its voice channel IDs, and every
  revision with view, diff and roll back.
- API: `/api/stats`, `/api/guilds?q=&status=&limit=&offset=`, `/api/guilds/{id}`,
  `/api/guilds/{id}/config` (GET/PUT), `/api/guilds/{id}/channels` and
  `/api/guilds/{id}/versions[/{revision}]`.

It needs the API's environment plus its own URLs, and the redirect URI must also be
registered for the Discord application:

```text
ADMIN_PUBLIC_BASE_URL=http://localhost:8001
ADMIN_DISCORD_REDIRECT_URI=http://localhost:8001/auth/discord/callback
ADMIN_SESSION_HOURS=12   # optional
```

`docker compose up` serves it on port 8001. Run the CLI there with
`docker compose run --rm --entrypoint python admin -m botchan_api.admin.cli ...`.

## Kubernetes

`k8s/base` holds a sample deployment to reference from your own kustomization,
and `k8s/example` shows a complete one using CloudNativePG for the database.
See [k8s/README.md](k8s/README.md).

## Discord Permissions

The bot needs enough permissions in the target guild to:

- Manage channels, to create and delete them.
- Manage roles, to copy permission overwrites from a pool's base channel to the
  rest of the pool. Discord treats editing channel permissions as a role
  operation, so without this the bot syncs everything except permissions and
  logs a warning.

If BotChan was added before it synced permissions, it holds Manage Channels
only. The editor shows a prompt for those servers; following it re-authorizes
the bot with the permissions it now needs.

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
uv run python -m compileall -q botchan botchan_api tests
uv run python -m unittest -v
(cd web && npm run type-check && npm test)
(cd web_admin && npm run type-check && npm test)
```

### Frontends

The guild editor (`web/`) and the operator console (`web_admin/`) are two
separate Vue 3 + TypeScript projects built with Vite. Each has its own
`package.json` and dependencies: the editor aims to be easy for server admins,
while the console is a dense tool for operators, so they are free to diverge.

The API serves each app's production build from its `dist/` folder (the Docker
image builds them for you). Outside Docker, build them before starting the API:

```bash
cd web && npm ci && npm run build
```

The API starts without a build, but then its page fails with a missing
directory error.

While working on a frontend, rebuild on every change and reload the page:

```bash
cd web && npm run build -- --watch      # with uvicorn botchan_api.main:app on :8000
cd web_admin && npm run build -- --watch  # with the admin app on :8001
```

`npm run dev` starts Vite's dev server instead (http://localhost:5173), with hot
reloading. It proxies `/api` and `/auth` to the API. Discord still sends you back
to the API's own address after logging in, but cookies are not tied to a port,
so once logged in, open the dev server's address again and the session carries
over (with `SECURE_COOKIES=false`, as plain-HTTP localhost needs).

Each app keeps its pure logic in `src/core.ts` with Vitest tests in
`src/core.test.ts`, its API calls in `src/api.ts` and its UI in `src/components/`.
The console routes with vue-router on hash history (`#/guilds/<id>`): the route
table is in `web_admin/src/routes.ts`, and the views load lazily.

## Important Limitations

- The bot only manages guilds with a configuration stored in PostgreSQL.
- The naming scheme is deterministic: the base channel's name for channel 1, then `#N` suffixes for channels 2 and up. The longest name, `<base> #<max>`, must fit Discord's 100 character limit, or the pool pauses.
- Extra channels are matched by name across the whole guild, so an unrelated voice channel named like one (`<base> #N`) is adopted into that pool.
- Renaming an extra channel in Discord removes it from its pool. The channel is left alone from then on, and the bot creates a replacement to restore the pool's minimum.
- Extras follow a base channel rename only while the bot is running. If the base channel is renamed while the bot is offline, the old extras keep their names and are no longer part of the pool.
- Discord allows two renames of a channel per ten minutes. If the base channel is renamed more often than that, the extras catch up once the limit allows, and the pool waits for them in the meantime.
- Empty extras always wait at least a minute before they are deleted, so people hopping in and out do not make the bot create and delete channels over and over. Configurations saved with a shorter timeout are treated as one minute.
- Discord allows a bot 2000 channel creations per server per day. If Discord holds a create back for more than 30 seconds, the bot stops creating channels in that server for ten minutes, leaving pools below their size; deleting and syncing carry on.
- Channel order inside a category is left to Discord after the base channel moves; only newly created channels are positioned explicitly.

## Upgrading

The bot must be upgraded before the API. An older bot does not know about
`base_channel_id`, so it ignores configurations saved by the newer editor and
keeps running on the ones it loaded before. Likewise, an API that reads
`bot_guilds` shows every server as not installed until a bot that writes the
table has started; it fills the table within seconds of connecting.

## License

Copyright (C) 2026 Björn Domars.

BotChan is free software under the [GNU Affero General Public License, version
3 or later](LICENSE). You may run, study, share and modify it. If you modify it
and let other people use it over a network, including as a hosted service, you
must offer those users the complete source of your modified version under the
same license.
