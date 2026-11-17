"""The operator-only admin API.

It runs as its own process on its own host. Logging in uses the same Discord
application as the user app, but only Discord users listed in the
``operators`` table get a session, and that listing is checked again on every
request, so revoking an operator takes effect immediately.

Guild routes mirror the user API's paths and config shapes. Listing is
searched and paged on the server, so the frontend never needs every guild at
once. The guild list comes from the ``bot_guilds`` table the bot maintains, so
listing makes no Discord requests.

The operator frontend, built from ``web_admin/`` into ``web_admin/dist/``, is served from ``/``.
"""

import logging
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from botchan_api.admin.settings import AdminSettings
from botchan_api.common import (
    APIError,
    error,
    install_common,
    install_probes,
    now,
    security_compare,
    session_dependency,
)
from botchan_api.configs import ConfigWrite, config_response, save_config
from botchan_api.database import create_database
from botchan_api.discord_client import DiscordAPIError, DiscordClient
from botchan_api.guilds import (
    guild_voice_channels,
    has_bot_permissions,
    icon_url,
    install_url,
    installed_guilds,
    is_installed,
    valid_snowflake,
)
from botchan_api.models import AdminSession, BotGuild, GuildConfigRow, GuildConfigVersion, Operator
from botchan_api.security import Security

ADMIN_SESSION_COOKIE = "botchan_admin_session"
ADMIN_OAUTH_STATE_COOKIE = "botchan_admin_oauth_state"
ADMIN_STATE_SALT = "botchan-admin-oauth-state"
MAX_VERSIONS_PAGE = 200
MAX_GUILDS_PAGE = 200
RECENT_CHANGES = 15
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# The console's production build: `npm run build` in web_admin/ writes it.
ADMIN_WEB_ROOT = PROJECT_ROOT / "web_admin" / "dist"
log = logging.getLogger("botchan.admin")


class GuildFilter(StrEnum):
    ALL = "all"
    CONFIGURED = "configured"
    UNCONFIGURED = "unconfigured"
    NEEDS_PERMISSIONS = "needs_permissions"
    ORPHANED = "orphaned"


def matches_filter(summary: dict[str, Any], status: GuildFilter) -> bool:
    match status:
        case GuildFilter.ALL:
            return True
        case GuildFilter.CONFIGURED:
            return summary["installed"] and summary["configured"]
        case GuildFilter.UNCONFIGURED:
            return summary["installed"] and not summary["configured"]
        case GuildFilter.NEEDS_PERMISSIONS:
            return summary["needs_permissions"]
        case GuildFilter.ORPHANED:
            return not summary["installed"]


def matches_query(summary: dict[str, Any], query: str) -> bool:
    """IDs match by prefix, names by case-insensitive substring."""
    query = query.strip()
    if not query:
        return True
    if query.isdigit() and summary["id"].startswith(query):
        return True
    return query.casefold() in (summary["name"] or "").casefold()


def create_admin_app(settings: AdminSettings | None = None, discord: Any = None) -> FastAPI:
    settings = settings or AdminSettings.from_env()
    base = settings.base
    engine, session_factory = create_database(base)
    security = Security(base.session_secret, base.token_encryption_key, ADMIN_STATE_SALT)
    discord = discord or DiscordClient(base)

    app = FastAPI(title="BotChan Admin API", version="0.1.0")
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.security = security
    app.state.discord = discord

    install_common(app)
    install_probes(app, session_factory, log)
    db_session = session_dependency(session_factory)
    DB = Annotated[AsyncSession, Depends(db_session)]

    async def require_operator(request: Request, db: DB) -> AdminSession:
        raw = request.cookies.get(ADMIN_SESSION_COOKIE)
        if not raw:
            raise error(401, "AUTHENTICATION_REQUIRED", "Log in with Discord first.")
        admin_session = await db.get(AdminSession, security.session_hash(raw))
        if admin_session is None or admin_session.expires_at <= now():
            if admin_session is not None:
                await db.delete(admin_session)
                await db.commit()
            raise error(401, "SESSION_EXPIRED", "Your session has expired.")
        if await db.get(Operator, admin_session.discord_user_id) is None:
            await db.delete(admin_session)
            await db.commit()
            raise error(403, "NOT_AN_OPERATOR", "You are not a BotChan operator.")
        return admin_session

    OperatorSession = Annotated[AdminSession, Depends(require_operator)]

    def check_csrf(request: Request, admin_session: AdminSession) -> None:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not supplied or not security_compare(supplied, admin_session.csrf_token):
            raise error(403, "CSRF_REJECTED", "The request could not be verified.")

    def checked_guild_id(guild_id: str) -> str:
        if not valid_snowflake(guild_id):
            raise error(404, "GUILD_NOT_FOUND", "Guild not found.")
        return guild_id

    async def require_installed(db: AsyncSession, guild_id: str) -> None:
        if not await is_installed(db, guild_id):
            raise error(409, "BOT_NOT_INSTALLED", "BotChan is not in this guild.")

    def summarize(guild_id: str, guild: BotGuild | None, row: Any | None) -> dict[str, Any]:
        """One guild as the admin views show it. ``row`` needs guild_id, revision, updated_at.

        ``guild`` is None unless the bot is in the guild now.
        """
        return {
            "id": guild_id,
            "name": guild.name if guild else None,
            "icon_url": icon_url(guild_id, guild.icon) if guild else None,
            "member_count": guild.member_count if guild else None,
            "installed": guild is not None,
            "needs_permissions": guild is not None and not has_bot_permissions(guild.permissions),
            "configured": row is not None,
            "revision": row.revision if row else None,
            "updated_at": row.updated_at.isoformat() if row else None,
            "install_url": install_url(base.discord_client_id, guild_id),
        }

    async def all_summaries(db: AsyncSession) -> list[dict[str, Any]]:
        """Every guild the bot is in plus every stored config, named guilds first."""
        installed = await installed_guilds(db)
        rows = {
            row.guild_id: row
            for row in (
                await db.execute(
                    select(
                        GuildConfigRow.guild_id,
                        GuildConfigRow.revision,
                        GuildConfigRow.updated_at,
                    )
                )
            ).all()
        }
        result = [
            summarize(guild_id, installed.get(guild_id), rows.get(guild_id))
            for guild_id in installed.keys() | rows.keys()
        ]

        # Named guilds alphabetically, then guilds the bot has left.
        def sort_key(item: dict[str, Any]) -> tuple[bool, str, str]:
            name = item["name"]
            return (name is None, str(name or "").casefold(), str(item["id"]))

        result.sort(key=sort_key)
        return result

    @app.get("/auth/discord")
    async def discord_login() -> RedirectResponse:
        state = security.random_token()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": base.discord_client_id,
                # Identity only: guild data comes from the bot, not the operator.
                "scope": "identify",
                "state": state,
                "redirect_uri": settings.discord_redirect_uri,
            }
        )
        response = RedirectResponse(f"https://discord.com/oauth2/authorize?{query}")
        response.set_cookie(
            ADMIN_OAUTH_STATE_COOKIE,
            security.sign_oauth_state(state),
            max_age=600,
            httponly=True,
            secure=base.secure_cookies,
            samesite="lax",
        )
        return response

    @app.get("/auth/discord/callback")
    async def discord_callback(
        request: Request,
        db: DB,
        code: str = "",
        state: str = "",
        error_name: Annotated[str, Query(alias="error")] = "",
    ) -> RedirectResponse:
        def fail(reason: str) -> RedirectResponse:
            response = RedirectResponse(f"/?auth_error={reason}", status_code=303)
            response.delete_cookie(ADMIN_OAUTH_STATE_COOKIE)
            return response

        signed_state = request.cookies.get(ADMIN_OAUTH_STATE_COOKIE, "")
        if (
            error_name
            or not code
            or not state
            or not security.verify_oauth_state(signed_state, state)
        ):
            return fail("oauth_failed")
        try:
            token = await discord.exchange_code(code, settings.discord_redirect_uri)
            user = await discord.current_user(token["access_token"])
        except DiscordAPIError, KeyError:
            return fail("discord_unavailable")
        # The Discord token was only needed to learn who this is.
        try:
            await discord.revoke(token["access_token"])
        except DiscordAPIError:
            log.warning("Revoking an admin login's Discord token failed")

        user_id = str(user["id"])
        if await db.get(Operator, user_id) is None:
            log.warning("Discord user %s tried to log in to the admin app", user_id)
            return fail("not_operator")

        raw_session = security.random_token()
        timestamp = now()
        db.add(
            AdminSession(
                id_hash=security.session_hash(raw_session),
                discord_user_id=user_id,
                display_name=user.get("global_name") or user["username"],
                csrf_token=security.random_token(),
                created_at=timestamp,
                expires_at=timestamp + timedelta(hours=settings.session_hours),
            )
        )
        await db.commit()
        log.info("Operator %s logged in to the admin app", user_id)
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(ADMIN_OAUTH_STATE_COOKIE)
        response.set_cookie(
            ADMIN_SESSION_COOKIE,
            raw_session,
            max_age=settings.session_hours * 3600,
            httponly=True,
            secure=base.secure_cookies,
            samesite="strict",
        )
        return response

    @app.get("/api/session")
    async def session_info(request: Request, db: DB) -> dict[str, Any]:
        try:
            admin_session = await require_operator(request, db)
        except APIError:
            return {"authenticated": False}
        return {
            "authenticated": True,
            "operator": True,
            "csrf_token": admin_session.csrf_token,
            "user": {
                "id": admin_session.discord_user_id,
                "name": admin_session.display_name,
                "avatar_url": None,
            },
        }

    @app.post("/auth/logout")
    async def logout(request: Request, db: DB, admin_session: OperatorSession) -> JSONResponse:
        check_csrf(request, admin_session)
        await db.delete(admin_session)
        await db.commit()
        response = JSONResponse({"ok": True})
        response.delete_cookie(ADMIN_SESSION_COOKIE)
        return response

    @app.get("/api/guilds")
    async def guild_list(
        db: DB,
        _operator: OperatorSession,
        q: Annotated[str, Query(max_length=100)] = "",
        status: GuildFilter = GuildFilter.ALL,
        limit: Annotated[int, Query(ge=1, le=MAX_GUILDS_PAGE)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Guilds the bot is in and guilds with a stored config, searched and paged.

        ``q`` matches a guild ID by prefix or a name by substring. A config
        whose guild the bot has left has no name, and sorts last.
        """
        matching = [
            summary
            for summary in await all_summaries(db)
            if matches_filter(summary, status) and matches_query(summary, q)
        ]
        return {
            "guilds": matching[offset : offset + limit],
            "total": len(matching),
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/guilds/{guild_id}")
    async def guild_detail(guild_id: str, db: DB, _operator: OperatorSession) -> dict[str, Any]:
        checked_guild_id(guild_id)
        guild = (await installed_guilds(db, [guild_id])).get(guild_id)
        row = (
            await db.execute(
                select(
                    GuildConfigRow.guild_id, GuildConfigRow.revision, GuildConfigRow.updated_at
                ).where(GuildConfigRow.guild_id == guild_id)
            )
        ).first()
        if guild is None and row is None:
            raise error(
                404, "GUILD_NOT_FOUND", "BotChan is not in this guild and has no config for it."
            )
        return summarize(guild_id, guild, row)

    @app.get("/api/stats")
    async def stats(db: DB, _operator: OperatorSession) -> dict[str, Any]:
        summaries = await all_summaries(db)
        installed = [summary for summary in summaries if summary["installed"]]
        week_ago = now() - timedelta(days=7)
        revisions, by_operator, last_week = (
            await db.execute(
                select(
                    func.count(),
                    func.count().filter(GuildConfigVersion.as_operator),
                    func.count().filter(GuildConfigVersion.created_at >= week_ago),
                )
            )
        ).one()
        pools = await db.scalar(
            select(
                func.coalesce(
                    func.sum(func.jsonb_array_length(GuildConfigRow.document["channel_pools"])), 0
                )
            )
        )
        names = {summary["id"]: summary["name"] for summary in summaries}
        recent = (
            await db.scalars(
                select(GuildConfigVersion)
                .order_by(GuildConfigVersion.created_at.desc())
                .limit(RECENT_CHANGES)
            )
        ).all()
        return {
            "guilds": {
                "installed": len(installed),
                "configured": sum(1 for s in installed if s["configured"]),
                "unconfigured": sum(1 for s in installed if not s["configured"]),
                "needs_permissions": sum(1 for s in installed if s["needs_permissions"]),
                "orphaned": sum(1 for s in summaries if not s["installed"]),
                "members": sum(s["member_count"] or 0 for s in installed),
            },
            "pools": int(pools or 0),
            "revisions": {
                "total": revisions,
                "by_operator": by_operator,
                "last_7_days": last_week,
            },
            "operators": await db.scalar(select(func.count()).select_from(Operator)),
            "recent_changes": [
                {
                    "guild_id": version.guild_id,
                    "guild_name": names.get(version.guild_id),
                    "revision": version.revision,
                    "editor": {
                        "id": version.editor_discord_user_id,
                        "name": version.editor_name,
                    },
                    "as_operator": version.as_operator,
                    "created_at": version.created_at.isoformat(),
                }
                for version in recent
            ],
        }

    @app.get("/api/guilds/{guild_id}/config")
    async def get_config(guild_id: str, db: DB, _operator: OperatorSession) -> JSONResponse:
        checked_guild_id(guild_id)
        return config_response(guild_id, await db.get(GuildConfigRow, guild_id))

    @app.get("/api/guilds/{guild_id}/channels")
    async def channel_list(guild_id: str, db: DB, _operator: OperatorSession) -> dict[str, Any]:
        checked_guild_id(guild_id)
        await require_installed(db, guild_id)
        return {"channels": await guild_voice_channels(discord, guild_id)}

    @app.put("/api/guilds/{guild_id}/config")
    async def put_config(
        guild_id: str,
        body: ConfigWrite,
        request: Request,
        db: DB,
        admin_session: OperatorSession,
        if_match: Annotated[str | None, Header()] = None,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        check_csrf(request, admin_session)
        checked_guild_id(guild_id)
        await require_installed(db, guild_id)
        response = await save_config(
            db,
            discord,
            guild_id,
            body,
            editor_id=admin_session.discord_user_id,
            editor_name=admin_session.display_name,
            if_match=if_match,
            if_none_match=if_none_match,
            as_operator=True,
        )
        log.info(
            "Operator %s saved guild %s as %s",
            admin_session.discord_user_id,
            guild_id,
            response.headers.get("ETag"),
        )
        return response

    @app.get("/api/guilds/{guild_id}/versions")
    async def version_list(
        guild_id: str,
        db: DB,
        _operator: OperatorSession,
        before: Annotated[int | None, Query(ge=1)] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_VERSIONS_PAGE)] = 50,
    ) -> dict[str, Any]:
        """A guild's saved revisions, newest first. Pass ``before`` for older ones."""
        checked_guild_id(guild_id)
        query = select(GuildConfigVersion).where(GuildConfigVersion.guild_id == guild_id)
        if before is not None:
            query = query.where(GuildConfigVersion.revision < before)
        versions = (
            await db.scalars(query.order_by(GuildConfigVersion.revision.desc()).limit(limit))
        ).all()
        return {
            "versions": [
                {
                    "revision": version.revision,
                    "editor": {
                        "id": version.editor_discord_user_id,
                        "name": version.editor_name,
                    },
                    "as_operator": version.as_operator,
                    "pool_count": len(version.document.get("channel_pools", [])),
                    "created_at": version.created_at.isoformat(),
                }
                for version in versions
            ],
            "next_before": versions[-1].revision if len(versions) == limit else None,
        }

    @app.get("/api/guilds/{guild_id}/versions/{revision}")
    async def version_detail(
        guild_id: str, revision: int, db: DB, _operator: OperatorSession
    ) -> dict[str, Any]:
        """One stored revision in full. Rolling back is a PUT of its channel_pools."""
        checked_guild_id(guild_id)
        version = await db.get(GuildConfigVersion, (guild_id, revision))
        if version is None:
            raise error(404, "VERSION_NOT_FOUND", "That revision does not exist.")
        return {
            "guild_id": guild_id,
            "revision": version.revision,
            "channel_pools": version.document["channel_pools"],
            "editor": {"id": version.editor_discord_user_id, "name": version.editor_name},
            "as_operator": version.as_operator,
            "created_at": version.created_at.isoformat(),
        }

    @app.get("/botchan-192.png", include_in_schema=False)
    async def logo() -> FileResponse:
        return FileResponse(PROJECT_ROOT / "botchan-192.png")

    # Last, so the API and auth routes above take precedence.
    # check_dir=False, as in botchan_api/app.py: the API starts without a build.
    app.mount(
        "/",
        StaticFiles(directory=ADMIN_WEB_ROOT, html=True, check_dir=False),
        name="admin-web",
    )
    return app
