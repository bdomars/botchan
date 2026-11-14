<script setup lang="ts">
import { onBeforeRouteLeave, onBeforeRouteUpdate } from "vue-router";
import { useGuildDetail } from "../composables/useGuildDetail";
import { ago, number } from "../format";
import ChannelList from "./ChannelList.vue";
import ConfigEditor from "./ConfigEditor.vue";
import StatusBadges from "./StatusBadges.vue";
import VersionHistory from "./VersionHistory.vue";

defineProps<{ guildId: string }>();

const { state, editorDirty } = useGuildDetail();

// An unsaved edit asks before you leave the guild, for another page or
// another guild; vue-router cancels the navigation if you keep it.
function confirmLeave(): boolean {
    return !editorDirty.value || window.confirm("Discard your unsaved edit?");
}
onBeforeRouteLeave(confirmLeave);
onBeforeRouteUpdate((to, from) => to.params.guildId === from.params.guildId || confirmLeave());
</script>

<template>
    <main class="content">
        <p class="crumbs"><RouterLink to="/guilds">Guilds</RouterLink> / <span class="mono">{{ guildId }}</span></p>
        <p v-if="state.guild.notFound" class="alert-inline">BotChan is not in guild {{ guildId }} and has no config for it.</p>
        <template v-if="state.guild.summary">
            <div class="heading">
                <h1>
                    <img v-if="state.guild.summary.icon_url" :src="state.guild.summary.icon_url" alt="" class="icon icon-lg">
                    {{ state.guild.summary.name || "Unknown guild" }}
                </h1>
                <StatusBadges :guild="state.guild.summary" />
            </div>
            <dl class="facts">
                <div><dt>ID</dt><dd class="mono">{{ state.guild.summary.id }}</dd></div>
                <div v-if="state.guild.summary.member_count != null">
                    <dt>Members</dt><dd>{{ number(state.guild.summary.member_count) }}</dd>
                </div>
                <div><dt>Revision</dt><dd class="mono">{{ state.guild.summary.revision ?? "none" }}</dd></div>
                <div v-if="state.guild.summary.updated_at">
                    <dt>Updated</dt>
                    <dd :title="state.guild.summary.updated_at">{{ ago(state.guild.summary.updated_at) }}</dd>
                </div>
                <div v-if="state.guild.summary.needs_permissions || !state.guild.summary.installed">
                    <dt>Install link</dt>
                    <dd><a :href="state.guild.summary.install_url" target="_blank" rel="noopener">re-authorize</a></dd>
                </div>
            </dl>

            <div class="columns">
                <ConfigEditor :summary="state.guild.summary" />
                <ChannelList :summary="state.guild.summary" />
            </div>

            <VersionHistory :summary="state.guild.summary" />
        </template>
    </main>
</template>
