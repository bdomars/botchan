<script setup lang="ts">
import { onBeforeUnmount, onMounted, provide, ref } from "vue";
import { errorMessage, loadSession, session } from "./api";
import { EditorKey } from "./composables/editorContext";
import { useGuildEditor } from "./composables/useGuildEditor";
import { useToast } from "./composables/useToast";
import EditorIntro from "./components/EditorIntro.vue";
import GuildPicker from "./components/GuildPicker.vue";
import LoginPanel from "./components/LoginPanel.vue";
import PoolList from "./components/PoolList.vue";
import SavePanel from "./components/SavePanel.vue";
import SiteHeader from "./components/SiteHeader.vue";

const { toast, notify } = useToast();
const editor = useGuildEditor(notify);
provide(EditorKey, editor);
const { pageError, selectedGuild, isDirty } = editor;

const authLoading = ref(true);

async function initialize() {
    let authenticated = false;
    const authError = new URLSearchParams(window.location.search).get("auth_error");
    if (authError) {
        pageError.value = "Discord login did not complete. Please try again.";
        window.history.replaceState({}, "", "/");
    }
    try {
        authenticated = (await loadSession()).authenticated;
    } catch (error) {
        pageError.value = "Could not connect to BotChan: " + errorMessage(error);
    } finally {
        authLoading.value = false;
    }
    if (authenticated) await editor.loadGuilds();
}

function beforeUnload(event: BeforeUnloadEvent) {
    if (!isDirty.value) return;
    event.preventDefault();
    event.returnValue = "";
}

onMounted(() => {
    window.addEventListener("beforeunload", beforeUnload);
    void initialize();
});
onBeforeUnmount(() => window.removeEventListener("beforeunload", beforeUnload));
</script>

<template>
    <section v-if="authLoading" class="panel state-panel boot-panel" aria-live="polite">
        <h2>Loading BotChan…</h2>
    </section>

    <template v-else>
        <SiteHeader />

        <LoginPanel v-if="!session?.authenticated" />

        <p v-if="pageError" class="save-error page-error" role="alert">{{ pageError }}</p>

        <template v-if="session?.authenticated">
            <EditorIntro />

            <section class="guild-editor" aria-label="Guild settings">
                <GuildPicker />
                <template v-if="selectedGuild?.installed">
                    <PoolList />
                    <SavePanel />
                </template>
            </section>
        </template>

        <div v-if="toast" class="toast" role="status">{{ toast }}</div>
    </template>
</template>
