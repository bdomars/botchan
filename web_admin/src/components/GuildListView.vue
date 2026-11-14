<script setup lang="ts">
import { onBeforeUnmount } from "vue";
import { PAGE_SIZE, type GuildFilter, type GuildFilters } from "../core";
import { useGuildList } from "../composables/useGuildList";
import { ago, number } from "../format";
import { go } from "../router";
import StatusBadges from "./StatusBadges.vue";

// From the address: "#/guilds?q=…&status=…&offset=…" (see routes.ts).
const props = defineProps<GuildFilters>();

const { guilds } = useGuildList();
let filterTimer: number | undefined;

// Typing filters after a short pause, without piling up history entries.
function filterInput(event: Event) {
    const value = (event.target as HTMLInputElement).value;
    window.clearTimeout(filterTimer);
    filterTimer = window.setTimeout(() => {
        go({ name: "guilds", ...props, q: value.trim(), offset: 0 }, { replace: true });
    }, 250);
}

function statusChange(event: Event) {
    const status = (event.target as HTMLSelectElement).value as GuildFilter;
    go({ name: "guilds", ...props, status, offset: 0 });
}

function page(direction: number) {
    const limit = guilds.data?.limit || PAGE_SIZE;
    go({ name: "guilds", ...props, offset: Math.max(0, props.offset + direction * limit) });
}

onBeforeUnmount(() => window.clearTimeout(filterTimer));
</script>

<template>
    <main class="content">
        <div class="heading">
            <h1>Guilds</h1>
            <span v-if="guilds.data" class="muted">{{ number(guilds.data.total) }} matching</span>
        </div>
        <div class="filters">
            <input
                :value="q"
                type="search"
                placeholder="Filter by name or ID prefix"
                aria-label="Filter guilds"
                spellcheck="false"
                @input="filterInput"
            >
            <select :value="status" aria-label="Status" @change="statusChange">
                <option value="all">All</option>
                <option value="configured">Configured</option>
                <option value="unconfigured">Installed, no config</option>
                <option value="needs_permissions">Missing permissions</option>
                <option value="orphaned">Bot removed (config left)</option>
            </select>
        </div>
        <p v-if="guilds.loading && !guilds.data" class="muted">Loading…</p>
        <template v-if="guilds.data">
            <p v-if="!guilds.data.guilds.length" class="muted">No guilds match.</p>
            <div v-else class="scroll">
                <table class="grid" :class="{ stale: guilds.loading }">
                    <thead>
                        <tr>
                            <th>Guild</th><th>ID</th><th class="num">Members</th>
                            <th>Status</th><th class="num">Rev</th><th>Updated</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr
                            v-for="guild in guilds.data.guilds"
                            :key="guild.id"
                            class="clickable"
                            @click="go({ name: 'guild', guildId: guild.id })"
                        >
                            <td>
                                <RouterLink :to="{ name: 'guild', params: { guildId: guild.id } }" @click.stop>
                                    <img v-if="guild.icon_url" :src="guild.icon_url" alt="" class="icon">
                                    {{ guild.name || "—" }}
                                </RouterLink>
                            </td>
                            <td class="mono muted">{{ guild.id }}</td>
                            <td class="num">{{ guild.member_count == null ? "" : number(guild.member_count) }}</td>
                            <td><StatusBadges :guild="guild" /></td>
                            <td class="num mono">{{ guild.revision ?? "" }}</td>
                            <td :title="guild.updated_at || ''">{{ ago(guild.updated_at) }}</td>
                        </tr>
                    </tbody>
                </table>
            </div>
            <div v-if="guilds.data.total > guilds.data.limit" class="pager">
                <button type="button" class="button" :disabled="offset === 0" @click="page(-1)">← Prev</button>
                <span class="muted">
                    {{ number(offset + 1) }}–{{ number(Math.min(offset + guilds.data.limit, guilds.data.total)) }}
                    of {{ number(guilds.data.total) }}
                </span>
                <button
                    type="button"
                    class="button"
                    :disabled="offset + guilds.data.limit >= guilds.data.total"
                    @click="page(1)"
                >Next →</button>
            </div>
        </template>
    </main>
</template>
