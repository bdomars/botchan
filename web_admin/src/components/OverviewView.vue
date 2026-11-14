<script setup lang="ts">
import { useStats } from "../composables/useStats";
import { ago, number } from "../format";

const { stats, loadStats } = useStats();
</script>

<template>
    <main class="content">
        <div class="heading">
            <h1>Overview</h1>
            <button type="button" class="button" :disabled="stats.loading" @click="loadStats">Refresh</button>
        </div>
        <p v-if="stats.loading && !stats.data" class="muted">Loading…</p>
        <template v-if="stats.data">
            <section class="tiles">
                <RouterLink class="tile" to="/guilds">
                    <span class="tile-value">{{ number(stats.data.guilds.installed) }}</span>
                    <span class="tile-label">installs</span>
                </RouterLink>
                <RouterLink class="tile" :to="{ name: 'guilds', query: { status: 'configured' } }">
                    <span class="tile-value">{{ number(stats.data.guilds.configured) }}</span>
                    <span class="tile-label">configured</span>
                </RouterLink>
                <RouterLink class="tile" :to="{ name: 'guilds', query: { status: 'unconfigured' } }">
                    <span class="tile-value">{{ number(stats.data.guilds.unconfigured) }}</span>
                    <span class="tile-label">installed, no config</span>
                </RouterLink>
                <RouterLink class="tile" :class="{ warn: stats.data.guilds.needs_permissions }" :to="{ name: 'guilds', query: { status: 'needs_permissions' } }">
                    <span class="tile-value">{{ number(stats.data.guilds.needs_permissions) }}</span>
                    <span class="tile-label">missing permissions</span>
                </RouterLink>
                <RouterLink class="tile" :to="{ name: 'guilds', query: { status: 'orphaned' } }">
                    <span class="tile-value">{{ number(stats.data.guilds.orphaned) }}</span>
                    <span class="tile-label">configs, bot removed</span>
                </RouterLink>
                <div class="tile">
                    <span class="tile-value">{{ number(stats.data.guilds.members) }}</span>
                    <span class="tile-label">members (approx.)</span>
                </div>
                <div class="tile">
                    <span class="tile-value">{{ number(stats.data.pools) }}</span>
                    <span class="tile-label">channel pools</span>
                </div>
                <div class="tile">
                    <span class="tile-value">{{ number(stats.data.revisions.total) }}</span>
                    <span class="tile-label">
                        revisions · {{ number(stats.data.revisions.last_7_days) }} this week ·
                        {{ number(stats.data.revisions.by_operator) }} by operators
                    </span>
                </div>
                <div class="tile">
                    <span class="tile-value">{{ number(stats.data.operators) }}</span>
                    <span class="tile-label">operators</span>
                </div>
            </section>

            <h2>Recent changes</h2>
            <p v-if="!stats.data.recent_changes.length" class="muted">No saves yet.</p>
            <div v-else class="scroll">
                <table class="grid">
                    <thead>
                        <tr><th>When</th><th>Guild</th><th class="num">Rev</th><th>Editor</th></tr>
                    </thead>
                    <tbody>
                        <tr v-for="change in stats.data.recent_changes" :key="change.guild_id + ':' + change.revision">
                            <td :title="change.created_at">{{ ago(change.created_at) }}</td>
                            <td>
                                <RouterLink :to="{ name: 'guild', params: { guildId: change.guild_id } }">{{ change.guild_name || change.guild_id }}</RouterLink>
                                <span v-if="!change.guild_name" class="badge">bot removed</span>
                            </td>
                            <td class="num mono">{{ change.revision }}</td>
                            <td>
                                {{ change.editor.name }}
                                <span v-if="change.as_operator" class="badge badge-operator">operator</span>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </template>
    </main>
</template>
