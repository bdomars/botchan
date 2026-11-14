<script setup lang="ts">
import { ref } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api";
import { lookupRoute } from "../core";
import { useGuildDetail } from "../composables/useGuildDetail";
import { go } from "../router";
import { authenticated, session } from "../state";

const { editorDirty } = useGuildDetail();
const route = useRoute();
const lookupText = ref("");

function lookup() {
    const text = lookupText.value.trim();
    if (!text) return;
    lookupText.value = "";
    go(lookupRoute(text));
}

async function logout() {
    if (editorDirty.value && !window.confirm("Log out and discard your edit?")) return;
    await api("/auth/logout", { method: "POST" });
    window.location.assign("/");
}
</script>

<template>
    <header class="topbar">
        <RouterLink class="brand" to="/">
            <img src="/botchan-192.png" alt="">
            <span>BotChan <b>admin</b></span>
        </RouterLink>
        <template v-if="authenticated">
            <nav class="nav">
                <RouterLink to="/" :class="{ active: route.name === 'overview' }">Overview</RouterLink>
                <RouterLink to="/guilds" :class="{ active: route.name !== 'overview' }">Guilds</RouterLink>
            </nav>
            <form class="lookup" role="search" @submit.prevent="lookup">
                <input
                    v-model="lookupText"
                    type="search"
                    placeholder="Guild ID or name"
                    aria-label="Look up a guild by ID or name"
                    spellcheck="false"
                >
            </form>
            <div v-if="session.authenticated" class="who">
                <span class="badge badge-operator" title="Every save here is recorded as an operator edit">operator</span>
                <span class="muted">{{ session.user.name }}</span>
                <button type="button" class="link" @click="logout">Log out</button>
            </div>
        </template>
    </header>
</template>
