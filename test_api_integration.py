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

        from botchan_api.app import (
            ADMINISTRATOR,
            BOT_PERMISSIONS,
            MANAGE_CHANNELS,
            create_app,
            now,
        )
        from botchan_api.models import GuildConfigRow, GuildConfigVersion, OAuthSession
        from botchan_api.settings import Settings

        self.discord_calls = {"user_guilds": 0, "bot_guilds": 0}
        discord_calls = self.discord_calls
        # Tests override this before their first request; the app caches the
        # result for 30 seconds, so later mutations within a test are ignored.
        self.bot_permissions = {"123456789012345678": BOT_PERMISSIONS}
        bot_permissions = self.bot_permissions
        self.guild_channels = [
            {"id": "223456789012345678", "type": 2, "name": "Other Games", "position": 0},
            {"id": "223456789012345679", "type": 2, "name": "Other Games #2", "position": 1},
            {"id": "223456789012345680", "type": 0, "name": "general", "position": 2},
        ]
        guild_channels = self.guild_channels
        self.required_bits = BOT_PERMISSIONS
        self.manage_channels_bit = MANAGE_CHANNELS
        self.administrator_bit = ADMINISTRATOR

        class FakeDiscord:
            async def user_guilds(_self, _access_token):
                discord_calls["user_guilds"] += 1
                return [
                    {
                        "id": "123456789012345678",
                        "name": "Test Guild",
                        "owner": False,
                        "permissions": str(1 << 5),
                        "icon": None,
                    }
                ]

            async def bot_guilds(_self):
                discord_calls["bot_guilds"] += 1
                return dict(bot_permissions)

            async def guild_channels(_self, _guild_id):
                return [dict(channel) for channel in guild_channels]

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

    async def test_probes_report_ready_against_a_real_database(self) -> None:
        health = await self.client.get("/healthz")
        ready = await self.client.get("/readyz")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json(), {"status": "ok"})

    async def test_guild_list_reports_installation_and_permissions(self) -> None:
        response = await self.client.get("/api/guilds")

        self.assertEqual(response.status_code, 200)
        guild = response.json()["guilds"][0]
        self.assertTrue(guild["installed"])
        self.assertFalse(guild["needs_permissions"])
        self.assertIn(f"permissions={self.required_bits}", guild["install_url"])

    async def test_guild_list_flags_a_stale_permission_grant(self) -> None:
        # The grant BotChan shipped with before it synced channel permissions.
        self.bot_permissions["123456789012345678"] = self.manage_channels_bit

        response = await self.client.get("/api/guilds")

        guild = response.json()["guilds"][0]
        self.assertTrue(guild["installed"])
        self.assertTrue(guild["needs_permissions"])

    async def test_guild_list_accepts_administrator_in_place_of_both(self) -> None:
        self.bot_permissions["123456789012345678"] = self.administrator_bit

        response = await self.client.get("/api/guilds")

        self.assertFalse(response.json()["guilds"][0]["needs_permissions"])

    async def test_first_save_get_and_stale_save(self) -> None:
        import asyncpg
        from sqlalchemy import func, select

        from botchan.database_config import PostgresConfigSource
        from botchan_api.models import GuildConfigVersion

        assert TEST_DATABASE_URL is not None
        notification = asyncio.Event()
        payloads = []
        connection = await asyncpg.connect(TEST_DATABASE_URL.replace("+asyncpg", ""))

        def listener(_connection, _pid, _channel, payload):
            payloads.append(payload)
            notification.set()

        await connection.add_listener("botchan_config_changed", listener)
        config_changes = []
        config_changed = asyncio.Event()

        async def apply_snapshot(snapshot):
            self.assertEqual(snapshot.guilds, {})

        async def apply_change(change):
            config_changes.append(change)
            config_changed.set()

        source = PostgresConfigSource(TEST_DATABASE_URL)
        await source.start(apply_snapshot, apply_change)
        self.addAsyncCleanup(source.close)
        body = {
            "channel_pools": [
                {
                    "base_channel_id": "223456789012345678",
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
        await asyncio.wait_for(config_changed.wait(), timeout=2)
        self.assertEqual(config_changes[0].config.revision, 1)
        saved_pool = config_changes[0].config.spec.channel_pools[0]
        self.assertEqual(saved_pool.base_channel_id, 223456789012345678)
        # The name comes from Discord, not from the request.
        self.assertEqual(saved_pool.base_name, "Other Games")

        loaded = await self.client.get("/api/guilds/123456789012345678/config")
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(
            loaded.json()["channel_pools"],
            [{**body["channel_pools"][0], "base_name": "Other Games"}],
        )

        stale = await self.client.put(
            "/api/guilds/123456789012345678/config",
            json=body,
            headers={"X-CSRF-Token": "csrf-token", "If-None-Match": "*"},
        )
        self.assertEqual(stale.status_code, 412)
        self.assertEqual(self.discord_calls, {"user_guilds": 2, "bot_guilds": 1})
        async with self.app.state.session_factory() as db:
            version_count = await db.scalar(select(func.count()).select_from(GuildConfigVersion))
        self.assertEqual(version_count, 1)
        await connection.close()

    async def put(self, *base_channel_ids: str):
        return await self.client.put(
            "/api/guilds/123456789012345678/config",
            json={
                "channel_pools": [
                    {"base_channel_id": channel_id, "min_channels": 1, "max_channels": 10}
                    for channel_id in base_channel_ids
                ]
            },
            headers={"X-CSRF-Token": "csrf-token", "If-None-Match": "*"},
        )

    async def test_channel_list_offers_voice_channels_only(self) -> None:
        response = await self.client.get("/api/guilds/123456789012345678/channels")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [channel["name"] for channel in response.json()["channels"]],
            ["Other Games", "Other Games #2"],
        )

    async def test_a_base_channel_must_be_a_voice_channel_in_the_guild(self) -> None:
        text_channel = await self.put("223456789012345680")
        unknown = await self.put("323456789012345678")

        self.assertEqual(text_channel.status_code, 422)
        self.assertEqual(unknown.json()["code"], "BASE_CHANNEL_NOT_FOUND")

    async def test_a_base_channel_cannot_be_another_pools_extra(self) -> None:
        response = await self.put("223456789012345678", "223456789012345679")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "POOL_NAMES_CONFLICT")
