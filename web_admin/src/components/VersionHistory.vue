<script setup lang="ts">
import { useGuildDetail } from "../composables/useGuildDetail";
import { ago } from "../format";
import { copyText } from "../state";
import type { GuildSummary } from "../types";

defineProps<{ summary: GuildSummary }>();

const { state, viewing, showDiff, viewingText, viewingDiff, loadVersions, viewRevision, rollback } =
    useGuildDetail();
</script>

<template>
    <section class="panel">
        <div class="panel-head"><h2>Revisions</h2></div>
        <p v-if="!state.versions.list.length && !state.versions.loading" class="muted">Never saved.</p>
        <div v-if="state.versions.list.length" class="scroll">
            <table class="grid">
                <thead>
                    <tr><th class="num">Rev</th><th>When</th><th>Editor</th><th class="num">Pools</th><th></th></tr>
                </thead>
                <tbody>
                    <template v-for="version in state.versions.list" :key="version.revision">
                        <tr :class="{ highlight: viewing && viewing.revision === version.revision }">
                            <td class="num mono">
                                {{ version.revision }}
                                <span v-if="version.revision === summary.revision" class="badge badge-ok">current</span>
                            </td>
                            <td :title="version.created_at">{{ ago(version.created_at) }}</td>
                            <td>
                                {{ version.editor.name }}
                                <span class="muted mono small">{{ version.editor.id }}</span>
                                <span v-if="version.as_operator" class="badge badge-operator">operator</span>
                            </td>
                            <td class="num">{{ version.pool_count ?? "" }}</td>
                            <td>
                                <div class="actions">
                                    <button type="button" class="button small" @click="viewRevision(version.revision)">{{ viewing && viewing.revision === version.revision ? "Hide" : "View" }}</button>
                                    <button
                                        type="button"
                                        class="button small danger"
                                        :disabled="version.revision === summary.revision || !summary.installed || state.editor.editing || state.editor.saving"
                                        @click="rollback(version.revision)"
                                    >Roll back</button>
                                </div>
                            </td>
                        </tr>
                        <!-- The open revision, right under its own row. -->
                        <tr v-if="viewing && viewing.revision === version.revision" class="viewer-row">
                            <td colspan="5">
                                <div class="viewer">
                                    <div class="panel-head">
                                        <h3>Revision {{ viewing.revision }} <span class="muted small">{{ viewing.created_at }}</span></h3>
                                        <div class="actions">
                                            <label class="toggle"><input v-model="showDiff" type="checkbox"> diff against current</label>
                                            <button type="button" class="button" @click="copyText(viewingText)">Copy</button>
                                        </div>
                                    </div>
                                    <pre v-if="!showDiff" class="json">{{ viewingText }}</pre>
                                    <pre v-else class="json diff"><span
                                        v-for="(line, index) in viewingDiff"
                                        :key="index"
                                        :class="{ add: line.op === '+', del: line.op === '-' }"
                                    >{{ line.op }} {{ line.text }}
</span></pre>
                                    <p v-if="showDiff" class="hint muted">What rolling back would change: − lines leave the current config, + lines come from revision {{ viewing.revision }}.</p>
                                </div>
                            </td>
                        </tr>
                    </template>
                </tbody>
            </table>
        </div>
        <button
            v-if="state.versions.nextBefore"
            type="button"
            class="button"
            :disabled="state.versions.loading"
            @click="loadVersions(true)"
        >Older revisions</button>
    </section>
</template>
