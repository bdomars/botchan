# CLAUDE.md

Guidance for Claude Code when working in this repository. The README is the
user-facing documentation and the source of truth for behaviour; read it before
changing anything that affects how pools behave.

## What this is

BotChan is a Discord bot that keeps pools of overflow voice channels sized to
demand, plus a web editor for server admins and an operator console. Three
deployables share one PostgreSQL database:

| Part | Entry point | Image | Purpose |
| --- | --- | --- | --- |
| Bot | `python -m botchan` | `Dockerfile` | Reconciles each guild's voice channels against its pool specs |
| Config API + editor | `botchan_api.main:app` | `Dockerfile.api` | Discord OAuth2, Manage Server check, revisioned config storage, serves `web/dist` |
| Admin API + console | `botchan_api.admin.main:app` | `Dockerfile.api` (same image) | Operator-only view/edit of every guild, serves `web_admin/dist` |

## Layout

- `botchan/` – the bot.
  - `reconciliation.py` – pure planning logic (desired count, which channels to
    create/delete, settings diff via `SYNCED_ATTRIBUTES`). Uses `Protocol`s so it
    is testable without discord.py objects. Keep it pure.
  - `client.py` – `BotChan(commands.Bot)`: Discord events, a per-guild
    `asyncio.Lock` (`_guild_lock`), applying plans, rename queueing, pausing pools,
    creation back-off.
  - `database_config.py` – `PostgresConfigSource`: loads a full snapshot at start
    and after reconnects, `LISTEN`s on `botchan_config_changed` for reload hints.
  - `config.py` (specs, name limits), `rate_limits.py`, `settings.py` (env).
- `botchan_api/` – FastAPI.
  - `app.py` user-facing app, `admin/` operator app (+ `admin/cli.py` for
    granting operators), `common.py` HTTP plumbing shared by both, `configs.py`
    save/ETag/`pg_notify`, `models.py` SQLAlchemy, `security.py` token
    encryption, `discord_client.py`, `guilds.py`.
- `migrations/` – Alembic (`0001_backend`, `0002_admin`). Schema changes always
  get a new migration.
- `tests/` – a package; `unittest`, not pytest. `test_*_integration.py` need
  `BOTCHAN_TEST_DATABASE_URL` and are skipped without it.
- `web/` (guild editor) and `web_admin/` (operator console) – two **independent**
  Vue 3 + TS + Vite projects, each with its own lockfile. They are allowed to
  diverge; don't factor shared code between them. Pattern in each: pure logic in
  `src/core.ts` with Vitest tests in `src/core.test.ts`, API calls in
  `src/api.ts`, UI in `src/components/`, state in `src/composables/`. The console
  uses vue-router with hash history (`web_admin/src/routes.ts`, lazy views).
- `k8s/` – kustomize `base`, `components/admin`, and an `example` using
  CloudNativePG.
- `.github/workflows/` – CI: test → publish both images → deploy (pins image
  tags *and* `k8s/` refs to the same commit in the infra repo).

## Key invariants

These come from the README's Safety Rules and Limitations; don't weaken them
without being asked:

- Only the base channel and channels named exactly `<base name> #N` are managed.
- Never delete occupied channels or channels numbered `1..min_channels`.
- Never edit, rename or delete the base channel. Missing base channel ⇒ pool
  pauses, not rebuilt. Duplicate `#N` numbers ⇒ skip create/delete and warn.
- A setting that can't be applied is attempted once, not retried in a loop.
- The database is the source of truth; notifications are only hints.
- Config saves use optimistic concurrency (ETag = revision).
- Bot and API share the config document, schema and notification payload, so
  they deploy together, and the bot must be upgraded before the API (older bots
  ignore configs with fields they don't know).
- One failing guild must not break reconciliation of the others.

## Commands

```bash
uv sync
uv run alembic upgrade head
uv run python -m botchan
uv run uvicorn botchan_api.main:app --reload             # :8000
uv run uvicorn botchan_api.admin.main:app --port 8001    # admin
docker compose up --build                                # everything, incl. Postgres
```

Full check suite (same as CI) – run all of it before reporting a change as done:

```bash
uv run ruff check && uv run ruff format --check     # line length 100
uv run ty check
uv run python -m compileall -q botchan botchan_api tests
uv run python -m unittest -v
(cd web && npm run type-check && npm test && npm run build)
(cd web_admin && npm run type-check && npm test && npm run build)
```

Some Python tests check that the API serves the built pages, so build the
frontends before running them. For the integration tests, point
`BOTCHAN_TEST_DATABASE_URL` at a throwaway Postgres (e.g. the compose one) and
run migrations against it first. If an integration test was skipped, say so
rather than reporting the suite as green.

## How we work

- **Never commit.** Leave changes in the working tree and report what changed;
  the user makes the commits. Don't offer to commit as a next step either. Other
  git commands (status, diff, branching) are fine.
- **Developer-only code never touches `main`.** Local conveniences (e.g. a
  "backdoor" letting the developer's own Discord user see every guild) live on a
  separate branch that is rebased, never merged, and in locally built images
  that are never pushed. Gate them on a hardcoded user ID on that branch. Don't
  propose env-var/feature-flag-off-by-default code on `main` as a substitute:
  the requirement is "absent from what ships", not "inert when disabled". Keep
  such branches touching as few shared files as possible.
- **Keep the README in step with behaviour.** Any user-visible change (pool
  behaviour, env vars, endpoints, commands, limitations) gets a README update in
  the same change. Write it in plain prose for server admins and operators: say
  what happens and why, no marketing tone or filler.
- **Comments explain why**, not what – constraints, Discord quirks, why an
  alternative was rejected. Match the existing style.
- **Test pure logic first.** New planning rules go into `reconciliation.py` with
  tests in `tests/test_botchan_reconciliation.py`; frontend logic goes into
  `core.ts` with Vitest tests, keeping components thin.
- **Small, focused changes.** Prefer minimal diffs over speculative abstraction,
  don't add dependencies casually, and keep the two frontends separate.
- **Secrets**: never commit tokens or `.env`; real config comes from env vars.
- Python ≥ 3.14, `uv` for everything (no bare `pip`), Node 24 for frontends.
