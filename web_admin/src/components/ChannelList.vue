<script setup lang="ts">
import { useGuildDetail } from "../composables/useGuildDetail";
import { copyText } from "../state";
import type { GuildSummary } from "../types";

defineProps<{ summary: GuildSummary }>();

const { state, baseChannelIds } = useGuildDetail();
</script>

<template>
    <section class="panel">
        <div class="panel-head"><h2>Voice channels</h2></div>
        <p v-if="!summary.installed" class="muted">BotChan is not in this guild.</p>
        <p v-else-if="state.channels.loading" class="muted">Loading…</p>
        <p v-else-if="state.channels.error" class="error">{{ state.channels.error }}</p>
        <div v-else-if="state.channels.list" class="scroll">
            <table class="grid compact">
                <thead><tr><th>Name</th><th>ID</th></tr></thead>
                <tbody>
                    <tr
                        v-for="channel in state.channels.list"
                        :key="channel.id"
                        :class="{ highlight: baseChannelIds.has(channel.id) }"
                    >
                        <td>
                            {{ channel.name }}
                            <span v-if="channel.category" class="muted small">· {{ channel.category }}</span>
                        </td>
                        <td>
                            <button type="button" class="link mono" title="Copy ID" @click="copyText(channel.id)">{{ channel.id }}</button>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
    </section>
</template>
