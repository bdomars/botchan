<script setup lang="ts">
import { computed, nextTick, useTemplateRef } from "vue";
import { useGuildDetail } from "../composables/useGuildDetail";
import { copyText } from "../state";
import type { GuildSummary } from "../types";

defineProps<{ summary: GuildSummary }>();

const { state, editorParse, editorDirty, startEditing, cancelEditing, formatEditor, saveEditor } =
    useGuildDetail();
const editor = computed(() => state.editor);
const textarea = useTemplateRef<HTMLTextAreaElement>("textarea");

const rows = computed(() => Math.min(Math.max(editor.value.text.split("\n").length + 1, 8), 40));

async function edit() {
    startEditing();
    await nextTick();
    textarea.value?.focus();
}

// Tab indents instead of leaving the editor; Ctrl/Cmd+S saves.
async function onKeydown(event: KeyboardEvent) {
    if (!editor.value.editing) return;
    if (event.key === "Tab" && !event.shiftKey) {
        event.preventDefault();
        const area = event.target as HTMLTextAreaElement;
        const start = area.selectionStart;
        const text = editor.value.text;
        editor.value.text = text.slice(0, start) + "  " + text.slice(area.selectionEnd);
        await nextTick();
        area.selectionStart = area.selectionEnd = start + 2;
    } else if ((event.ctrlKey || event.metaKey) && event.key === "s") {
        event.preventDefault();
        if (editorParse.value.ok && editorDirty.value) void saveEditor();
    }
}
</script>

<template>
    <section class="panel">
        <div class="panel-head">
            <h2>
                Config
                <span v-if="editor.editing" class="badge badge-warn">editing</span>
                <span v-else class="badge">read-only</span>
            </h2>
            <div class="actions">
                <template v-if="!editor.editing">
                    <button type="button" class="button" @click="copyText(editor.text)">Copy</button>
                    <button
                        type="button"
                        class="button primary"
                        :disabled="!summary.installed"
                        :title="summary.installed ? '' : 'BotChan is not in this guild, so its config cannot be saved'"
                        @click="edit"
                    >Edit</button>
                </template>
                <template v-else>
                    <button type="button" class="button" @click="formatEditor">Format</button>
                    <button type="button" class="button" @click="cancelEditing">Cancel</button>
                    <button
                        type="button"
                        class="button primary"
                        :disabled="!editorParse.ok || !editorDirty || editor.saving"
                        @click="saveEditor"
                    >
                        {{ editor.saving ? "Saving…" : "Save" }}
                    </button>
                </template>
            </div>
        </div>
        <textarea
            ref="textarea"
            v-model="editor.text"
            class="json"
            :class="{ editing: editor.editing, invalid: editor.editing && !editorParse.ok }"
            :readonly="!editor.editing"
            spellcheck="false"
            autocomplete="off"
            aria-label="Guild config JSON"
            :rows="rows"
            @keydown="onKeydown"
        ></textarea>
        <p v-if="editor.editing && !editorParse.ok" class="error">{{ editorParse.error }}</p>
        <p v-if="editor.error" class="error" role="alert">{{ editor.error }}</p>
        <p v-if="editor.editing" class="hint muted">
            Sent as the PUT body. <span class="mono">base_name</span> is filled in from Discord on save.
            Every pool needs a <span class="mono">base_channel_id</span> from the channel list.
            Ctrl+S saves.
        </p>
    </section>
</template>
