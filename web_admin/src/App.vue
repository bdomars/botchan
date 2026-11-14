<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { loadSession } from "./api";
import type { Route } from "./core";
import { useGuildDetail } from "./composables/useGuildDetail";
import { useGuildList } from "./composables/useGuildList";
import { useStats } from "./composables/useStats";
import { loadRoute, router, startLoading } from "./router";
import { authenticated, errorMessage, pageError, toast } from "./state";
import LoginView from "./components/LoginView.vue";
import TopBar from "./components/TopBar.vue";

const { loadStats } = useStats();
const { loadGuilds } = useGuildList();
const { editorDirty, loadGuild } = useGuildDetail();

const booting = ref(true);

function load(route: Route, token: number) {
    if (route.name === "overview") return loadStats();
    if (route.name === "guilds") return loadGuilds(route, token);
    return loadGuild(route.guildId, token);
}

function beforeUnload(event: BeforeUnloadEvent) {
    if (!editorDirty.value) return;
    event.preventDefault();
    event.returnValue = "";
}

onMounted(async () => {
    window.addEventListener("beforeunload", beforeUnload);
    try {
        await loadSession();
    } catch (error) {
        pageError.value = "Could not reach the admin API: " + errorMessage(error);
    } finally {
        booting.value = false;
    }
    if (!authenticated.value) return;
    await router.isReady();
    startLoading(load);
    await loadRoute();
});

onBeforeUnmount(() => window.removeEventListener("beforeunload", beforeUnload));
</script>

<template>
    <TopBar />

    <p v-if="pageError" class="alert" role="alert">
        {{ pageError }}
        <button type="button" class="link" @click="pageError = ''">dismiss</button>
    </p>

    <main v-if="booting" class="content"><p class="muted">Loading…</p></main>
    <LoginView v-else-if="!authenticated" />
    <RouterView v-else />

    <div v-if="toast" class="toast" role="status">{{ toast }}</div>
</template>
