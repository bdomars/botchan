// The admin app's routes. Kept apart from router.ts, which creates the
// browser's router, so tests can build one on in-memory history.

import {
    createRouter,
    type RouteLocationNormalized,
    type RouteLocationRaw,
    type RouterHistory,
} from "vue-router";
import { guildFiltersQuery, parseGuildFilters, type Route } from "./core";

// A guild ID, as core.isSnowflake checks it.
const SNOWFLAKE = "[1-9][0-9]{0,19}";

export function createAdminRouter(history: RouterHistory) {
    return createRouter({
        history,
        routes: [
            // Views load lazily: each is its own chunk, and the stores they use
            // can import the router without an import cycle.
            {
                path: "/",
                name: "overview",
                component: () => import("./components/OverviewView.vue"),
            },
            {
                path: "/guilds",
                name: "guilds",
                component: () => import("./components/GuildListView.vue"),
                props: (location) => parseGuildFilters(location.query),
            },
            {
                path: `/guilds/:guildId(${SNOWFLAKE})`,
                name: "guild",
                component: () => import("./components/GuildView.vue"),
                props: true,
            },
            // Unknown addresses, including malformed guild IDs, go to the overview.
            { path: "/:unknown(.*)*", redirect: "/" },
        ],
    });
}

export function toLocation(route: Route): RouteLocationRaw {
    if (route.name === "guild") return { name: "guild", params: { guildId: route.guildId } };
    if (route.name === "guilds") return { name: "guilds", query: guildFiltersQuery(route) };
    return { name: "overview" };
}

export function fromLocation(location: RouteLocationNormalized): Route {
    if (location.name === "guild") return { name: "guild", guildId: String(location.params.guildId) };
    if (location.name === "guilds") return { name: "guilds", ...parseGuildFilters(location.query) };
    return { name: "overview" };
}
