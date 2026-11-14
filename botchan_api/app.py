import logging
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from botchan_api.common import (
    APIError,
    error,
    install_common,
    install_probes,
    now,
    security_compare,
    session_dependency,
)
from botchan_api.configs import ConfigWrite, config_etag, config_response, save_config
from botchan_api.database import create_database
from botchan_api.discord_client import DiscordAPIError, DiscordClient
from botchan_api.guilds import (
    ADMINISTRATOR,
    BOT_GUILD_CACHE_SECONDS,
    BOT_PERMISSIONS,
    CATEGORY_CHANNEL,
    MANAGE_CHANNELS,
    MANAGE_GUILD,
    MANAGE_ROLES,
    VOICE_CHANNEL,
    TimedCache,
    guild_voice_channels,
    has_bot_permissions,
    icon_url,
    install_url,
    valid_snowflake,
    voice_channel_list,
)
from botchan_api.models import GuildConfigRow, OAuthSession
from botchan_api.security import Security
from botchan_api.settings import Settings

# Re-exported for callers and tests that import them from here.
__all__ = [
    "ADMINISTRATOR",
    "BOT_GUILD_CACHE_SECONDS",
    "BOT_PERMISSIONS",
    "CATEGORY_CHANNEL",
    "MANAGE_CHANNELS",
    "MANAGE_GUILD",
    "MANAGE_ROLES",
    "VOICE_CHANNEL",
    "APIError",
    "ConfigWrite",
    "config_etag",
    "create_app",
    "has_bot_permissions",
    "now",
    "valid_snowflake",
    "voice_channel_list",
]

SESSION_COOKIE = "botchan_session"
OAUTH_STATE_COOKIE = "botchan_oauth_state"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# The editor's production build: `npm run build` in web/ writes it.
WEB_ROOT = PROJECT_ROOT / "web" / "dist"
log = logging.getLogger("botchan.api")


def create_app(
    settings: Settings | None = None,
    discord: Any = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    engine, session_factory = create_database(settings)
    security = Security(settings.session_secret, settings.token_encryption_key)
    discord = discord or DiscordClient(settings)

    app = FastAPI(title="BotChan API", version="0.1.0")
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.security = security
    app.state.discord = discord

    install_common(app)
    install_probes(app, session_factory, log)
    bot_guilds: TimedCache[dict[str, int]] = TimedCache(lambda: discord.bot_guilds())
    db_session = session_dependency(session_factory)

    DB = Annotated[AsyncSession, Depends(db_session)]

    async def authenticated_session(request: Request, db: DB) -> OAuthSession:
        raw = request.cookies.get(SESSION_COOKIE)
        if not raw:
            raise error(401, "AUTHENTICATION_REQUIRED", "Log in with Discord first.")
        oauth_session = await db.get(OAuthSession, security.session_hash(raw))
        if oauth_session is None or oauth_session.expires_at <= now():
            if oauth_session is not None:
                await db.delete(oauth_session)
                await db.commit()
            raise error(401, "SESSION_EXPIRED", "Your session has expired.")
        return oauth_session

    AuthSession = Annotated[OAuthSession, Depends(authenticated_session)]

    async def access_token(oauth_session: OAuthSession, db: AsyncSession) -> str:
        if oauth_session.token_expires_at > now() + timedelta(seconds=60):
            return security.decrypt(oauth_session.access_token)
        try:
            token = await discord.refresh(security.decrypt(oauth_session.refresh_token))
        except DiscordAPIError as exc:
            raise error(401, "DISCORD_SESSION_EXPIRED", "Log in with Discord again.") from exc
        oauth_session.access_token = security.encrypt(token["access_token"])
        if token.get("refresh_token"):
            oauth_session.refresh_token = security.encrypt(token["refresh_token"])
        oauth_session.token_expires_at = now() + timedelta(seconds=int(token["expires_in"]))
        await db.commit()
        return token["access_token"]

    async def user_guilds(
        oauth_session: OAuthSession, db: AsyncSession, *, force: bool
    ) -> list[dict[str, Any]]:
        if (
            not force
            and oauth_session.guild_cache is not None
            and oauth_session.guild_cache_at is not None
            and oauth_session.guild_cache_at > now() - timedelta(minutes=2)
        ):
            return oauth_session.guild_cache
        try:
            guilds = await discord.user_guilds(await access_token(oauth_session, db))
        except DiscordAPIError as exc:
            raise error(
                503, "DISCORD_UNAVAILABLE", "Discord could not verify guild access."
            ) from exc
        oauth_session.guild_cache = guilds
        oauth_session.guild_cache_at = now()
        await db.commit()
        return guilds

    def manageable(guild: dict[str, Any]) -> bool:
        permissions = int(guild.get("permissions", "0"))
        return bool(guild.get("owner") or permissions & (MANAGE_GUILD | ADMINISTRATOR))

    async def authorized_guild(
        guild_id: str,
        oauth_session: OAuthSession,
        db: AsyncSession,
        *,
        force: bool,
        require_installed: bool = True,
    ) -> dict[str, Any]:
        if not valid_snowflake(guild_id):
            raise error(404, "GUILD_NOT_FOUND", "Guild not found.")
        guild = next(
            (
                item
                for item in await user_guilds(oauth_session, db, force=force)
                if str(item["id"]) == guild_id and manageable(item)
            ),
            None,
        )
        if guild is None:
            raise error(403, "GUILD_ACCESS_DENIED", "You cannot manage this guild.")
        if require_installed and guild_id not in await bot_guilds.get():
            raise error(409, "BOT_NOT_INSTALLED", "Install BotChan before configuring this guild.")
        return guild

    def check_csrf(request: Request, oauth_session: OAuthSession) -> None:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not supplied or not security_compare(supplied, oauth_session.csrf_token):
            raise error(403, "CSRF_REJECTED", "The request could not be verified.")

    @app.get("/auth/discord")
    async def discord_login() -> RedirectResponse:
        state = security.random_token()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": settings.discord_client_id,
                "scope": "identify guilds",
                "state": state,
                "redirect_uri": settings.discord_redirect_uri,
            }
        )
        response = RedirectResponse(f"https://discord.com/oauth2/authorize?{query}")
        response.set_cookie(
            OAUTH_STATE_COOKIE,
            security.sign_oauth_state(state),
            max_age=600,
            httponly=True,
            secure=settings.secure_cookies,
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
        signed_state = request.cookies.get(OAUTH_STATE_COOKIE, "")
        if (
            error_name
            or not code
            or not state
            or not security.verify_oauth_state(signed_state, state)
        ):
            response = RedirectResponse("/?auth_error=oauth_failed", status_code=303)
            response.delete_cookie(OAUTH_STATE_COOKIE)
            return response
        try:
            token = await discord.exchange_code(code)
            user = await discord.current_user(token["access_token"])
        except DiscordAPIError, KeyError:
            response = RedirectResponse("/?auth_error=discord_unavailable", status_code=303)
            response.delete_cookie(OAUTH_STATE_COOKIE)
            return response

        raw_session = security.random_token()
        timestamp = now()
        oauth_session = OAuthSession(
            id_hash=security.session_hash(raw_session),
            discord_user_id=str(user["id"]),
            username=user["username"],
            global_name=user.get("global_name"),
            avatar=user.get("avatar"),
            access_token=security.encrypt(token["access_token"]),
            refresh_token=security.encrypt(token["refresh_token"]),
            token_expires_at=timestamp + timedelta(seconds=int(token["expires_in"])),
            csrf_token=security.random_token(),
            created_at=timestamp,
            expires_at=timestamp + timedelta(days=settings.session_days),
        )
        db.add(oauth_session)
        await db.commit()
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(OAUTH_STATE_COOKIE)
        response.set_cookie(
            SESSION_COOKIE,
            raw_session,
            max_age=settings.session_days * 86400,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="lax",
        )
        return response

    @app.get("/api/session")
    async def session_info(request: Request, db: DB) -> dict[str, Any]:
        raw = request.cookies.get(SESSION_COOKIE)
        if not raw:
            return {"authenticated": False}
        oauth_session = await db.get(OAuthSession, security.session_hash(raw))
        if oauth_session is None or oauth_session.expires_at <= now():
            return {"authenticated": False}
        avatar_url = None
        if oauth_session.avatar:
            avatar_url = (
                f"https://cdn.discordapp.com/avatars/{oauth_session.discord_user_id}/"
                f"{oauth_session.avatar}.png?size=64"
            )
        return {
            "authenticated": True,
            "csrf_token": oauth_session.csrf_token,
            "user": {
                "id": oauth_session.discord_user_id,
                "name": oauth_session.global_name or oauth_session.username,
                "avatar_url": avatar_url,
            },
        }

    @app.post("/auth/logout")
    async def logout(request: Request, db: DB, oauth_session: AuthSession) -> JSONResponse:
        check_csrf(request, oauth_session)
        try:
            await discord.revoke(security.decrypt(oauth_session.access_token))
        except DiscordAPIError:
            pass
        await db.delete(oauth_session)
        await db.commit()
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/api/guilds")
    async def guild_list(db: DB, oauth_session: AuthSession) -> dict[str, Any]:
        guilds = [g for g in await user_guilds(oauth_session, db, force=False) if manageable(g)]
        bot_permissions = await bot_guilds.get()
        ids = [str(guild["id"]) for guild in guilds]
        revisions = {
            row.guild_id: row.revision
            for row in (
                await db.scalars(select(GuildConfigRow).where(GuildConfigRow.guild_id.in_(ids)))
            ).all()
        }
        result = []
        for guild in sorted(guilds, key=lambda item: item["name"].casefold()):
            guild_id = str(guild["id"])
            icon = guild.get("icon")
            installed = guild_id in bot_permissions
            granted = bot_permissions.get(guild_id, 0)
            # Re-authorizing an installed bot updates its permissions, so the
            # same install link doubles as the fix for a stale grant.
            needs_permissions = installed and not has_bot_permissions(granted)
            result.append(
                {
                    "id": guild_id,
                    "name": guild["name"],
                    "icon_url": icon_url(guild_id, icon),
                    "installed": installed,
                    "needs_permissions": needs_permissions,
                    "configured": guild_id in revisions,
                    "revision": revisions.get(guild_id),
                    "install_url": install_url(settings.discord_client_id, guild_id),
                }
            )
        return {"guilds": result}

    @app.get("/api/guilds/{guild_id}/config")
    async def get_config(guild_id: str, db: DB, oauth_session: AuthSession) -> JSONResponse:
        await authorized_guild(guild_id, oauth_session, db, force=False, require_installed=False)
        return config_response(guild_id, await db.get(GuildConfigRow, guild_id))

    @app.get("/api/guilds/{guild_id}/channels")
    async def channel_list(guild_id: str, db: DB, oauth_session: AuthSession) -> dict[str, Any]:
        await authorized_guild(guild_id, oauth_session, db, force=False)
        return {"channels": await guild_voice_channels(discord, guild_id)}

    @app.put("/api/guilds/{guild_id}/config")
    async def put_config(
        guild_id: str,
        body: ConfigWrite,
        request: Request,
        db: DB,
        oauth_session: AuthSession,
        if_match: Annotated[str | None, Header()] = None,
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        check_csrf(request, oauth_session)
        await authorized_guild(guild_id, oauth_session, db, force=True)
        return await save_config(
            db,
            discord,
            guild_id,
            body,
            editor_id=oauth_session.discord_user_id,
            editor_name=oauth_session.global_name or oauth_session.username,
            if_match=if_match,
            if_none_match=if_none_match,
        )

    # A small copy of botchan.png (1024 px, 2 MB), which stays the source image.
    @app.get("/botchan-192.png", include_in_schema=False)
    async def logo() -> FileResponse:
        return FileResponse(PROJECT_ROOT / "botchan-192.png")

    # check_dir=False lets the API start (and its tests run) before the editor
    # is built; requests for the editor then fail loudly instead.
    app.mount("/", StaticFiles(directory=WEB_ROOT, html=True, check_dir=False), name="web")
    return app
