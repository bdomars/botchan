import assert from "node:assert/strict";
import { test } from "vitest";
import * as core from "./core";
import type { ParseResult } from "./core";

// The error of a parse that is expected to fail.
function parseError(result: ParseResult): string {
    assert.equal(result.ok, false);
    return result.ok ? "" : result.error;
}

const GUILD = "123456789012345678";

test("guild list filters round-trip through the query", () => {
    const filters: core.GuildFilters[] = [
        { q: "odl", status: "orphaned", offset: 50 },
        { q: "", status: "all", offset: 0 },
    ];
    for (const value of filters) {
        assert.deepEqual(core.parseGuildFilters(core.guildFiltersQuery(value)), value);
    }
    assert.deepEqual(core.guildFiltersQuery({ q: "", status: "all", offset: 0 }), {});
});

test("malformed filters fall back to their defaults", () => {
    assert.deepEqual(core.parseGuildFilters({ status: "bogus", offset: "-5" }), {
        q: "", status: "all", offset: 0,
    });
    assert.deepEqual(core.parseGuildFilters({ q: ["first", "second"], status: null }), {
        q: "first", status: "all", offset: 0,
    });
});

test("a full guild ID opens the guild, anything else searches", () => {
    assert.deepEqual(core.lookupRoute(` ${GUILD} `), { name: "guild", guildId: GUILD });
    assert.deepEqual(core.lookupRoute("1234"), { name: "guilds", q: "1234", status: "all", offset: 0 });
    assert.deepEqual(core.lookupRoute("Old Drunken"), {
        name: "guilds", q: "Old Drunken", status: "all", offset: 0,
    });
});

test("the editor text is the PUT body and parses back", () => {
    const pools = [{ base_channel_id: "223456789012345678", min_channels: 3, max_channels: 10, idle_seconds: 600 }];
    const text = core.formatBody(pools);
    assert.match(text, /^\{\n {2}"channel_pools": \[/);
    assert.deepEqual(core.parseBody(text), { ok: true, value: { channel_pools: pools } });
});

test("pool keys are shown in a fixed order, unknown keys last", () => {
    const text = core.formatBody([{ idle_seconds: 60, extra: true, base_name: "Games", max_channels: 5, min_channels: 1, base_channel_id: "2" }]);
    const keys = Object.keys(JSON.parse(text).channel_pools[0] as object);
    assert.deepEqual(keys, ["base_channel_id", "base_name", "min_channels", "max_channels", "idle_seconds", "extra"]);
});

test("parse errors name the line and column", () => {
    const result = core.parseBody('{\n  "channel_pools": [\n    {,}\n  ]\n}');
    assert.match(parseError(result), /line 3, column 6/);
});

test("the body must be an object holding only a channel_pools array", () => {
    assert.match(parseError(core.parseBody("[]")), /Expected an object/);
    assert.match(parseError(core.parseBody('{"channel_pools": {}}')), /must be an array/);
    assert.match(parseError(core.parseBody('{"channel_pools": [], "guild_id": "1"}')), /Unexpected key: guild_id/);
});

test("line diff marks removed and added lines", () => {
    const diff = core.diffLines("a\nb\nc", "a\nx\nc\nd");
    assert.deepEqual(diff, [
        { op: " ", text: "a" },
        { op: "-", text: "b" },
        { op: "+", text: "x" },
        { op: " ", text: "c" },
        { op: "+", text: "d" },
    ]);
    assert.ok(core.diffLines("same", "same").every((line) => line.op === " "));
});

test("relative times are short", () => {
    const now = Date.parse("2026-11-14T12:00:00Z");
    assert.equal(core.relativeTime("2026-11-14T11:59:58Z", now), "just now");
    assert.equal(core.relativeTime("2026-11-14T11:58:00Z", now), "2m ago");
    assert.equal(core.relativeTime("2026-11-13T12:00:00Z", now), "1d ago");
    assert.equal(core.relativeTime("2026-01-01T00:00:00Z", now), "2026-01-01");
    assert.equal(core.relativeTime(null, now), "");
});
