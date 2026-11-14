import unittest

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from botchan.rate_limits import describe_rate_limit, rate_limit_trace

CREATE = "/api/v10/guilds/1521248861186818118/channels"


class RateLimitLoggingTests(unittest.TestCase):
    def test_every_channel_create_is_logged_with_its_headers(self) -> None:
        line = describe_rate_limit(
            "POST",
            CREATE,
            201,
            {
                "X-RateLimit-Limit": "5",
                "X-RateLimit-Remaining": "4",
                "X-RateLimit-Reset-After": "83357.94",
                "X-RateLimit-Bucket": "abc123",
            },
        )

        self.assertEqual(
            line,
            f"POST {CREATE} -> 201: limit=5 remaining=4 reset_after=83357.94 bucket=abc123",
        )

    def test_other_requests_are_quiet_while_their_bucket_has_room(self) -> None:
        headers = {"X-RateLimit-Limit": "5", "X-RateLimit-Remaining": "3"}

        self.assertIsNone(describe_rate_limit("GET", CREATE, 200, headers))
        self.assertIsNone(describe_rate_limit("PATCH", "/api/v10/channels/1", 200, headers))

    def test_an_emptied_bucket_or_a_refusal_is_logged_on_any_route(self) -> None:
        emptied = describe_rate_limit(
            "PATCH", "/api/v10/channels/1", 200, {"X-RateLimit-Remaining": "0"}
        )
        refused = describe_rate_limit(
            "PATCH", "/api/v10/channels/1", 429, {"X-RateLimit-Scope": "shared"}
        )

        self.assertIn("remaining=0", emptied or "")
        self.assertIn("-> 429: scope=shared", refused or "")

    def test_a_create_without_headers_says_so(self) -> None:
        self.assertEqual(
            describe_rate_limit("POST", CREATE, 201, {}),
            f"POST {CREATE} -> 201: no rate limit headers",
        )


class RateLimitTraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_trace_logs_a_real_response(self) -> None:
        async def create_channel(_request: web.Request) -> web.Response:
            return web.json_response(
                {"id": "1"},
                status=201,
                headers={"X-RateLimit-Limit": "5", "X-RateLimit-Remaining": "0"},
            )

        app = web.Application()
        app.router.add_post("/api/v10/guilds/42/channels", create_channel)
        async with (
            TestServer(app) as server,
            aiohttp.ClientSession(trace_configs=[rate_limit_trace()]) as session,
        ):
            with self.assertLogs("botchan.ratelimit", "INFO") as logs:
                async with session.post(server.make_url("/api/v10/guilds/42/channels")):
                    pass

        self.assertEqual(
            logs.output,
            ["INFO:botchan.ratelimit:POST /api/v10/guilds/42/channels -> 201: limit=5 remaining=0"],
        )


if __name__ == "__main__":
    unittest.main()
