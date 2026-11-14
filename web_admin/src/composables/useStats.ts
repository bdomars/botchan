import { reactive } from "vue";
import { api } from "../api";
import { errorMessage, pageError } from "../state";
import type { Stats } from "../types";

// Kept between visits, so the overview shows the last numbers while it refreshes.
const stats = reactive({ data: null as Stats | null, loading: false });

async function loadStats() {
    stats.loading = true;
    try {
        const { response, body } = await api<Stats>("/api/stats");
        if (!response.ok) throw new Error(body.message || `HTTP ${response.status}`);
        stats.data = body;
    } catch (error) {
        pageError.value = "Could not load stats: " + errorMessage(error);
    } finally {
        stats.loading = false;
    }
}

export function useStats() {
    return { stats, loadStats };
}
