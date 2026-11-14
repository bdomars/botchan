<script setup lang="ts">
import { computed } from "vue";
import type { GuildSummary } from "../types";

const props = defineProps<{
    guild: Pick<GuildSummary, "installed" | "configured" | "needs_permissions">;
}>();

const badges = computed(() => {
    const guild = props.guild;
    const result: { text: string; kind: string }[] = [];
    if (!guild.installed) result.push({ text: "bot removed", kind: "badge-muted" });
    else if (!guild.configured) result.push({ text: "no config", kind: "badge-muted" });
    else result.push({ text: "configured", kind: "badge-ok" });
    if (guild.needs_permissions) result.push({ text: "missing permissions", kind: "badge-warn" });
    return result;
});
</script>

<template>
    <span v-for="badge in badges" :key="badge.text" class="badge" :class="badge.kind">{{ badge.text }}</span>
</template>
