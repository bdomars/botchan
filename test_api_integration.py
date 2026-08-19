import asyncio
import os
import unittest
from datetime import timedelta

from cryptography.fernet import Fernet


TEST_DATABASE_URL = os.environ.get("BOTCHAN_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DATABASE_URL, "BOTCHAN_TEST_DATABASE_URL is not set")
class ConfigAPIIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        import httpx
        from sqlalchemy import delete

        from botchan_api.app import create_app, now
        from botchan_api.models import GuildConfigRow, GuildConfigVersion, OAuthSession
        from botchan_api.settings import Settings

        class FakeDiscord:
            async def user_guilds(_self, _access_token):
                return [
                    {
                        "id": "123456789012345678",
                        "name": "Test Guild",
                        "owner": False,
                        "permissions": str(1 << 5),
                        "icon": None,
                    }
                ]

            async def bot_guild_ids(_self):
                return {"123456789012345678"}

            async def refresh(_self, _refresh_token):
                raise AssertionError("token should not need refresh")

            async def revoke(_self, _token):
                return None

        assert TEST_DATABASE_URL is not None
        self.key = Fernet.generate_key().decode()
        settings = Settings(
            database_url=TEST_DATABASE_URL,
            discord_client_id="123456789012345678",
            discord_client_secret="secret",
            discord_bot_token="bot-token",
            discord_redirect_uri="http://test/auth/discord/callback",
            public_base_url="http://test",
            session_secret="session-secret",
            token_encryption_key=self.key,
            secure_cookies=False,
        )
        self.app = create_app(settings, FakeDiscord())
        async with self.app.state.session_factory() as db:
            await db.execute(delete(GuildConfigVersion))
            await db.execute(delete(GuildConfigRow))
            await db.execute(delete(OAuthSession))
            timestamp = now()
            db.add(
                OAuthSession(
                    id_hash=self.app.state.security.session_hash("browser-session"),
                    discord_user_id="987654321098765432",
                    username="admin",
                    global_name="Guild Admin",
                    avatar=None,
                    access_token=self.app.state.security.encrypt("access"),
                    refresh_token=self.app.state.security.encrypt("refresh"),
                    token_expires_at=timestamp + timedelta(hours=1),
                    csrf_token="csrf-token",
                    created_at=timestamp,
                    expires_at=timestamp + timedelta(days=7),
                )
            )
            await db.commit()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        self.client.cookies.set("botchan_session", "browser-session")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.app.state.engine.dispose()

    async def test_first_save_get_and_stale_save(self) -> None:
        import asyncpg
        from sqlalchemy import func, select

        from botchan_api.models import GuildConfigVersion

        assert TEST_DATABASE_URL is not None
        notification = asyncio.Event()
        payloads = []
        connection = await asyncpg.connect(TEST_DATABASE_URL.replace("+asyncpg", ""))

        def listener(_connection, _pid, _channel, payload):
            payloads.append(payload)
            notification.set()

        await connection.add_listener("botchan_config_changed", listener)
        body = {
            "channel_pools": [
                {
                    "base_name": "Other Games",
                    "min_channels": 3,
                    "max_channels": 10,
                    "idle_seconds": 600,
                }
            ]
        }
        response = await self.client.put(
            "/api/guilds/123456789012345678/config",
            json=body,
            headers={"X-CSRF-Token": "csrf-token", "If-None-Match": "*"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["etag"], '"guild-config-1"')
        self.assertEqual(response.json()["guild_id"], "123456789012345678")
        await asyncio.wait_for(notification.wait(), timeout=2)
        self.assertEqual(payloads, ['{"guild_id":"123456789012345678","revision":1}'])

        loaded = await self.client.get("/api/guilds/123456789012345678/config")
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["channel_pools"], body["channel_pools"])

        stale = await self.client.put(
            "/api/guilds/123456789012345678/config",
            json=body,
            headers={"X-CSRF-Token": "csrf-token", "If-None-Match": "*"},
        )
        self.assertEqual(stale.status_code, 412)
        async with self.app.state.session_factory() as db:
            version_count = await db.scalar(select(func.count()).select_from(GuildConfigVersion))
        self.assertEqual(version_count, 1)
        await connection.close()
