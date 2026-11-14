<script setup lang="ts">
import { csrfToken, session } from "../api";
import { useEditor } from "../composables/editorContext";

const { isDirty, pageError, selectedGuild, resetDraft } = useEditor();

async function logout() {
    if (isDirty.value && !window.confirm("Log out and discard unsaved changes?")) return;
    const response = await fetch("/auth/logout", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken() },
    });
    if (response.ok) window.location.assign("/");
    else pageError.value = "Could not log out.";
}
</script>

<template>
    <header class="site-header">
        <div class="brand">
            <img src="/botchan-192.png" alt="" class="brand-avatar">
            <div>
                <p class="eyebrow">Configuration workshop</p>
                <h1>BotChan Guild Editor</h1>
            </div>
        </div>
        <div class="header-actions">
            <template v-if="session?.authenticated">
                <span class="user-chip">
                    <img v-if="session.user.avatar_url" :src="session.user.avatar_url" alt="">
                    {{ session.user.name }}
                </span>
                <button
                    v-if="selectedGuild?.installed"
                    class="button button-quiet"
                    type="button"
                    :disabled="!isDirty"
                    @click="resetDraft"
                >
                    Reset
                </button>
                <button class="button button-quiet" type="button" @click="logout">Log out</button>
            </template>
        </div>
    </header>
</template>
