import contextlib
import io
import os
import unittest
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet

TEST_DATABASE_URL = os.environ.get("BOTCHAN_TEST_DATABASE_URL")
INSTALLED_GUILD = "123456789012345678"
# A guild the operator is not a member of: the admin app must still show it.
OTHER_GUILD = "123456789012345679"
# A guild with a stored config that the bot has since left.
ORPHANED_GUILD = "123456789012345680"
OPERATOR_ID = "987654321098765432"
STRANGER_ID = "987654321098765433"
POOLS = {
    "channel_pools": [
        {
            "base_channel_id": "223456789012345678",
            "min_channels": 3,
            "max_channels": 10,
            "idle_seconds": 600,
        }
    ]
}


class AdminCLIArgumentTests(unittest.TestCase):
    def test_user_ids_must_be_snowflakes(self) -> None:
        from botchan_api.admin.cli import parse_args

        self.assertEqual(parse_args(["grant", OPERATOR_ID]).user_id, OPERATOR_ID)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parse_args(["grant", "not-an-id"])


@unittest.skipUnless(TEST_DATABASE_URL, "BOTCHAN_TEST_DATABASE_URL is not set")
class AdminAPIIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        import httpx
        from sqlalchemy import delete

        from botchan_api.admin.app import create_admin_app
        from botchan_api.admin.settings import AdminSettings
        from botchan_api.guilds import BOT_PERMISSIONS
        from botchan_api.models import (
            AdminSession,
            GuildConfigRow,
            GuildConfigVersion,
            OAuthSession,
            Operator,
        )
        from botchan_api.settings import Settings

        self.logged_in_user = {"id": OPERATOR_ID, "username": "op", "global_name": "The Operator"}
        logged_in_user = self.logged_in_user
        self.revoked_tokens: list[str] = []
        revoked_tokens = self.revoked_tokens

        class FakeDiscord:
            async def bot_guild_list(_self):
                return [
                    {
                        "id": INSTALLED_GUILD,
                        "name": "Zebra Guild",
                        "icon": "abc",
                        "permissions": str(BOT_PERMISSIONS),
                    },
                    {"id": OTHER_GUILD, "name": "alpha guild", "icon": None, "permissions": "0"},
                ]

            async def guild_channels(_self, _guild_id):
                return [{"id": "223456789012345678", "type": 2, "name": "Games", "position": 0}]

            async def exchange_code(_self, code, redirect_uri=None):
                assert redirect_uri == "http://admin.test/auth/discord/callback"
                return {"access_token": f"token-for-{code}"}

            async def current_user(_self, _access_token):
                return dict(logged_in_user)

            async def revoke(_self, token):
                revoked_tokens.append(token)

        assert TEST_DATABASE_URL is not None
        settings = AdminSettings(
            base=Settings(
                database_url=TEST_DATABASE_URL,
                discord_client_id="123456789012345678",
                discord_client_secret="secret",
                discord_bot_token="bot-token",
                discord_redirect_uri="http://test/auth/discord/callback",
                public_base_url="http://test",
                session_secret="session-secret",
                token_encryption_key=Fernet.generate_key().decode(),
                secure_cookies=False,
            ),
            public_base_url="http://admin.test",
            discord_redirect_uri="http://admin.test/auth/discord/callback",
        )
        self.app = create_admin_app(settings, FakeDiscord())
        self.session_factory = self.app.state.session_factory
        async with self.session_factory() as db:
            for model in (
                GuildConfigVersion,
                GuildConfigRow,
                AdminSession,
                Operator,
                OAuthSession,
            ):
                await db.execute(delete(model))
            await db.commit()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://admin.test"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.app.state.engine.dispose()

    async def grant(self, user_id: str = OPERATOR_ID) -> None:
        from botchan_api.admin.cli import grant

        await grant(self.session_factory, user_id, "test")

    async def login(self):
        start = await self.client.get("/auth/discord")
        self.assertEqual(start.status_code, 307)
        query = parse_qs(urlparse(start.headers["location"]).query)
        self.assertEqual(query["scope"], ["identify"])
        self.assertEqual(query["redirect_uri"], ["http://admin.test/auth/discord/callback"])
        return await self.client.get(
            "/auth/discord/callback", params={"code": "the-code", "state": query["state"][0]}
        )

    async def login_as_operator(self) -> str:
        await self.grant()
        response = await self.login()
        self.assertEqual(response.headers["location"], "/")
        session = (await self.client.get("/api/session")).json()
        self.assertTrue(session["authenticated"])
        return session["csrf_token"]

    async def test_a_non_operator_gets_no_session(self) -> None:
        from sqlalchemy import func, select

        from botchan_api.models import AdminSession

        with self.assertLogs("botchan.admin", "WARNING") as logs:
            response = await self.login()

        self.assertIn(OPERATOR_ID, logs.output[0])
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/?auth_error=not_operator")
        self.assertNotIn("botchan_admin_session", response.cookies)
        async with self.session_factory() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(AdminSession)), 0)
        # The Discord token is never kept, whoever logs in.
        self.assertEqual(self.revoked_tokens, ["token-for-the-code"])

    async def test_a_forged_state_is_rejected(self) -> None:
        await self.grant()
        await self.client.get("/auth/discord")

        response = await self.client.get(
            "/auth/discord/callback", params={"code": "the-code", "state": "forged"}
        )

        self.assertEqual(response.headers["location"], "/?auth_error=oauth_failed")

    async def test_endpoints_require_a_login(self) -> None:
        for path in (
            "/api/guilds",
            f"/api/guilds/{INSTALLED_GUILD}/config",
            f"/api/guilds/{INSTALLED_GUILD}/versions",
        ):
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 401, path)
        self.assertEqual((await self.client.get("/api/session")).json(), {"authenticated": False})

    async def test_a_user_app_session_is_not_an_admin_session(self) -> None:
        from botchan_api.app import SESSION_COOKIE
        from botchan_api.common import now
        from botchan_api.models import OAuthSession

        await self.grant()
        security = self.app.state.security
        timestamp = now()
        async with self.session_factory() as db:
            db.add(
                OAuthSession(
                    id_hash=security.session_hash("user-session"),
                    discord_user_id=OPERATOR_ID,
                    username="op",
                    global_name=None,
                    avatar=None,
                    access_token=security.encrypt("access"),
                    refresh_token=security.encrypt("refresh"),
                    token_expires_at=timestamp + timedelta(hours=1),
                    csrf_token="csrf",
                    created_at=timestamp,
                    expires_at=timestamp + timedelta(days=1),
                )
            )
            await db.commit()
        self.client.cookies.set(SESSION_COOKIE, "user-session")
        self.client.cookies.set("botchan_admin_session", "user-session")

        response = await self.client.get("/api/guilds")

        self.assertEqual(response.status_code, 401)

    async def test_guild_list_shows_every_guild_and_orphaned_configs(self) -> None:
        from botchan_api.common import now
        from botchan_api.models import GuildConfigRow

        await self.login_as_operator()
        async with self.session_factory() as db:
            db.add(
                GuildConfigRow(
                    guild_id=ORPHANED_GUILD,
                    document={"guild_id": ORPHANED_GUILD, "channel_pools": []},
                    revision=4,
                    editor_discord_user_id=STRANGER_ID,
                    updated_at=now(),
                )
            )
            await db.commit()

        response = await self.client.get("/api/guilds")

        self.assertEqual(response.status_code, 200)
        guilds = response.json()["guilds"]
        self.assertEqual([g["id"] for g in guilds], [OTHER_GUILD, INSTALLED_GUILD, ORPHANED_GUILD])
        other, installed, orphaned = guilds
        self.assertTrue(other["needs_permissions"])
        self.assertFalse(installed["needs_permissions"])
        self.assertIsNotNone(installed["icon_url"])
        self.assertEqual(
            orphaned,
            orphaned | {"name": None, "installed": False, "configured": True, "revision": 4},
        )

    async def test_operator_saves_are_flagged_and_versioned(self) -> None:
        csrf = await self.login_as_operator()
        url = f"/api/guilds/{INSTALLED_GUILD}/config"

        missing_csrf = await self.client.put(url, json=POOLS, headers={"If-None-Match": "*"})
        first = await self.client.put(
            url, json=POOLS, headers={"X-CSRF-Token": csrf, "If-None-Match": "*"}
        )
        second = await self.client.put(
            url, json=POOLS, headers={"X-CSRF-Token": csrf, "If-Match": first.headers["etag"]}
        )
        stale = await self.client.put(
            url, json=POOLS, headers={"X-CSRF-Token": csrf, "If-Match": first.headers["etag"]}
        )

        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.headers["etag"], '"guild-config-2"')
        self.assertEqual(stale.status_code, 412)
        self.assertEqual(
            first.json()["channel_pools"][0]["base_name"], "Games", "names come from Discord"
        )

        versions = (await self.client.get(f"/api/guilds/{INSTALLED_GUILD}/versions")).json()
        self.assertEqual([v["revision"] for v in versions["versions"]], [2, 1])
        self.assertTrue(all(v["as_operator"] for v in versions["versions"]))
        self.assertEqual(versions["versions"][0]["editor"]["name"], "The Operator")
        older = (
            await self.client.get(
                f"/api/guilds/{INSTALLED_GUILD}/versions", params={"before": 2, "limit": 1}
            )
        ).json()
        self.assertEqual([v["revision"] for v in older["versions"]], [1])
        detail = await self.client.get(f"/api/guilds/{INSTALLED_GUILD}/versions/1")
        self.assertEqual(detail.json()["channel_pools"], first.json()["channel_pools"])
        missing = await self.client.get(f"/api/guilds/{INSTALLED_GUILD}/versions/99")
        self.assertEqual(missing.status_code, 404)

    async def test_guilds_the_bot_has_left_cannot_be_edited(self) -> None:
        csrf = await self.login_as_operator()

        channels = await self.client.get(f"/api/guilds/{ORPHANED_GUILD}/channels")
        save = await self.client.put(
            f"/api/guilds/{ORPHANED_GUILD}/config",
            json=POOLS,
            headers={"X-CSRF-Token": csrf, "If-None-Match": "*"},
        )
        config = await self.client.get(f"/api/guilds/{ORPHANED_GUILD}/config")

        self.assertEqual(channels.status_code, 409)
        self.assertEqual(save.status_code, 409)
        self.assertEqual(config.status_code, 200)

    async def test_revoking_an_operator_ends_their_session_at_once(self) -> None:
        from botchan_api.admin.cli import list_operators, revoke

        await self.login_as_operator()
        self.assertEqual((await self.client.get("/api/guilds")).status_code, 200)

        self.assertTrue(await revoke(self.session_factory, OPERATOR_ID))
        self.assertFalse(await revoke(self.session_factory, OPERATOR_ID))

        self.assertEqual(await list_operators(self.session_factory), [])
        self.assertEqual((await self.client.get("/api/guilds")).status_code, 401)

    async def test_an_operator_removed_behind_the_cli_is_refused(self) -> None:
        from sqlalchemy import delete

        from botchan_api.models import Operator

        await self.login_as_operator()
        # Removed straight from the table, so the session row is still there.
        async with self.session_factory() as db:
            await db.execute(delete(Operator))
            await db.commit()

        response = await self.client.get("/api/guilds")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "NOT_AN_OPERATOR")

    async def test_logout_needs_csrf_and_ends_the_session(self) -> None:
        csrf = await self.login_as_operator()
        raw_session = self.client.cookies["botchan_admin_session"]

        refused = await self.client.post("/auth/logout")
        accepted = await self.client.post("/auth/logout", headers={"X-CSRF-Token": csrf})

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(accepted.status_code, 200)
        # A copy of the old cookie is worthless once the session row is gone.
        self.client.cookies.set("botchan_admin_session", raw_session)
        self.assertEqual((await self.client.get("/api/guilds")).status_code, 401)


if __name__ == "__main__":
    unittest.main()
