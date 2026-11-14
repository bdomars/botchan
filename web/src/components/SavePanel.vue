<script setup lang="ts">
import { useEditor } from "../composables/editorContext";

const { isSaving, saveError, validation, saveGuild } = useEditor();
</script>

<template>
    <div class="panel save-panel">
        <div class="save-copy">
            <h2>Save guild settings</h2>
            <p>Apply these channel pool settings to BotChan.</p>
            <p v-if="saveError" class="save-error" role="alert">
                {{ saveError }}
            </p>
        </div>
        <button
            class="button button-primary"
            type="button"
            :disabled="!validation.valid || isSaving"
            @click="saveGuild"
        >{{ isSaving ? "Saving…" : "Save" }}</button>
    </div>
</template>
