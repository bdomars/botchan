import json
import logging
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Self
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import RequestResponseEndpoint

from botchan_api.database import create_database
from botchan_api.discord_client import DiscordAPIError, DiscordClient
from botchan_api.models import GuildConfigRow, GuildConfigVersion, OAuthSession
from botchan_api.security import Security
from botchan_api.settings import Settings
from botchan_config import ChannelPoolConfig, GuildConfig


SESSION_COOKIE = "botchan_session"
OAUTH_STATE_COOKIE = "botchan_oauth_state"
MANAGE_GUILD = 1 << 5
ADMINISTRATOR = 1 << 3
WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
PROJECT_ROOT = WEB_ROOT.parent
log = logging.getLogger("botchan.api")


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message


class ConfigWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    channel_pools: list[ChannelPoolConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_names(self) -> Self:
        names = [pool.base_name for pool in self.channel_pools]
        if len(names) != len(set(names)):
            raise ValueError("channel pool base names must be unique")
        return self


def now() -> datetime:
    return datetime.now(UTC)


def error(status: int, code: str, message: str) -> APIError:
    return APIError(status, code, message)


def config_etag(revision: int) -> str:
    return f'"guild-config-{revision}"'


def valid_snowflake(value: str) -> bool:
    return value.isdigit() and value[0] != "0" and len(value) <= 20


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

    @app.middleware("http")
    async def security_headers(request: Request, call_next: RequestResponseEndpoint):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith(("/api/", "/auth/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(APIError)
    async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "INVALID_REQUEST",
                "message": "The request did not pass validation.",
                "details": jsonable_encoder(exc.errors()),
            },
        )

    async def db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

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
            raise error(503, "DISCORD_UNAVAILABLE", "Discord could not verify guild access.") from exc
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
        if require_installed:
            try:
                installed = guild_id in await discord.bot_guild_ids()
            except DiscordAPIError as exc:
                log.exception("Discord bot installation check failed: %s", exc)
                raise error(503, "DISCORD_UNAVAILABLE", "Bot installation could not be checked.") from exc
            if not installed:
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
        if error_name or not code or not state or not security.verify_oauth_state(signed_state, state):
            response = RedirectResponse("/?auth_error=oauth_failed", status_code=303)
            response.delete_cookie(OAUTH_STATE_COOKIE)
            return response
        try:
            token = await discord.exchange_code(code)
            user = await discord.current_user(token["access_token"])
        except (DiscordAPIError, KeyError):
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

    @app.get("/api/health")
    async def health(db: DB) -> dict[str, str]:
        await db.execute(text("SELECT 1"))
        return {"status": "ok"}

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
        try:
            installed_ids = await discord.bot_guild_ids()
        except DiscordAPIError as exc:
            log.exception("Discord bot installation check failed: %s", exc)
            raise error(503, "DISCORD_UNAVAILABLE", "Bot installation could not be checked.") from exc
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
            installed = guild_id in installed_ids
            install_query = urlencode(
                {
                    "client_id": settings.discord_client_id,
                    "scope": "bot",
                    "permissions": str(1 << 4),
                    "guild_id": guild_id,
                    "disable_guild_select": "true",
                }
            )
            result.append(
                {
                    "id": guild_id,
                    "name": guild["name"],
                    "icon_url": (
                        f"https://cdn.discordapp.com/icons/{guild_id}/{icon}.png?size=64"
                        if icon
                        else None
                    ),
                    "installed": installed,
                    "configured": guild_id in revisions,
                    "revision": revisions.get(guild_id),
                    "install_url": f"https://discord.com/oauth2/authorize?{install_query}",
                }
            )
        return {"guilds": result}

    @app.get("/api/guilds/{guild_id}/config")
    async def get_config(guild_id: str, db: DB, oauth_session: AuthSession) -> JSONResponse:
        await authorized_guild(guild_id, oauth_session, db, force=False)
        row = await db.get(GuildConfigRow, guild_id)
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
        guild = await authorized_guild(guild_id, oauth_session, db, force=True)
        validated = GuildConfig.model_validate(
            {"guild_id": int(guild_id), "channel_pools": body.model_dump()["channel_pools"]}
        )
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
        editor_name = oauth_session.global_name or oauth_session.username
        if row is None:
            row = GuildConfigRow(
                guild_id=guild_id,
                document=document,
                revision=revision,
                editor_discord_user_id=oauth_session.discord_user_id,
                updated_at=timestamp,
            )
            db.add(row)
            await db.flush()
        else:
            row.document = document
            row.revision = revision
            row.editor_discord_user_id = oauth_session.discord_user_id
            row.updated_at = timestamp
        db.add(
            GuildConfigVersion(
                guild_id=guild_id,
                revision=revision,
                document=document,
                editor_discord_user_id=oauth_session.discord_user_id,
                editor_name=editor_name,
                created_at=timestamp,
            )
        )
        payload = json.dumps({"guild_id": guild_id, "revision": revision}, separators=(",", ":"))
        await db.execute(
            text("SELECT pg_notify('botchan_config_changed', :payload)"), {"payload": payload}
        )
        await db.commit()
        response = JSONResponse(
            {
                "guild_id": guild_id,
                "channel_pools": document["channel_pools"],
                "revision": revision,
                "updated_at": timestamp.isoformat(),
            }
        )
        response.headers["ETag"] = config_etag(revision)
        return response

    @app.get("/botchan.png", include_in_schema=False)
    async def logo() -> FileResponse:
        return FileResponse(PROJECT_ROOT / "botchan.png")

    app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")
    return app


def security_compare(left: str, right: str) -> bool:
    return secrets.compare_digest(left, right)
