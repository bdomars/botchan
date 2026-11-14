import assert from "node:assert/strict";
import { test, vi } from "vitest";
import { createMemoryHistory } from "vue-router";
import type { Route } from "./core";
import { createAdminRouter, fromLocation, toLocation } from "./routes";

// Navigating loads each route's view, whose store imports the browser's
// router; these tests only need the route table, on in-memory history.
vi.mock("./router", () => ({ isCurrentLoad: () => true, routeGuildId: () => null }));

const GUILD = "123456789012345678";

async function visit(path: string) {
    const router = createAdminRouter(createMemoryHistory());
    await router.push(path);
    return router.currentRoute.value;
}

test("routes round-trip through the router, with the same URLs as before", async () => {
    const router = createAdminRouter(createMemoryHistory());
    const cases: [Route, string][] = [
        [{ name: "overview" }, "/"],
        [{ name: "guild", guildId: GUILD }, `/guilds/${GUILD}`],
        [{ name: "guilds", q: "odl", status: "orphaned", offset: 50 }, "/guilds?q=odl&status=orphaned&offset=50"],
        [{ name: "guilds", q: "", status: "all", offset: 0 }, "/guilds"],
    ];
    for (const [route, path] of cases) {
        await router.push(toLocation(route));
        assert.equal(router.currentRoute.value.fullPath, path);
        assert.deepEqual(fromLocation(router.currentRoute.value), route);
    }
});

test("unknown addresses and malformed guild IDs go to the overview", async () => {
    for (const path of ["/nope", "/guilds/not-an-id", "/guilds/0123", "/guilds/1/extra"]) {
        assert.equal((await visit(path)).name, "overview", path);
    }
});

test("malformed guild list filters fall back to their defaults", async () => {
    assert.deepEqual(fromLocation(await visit("/guilds?status=bogus&offset=-5")), {
        name: "guilds", q: "", status: "all", offset: 0,
    });
});
