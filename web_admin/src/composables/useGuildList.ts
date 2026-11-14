import { reactive } from "vue";
import { api } from "../api";
import { PAGE_SIZE, type Route } from "../core";
import { isCurrentLoad } from "../router";
import { errorMessage, pageError } from "../state";
import type { GuildPage } from "../types";

type GuildsRoute = Extract<Route, { name: "guilds" }>;

// Kept between visits: the old page stays up, dimmed, while the next loads.
const guilds = reactive({ data: null as GuildPage | null, loading: false });

async function loadGuilds(route: GuildsRoute, token: number) {
    const query = new URLSearchParams({
        q: route.q,
        status: route.status,
        offset: String(route.offset),
        limit: String(PAGE_SIZE),
    });
    guilds.loading = true;
    try {
        const { response, body } = await api<GuildPage>("/api/guilds?" + query);
        if (!isCurrentLoad(token)) return;
        if (!response.ok) throw new Error(body.message || `HTTP ${response.status}`);
        guilds.data = body;
    } catch (error) {
        if (isCurrentLoad(token)) pageError.value = "Could not load guilds: " + errorMessage(error);
    } finally {
        if (isCurrentLoad(token)) guilds.loading = false;
    }
}

export function useGuildList() {
    return { guilds, loadGuilds };
}
