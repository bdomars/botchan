"""Log Discord's rate-limit headers for the requests that matter to BotChan.

Discord does not publish its per-route limits; it only reports them in
response headers, which discord.py reads but never logs. Channel creation in
particular has a limit measured in hours, so its headers are logged on every
create, and any other response is logged once it empties its bucket or is
refused outright.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from types import SimpleNamespace

import aiohttp

log = logging.getLogger("botchan.ratelimit")

CREATE_CHANNEL_PATH = re.compile(r"/guilds/\d+/channels$")
RATE_LIMIT_HEADERS = {
    "limit": "X-RateLimit-Limit",
    "remaining": "X-RateLimit-Remaining",
    "reset_after": "X-RateLimit-Reset-After",
    "bucket": "X-RateLimit-Bucket",
    # Only sent with a 429: user, global or shared.
    "scope": "X-RateLimit-Scope",
}


def describe_rate_limit(
    method: str, path: str, status: int, headers: Mapping[str, str]
) -> str | None:
    """A log line for this response, or None if it is not worth one."""
    creates_channel = method == "POST" and CREATE_CHANNEL_PATH.search(path) is not None
    exhausted = headers.get(RATE_LIMIT_HEADERS["remaining"]) == "0"
    if not (creates_channel or exhausted or status == 429):
        return None
    values = " ".join(
        f"{name}={headers[header]}"
        for name, header in RATE_LIMIT_HEADERS.items()
        if header in headers
    )
    return f"{method} {path} -> {status}: {values or 'no rate limit headers'}"


async def _on_request_end(
    _session: aiohttp.ClientSession,
    _context: SimpleNamespace,
    params: aiohttp.TraceRequestEndParams,
) -> None:
    response = params.response
    line = describe_rate_limit(params.method, params.url.path, response.status, response.headers)
    if line is None:
        return
    log.log(logging.WARNING if response.status == 429 else logging.INFO, "%s", line)


def rate_limit_trace() -> aiohttp.TraceConfig:
    trace = aiohttp.TraceConfig()
    trace.on_request_end.append(_on_request_end)
    return trace
