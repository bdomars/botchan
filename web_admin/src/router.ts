// The browser's router (hash history, so URLs stay "#/guilds/<id>"), and the
// loading of each route's data.

import {
    createWebHashHistory,
    isNavigationFailure,
    NavigationFailureType,
    type RouteLocationNormalized,
} from "vue-router";
import type { Route } from "./core";
import { createAdminRouter, fromLocation, toLocation } from "./routes";

export const router = createAdminRouter(createWebHashHistory());

type Loader = (route: Route, token: number) => Promise<void> | void;

let loader: Loader | null = null;
// Bumped on every load, so late responses for a page you have already left
// are ignored.
let loadToken = 0;

export function isCurrentLoad(token: number): boolean {
    return token === loadToken;
}

export function loadRoute(location: RouteLocationNormalized = router.currentRoute.value) {
    return loader?.(fromLocation(location), ++loadToken);
}

// Loads each route's data once navigation to it has succeeded. Called once
// the session is known; until then nothing loads.
export function startLoading(load: Loader) {
    loader = load;
    router.afterEach((to, _from, failure) => {
        if (!failure) void loadRoute(to);
    });
}

// Navigates to a route. Going to the page you are already on reloads it,
// which vue-router would otherwise skip as a duplicate navigation.
export async function go(route: Route, { replace = false } = {}) {
    const location = toLocation(route);
    const failure = await (replace ? router.replace(location) : router.push(location));
    if (isNavigationFailure(failure, NavigationFailureType.duplicated)) await loadRoute();
}

// The guild the current route shows, if it shows one.
export function routeGuildId(): string | null {
    const current = router.currentRoute.value;
    return current.name === "guild" ? String(current.params.guildId) : null;
}
