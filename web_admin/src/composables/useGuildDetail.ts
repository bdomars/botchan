// One guild: its summary, config (and the JSON editor over it), voice
// channels and revision history. The state lives at module level, so it
// survives the view being re-rendered for the same guild.

import { computed, reactive, ref } from "vue";
import { api, guildPath } from "../api";
import * as core from "../core";
import { isCurrentLoad, routeGuildId } from "../router";
import { errorMessage, notify, pageError } from "../state";
import type {
    Channel,
    GuildConfig,
    GuildSummary,
    Pool,
    Version,
    VersionPage,
    VersionSummary,
} from "../types";

function emptyState() {
    return {
        guild: { summary: null as GuildSummary | null, notFound: false },
        config: { channelPools: [] as Pool[], etag: null as string | null },
        editor: { text: "", original: "", editing: false, saving: false, error: "" },
        channels: { list: null as Channel[] | null, loading: false, error: "" },
        versions: { list: [] as VersionSummary[], nextBefore: null as number | null, loading: false },
    };
}

const state = reactive(emptyState());
const viewing = ref<Version | null>(null);
const showDiff = ref(false);

const editorParse = computed(() => core.parseBody(state.editor.text));
const editorDirty = computed(
    () => state.editor.editing && state.editor.text !== state.editor.original,
);
const baseChannelIds = computed(
    () =>
        new Set(
            state.config.channelPools
                .map((pool) => pool.base_channel_id)
                .filter((id): id is string => typeof id === "string" && id !== ""),
        ),
);
const viewingText = computed(() => (viewing.value ? core.formatBody(viewing.value.channel_pools) : ""));
// What rolling back to the viewed revision would change.
const viewingDiff = computed(() =>
    core.diffLines(core.formatBody(state.config.channelPools), viewingText.value),
);

// --- loading ---------------------------------------------------------------

async function loadGuild(guildId: string, token: number) {
    const sameGuild = state.guild.summary?.id === guildId;
    if (!sameGuild) {
        Object.assign(state, emptyState());
        viewing.value = null;
    }
    const { response, body } = await api<GuildSummary>(guildPath(guildId));
    if (!isCurrentLoad(token)) return;
    if (response.status === 404) {
        state.guild.notFound = true;
        return;
    }
    if (!response.ok) {
        pageError.value = body.message || `Could not load guild: HTTP ${response.status}`;
        return;
    }
    state.guild.summary = body;
    await Promise.all([
        loadConfig(),
        loadVersions(false),
        sameGuild && state.channels.list ? null : loadChannels(),
    ]);
}

async function loadConfig() {
    const guildId = routeGuildId();
    if (!guildId) return;
    const { response, body } = await api<GuildConfig>(guildPath(guildId, "config"));
    if (guildId !== routeGuildId()) return;
    if (!response.ok) {
        pageError.value = body.message || `Could not load config: HTTP ${response.status}`;
        return;
    }
    state.config = { channelPools: body.channel_pools, etag: response.headers.get("ETag") };
    if (!state.editor.editing) {
        const text = core.formatBody(body.channel_pools);
        state.editor.text = text;
        state.editor.original = text;
    }
}

async function loadChannels() {
    const guildId = routeGuildId();
    if (!guildId || !state.guild.summary?.installed) return;
    state.channels = { list: null, loading: true, error: "" };
    const { response, body } = await api<{ channels: Channel[] }>(guildPath(guildId, "channels"));
    if (guildId !== routeGuildId()) return;
    state.channels.loading = false;
    if (!response.ok) state.channels.error = body.message || `HTTP ${response.status}`;
    else state.channels.list = body.channels;
}

async function loadVersions(more: boolean) {
    const guildId = routeGuildId();
    if (!guildId) return;
    const query = new URLSearchParams({ limit: "25" });
    if (more && state.versions.nextBefore) query.set("before", String(state.versions.nextBefore));
    state.versions.loading = true;
    const { response, body } = await api<VersionPage>(guildPath(guildId, "versions?" + query));
    if (guildId !== routeGuildId()) return;
    state.versions.loading = false;
    if (!response.ok) {
        pageError.value = body.message || `Could not load revisions: HTTP ${response.status}`;
        return;
    }
    state.versions.list = more ? state.versions.list.concat(body.versions) : body.versions;
    state.versions.nextBefore = body.next_before;
}

// --- the JSON editor -------------------------------------------------------

function startEditing() {
    state.editor.editing = true;
    state.editor.error = "";
    state.editor.original = state.editor.text;
}

function cancelEditing() {
    if (editorDirty.value && !window.confirm("Discard your edit?")) return;
    state.editor.editing = false;
    state.editor.error = "";
    state.editor.text = core.formatBody(state.config.channelPools);
    state.editor.original = state.editor.text;
}

function formatEditor() {
    const parsed = editorParse.value;
    if (parsed.ok) state.editor.text = core.formatBody(parsed.value.channel_pools);
}

async function saveEditor() {
    const parsed = editorParse.value;
    if (!parsed.ok) return;
    const saved = await putConfig(parsed.value.channel_pools);
    if (saved) {
        state.editor.editing = false;
        notify(`Saved revision ${saved}`);
    }
}

// Returns the new revision, or null with the reason in editor.error.
async function putConfig(channelPools: unknown[]): Promise<number | null> {
    const guildId = routeGuildId();
    if (!guildId) return null;
    state.editor.saving = true;
    state.editor.error = "";
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (state.config.etag) headers["If-Match"] = state.config.etag;
    else headers["If-None-Match"] = "*";
    try {
        const { response, body } = await api<GuildConfig>(guildPath(guildId, "config"), {
            method: "PUT",
            headers,
            body: JSON.stringify({ channel_pools: channelPools }),
        });
        if (response.status === 412) {
            state.editor.error =
                "Someone saved this guild since you loaded it. Copy your edit, cancel, " +
                "and start again from the new revision.";
            return null;
        }
        if (!response.ok) {
            const details = (body.details || []).map(
                (item) => `${(item.loc || []).join(".")}: ${item.msg}`,
            );
            state.editor.error = [body.message || `HTTP ${response.status}`, ...details].join("\n");
            return null;
        }
        state.config = { channelPools: body.channel_pools, etag: response.headers.get("ETag") };
        state.editor.text = core.formatBody(body.channel_pools);
        state.editor.original = state.editor.text;
        const summary = state.guild.summary;
        if (summary) {
            summary.revision = body.revision;
            summary.updated_at = body.updated_at;
            summary.configured = true;
        }
        await loadVersions(false);
        return body.revision;
    } catch (error) {
        state.editor.error = "Save failed: " + errorMessage(error);
        return null;
    } finally {
        state.editor.saving = false;
    }
}

// --- revisions -------------------------------------------------------------

async function fetchRevision(revision: number): Promise<Version> {
    const { response, body } = await api<Version>(
        guildPath(routeGuildId() ?? "", `versions/${revision}`),
    );
    if (!response.ok) throw new Error(body.message || `HTTP ${response.status}`);
    return body;
}

async function viewRevision(revision: number) {
    if (viewing.value?.revision === revision) {
        viewing.value = null;
        return;
    }
    try {
        viewing.value = await fetchRevision(revision);
    } catch (error) {
        pageError.value = `Could not load revision ${revision}: ${errorMessage(error)}`;
    }
}

async function rollback(revision: number) {
    const summary = state.guild.summary;
    if (!summary) return;
    const name = summary.name || summary.id;
    const message =
        `Roll ${name} back to revision ${revision}?\n\n` +
        `This saves revision ${revision}'s pools as a new revision on top of ${summary.revision}.`;
    if (!window.confirm(message)) return;
    let target: Version;
    try {
        target = await fetchRevision(revision);
    } catch (error) {
        pageError.value = `Could not load revision ${revision}: ${errorMessage(error)}`;
        return;
    }
    const saved = await putConfig(target.channel_pools);
    if (saved) {
        notify(`Rolled back to revision ${revision} as revision ${saved}`);
    } else {
        pageError.value = "Rollback failed: " + state.editor.error;
        state.editor.error = "";
    }
}

export function useGuildDetail() {
    return {
        state,
        viewing,
        showDiff,
        editorParse,
        editorDirty,
        baseChannelIds,
        viewingText,
        viewingDiff,
        loadGuild,
        loadVersions,
        startEditing,
        cancelEditing,
        formatEditor,
        saveEditor,
        viewRevision,
        rollback,
    };
}
