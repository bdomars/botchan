// The editor's state and actions: the guild list, the selected guild's draft,
// its voice channels and saving. Components get these through App.vue.

import { computed, ref } from "vue";
import * as core from "../core";
import type { Draft, IdleUnit, PoolDraft } from "../core";
import { api, csrfToken, errorMessage, guildPath } from "../api";
import type { Channel, Guild, GuildConfig } from "../types";

// A pool in the form, with a stable key for v-for.
export type EditorPool = PoolDraft & { client_id: number };
export type EditorDraft = Draft<EditorPool>;

let nextPoolId = 1;

function withClientIds(config: Pick<GuildConfig, "guild_id" | "channel_pools">): EditorDraft {
    return {
        guild_id: String(config.guild_id ?? ""),
        channel_pools: (config.channel_pools || []).map((pool) => ({
            client_id: nextPoolId++,
            ...core.poolToDraft(pool),
        })),
    };
}

function clone<T>(value: T): T {
    return JSON.parse(JSON.stringify(value)) as T;
}

export function useGuildEditor(notify: (message: string) => void) {
    const guildOptions = ref<Guild[]>([]);
    const guildLoading = ref(false);
    // The selected guild's voice channels; null until loaded.
    const channels = ref<Channel[] | null>(null);
    const channelsLoading = ref(false);
    const channelsError = ref("");
    const draft = ref<EditorDraft>({ ...core.defaultDraft(), channel_pools: [] });
    const loadedDraft = ref<EditorDraft | null>(null);
    const configEtag = ref<string | null>(null);
    const isSaving = ref(false);
    const saveError = ref("");
    const pageError = ref("");

    const validation = computed(() => core.validateDraft(draft.value, channels.value));
    const channelGroups = computed(() => core.groupChannels(channels.value));
    const selectedGuild = computed(
        () => guildOptions.value.find((guild) => guild.id === draft.value.guild_id) || null,
    );
    const isDirty = computed(() =>
        Boolean(
            loadedDraft.value &&
                core.draftFingerprint(draft.value) !== core.draftFingerprint(loadedDraft.value),
        ),
    );

    function setLoadedDraft(config: GuildConfig, etag: string | null) {
        draft.value = withClientIds(config);
        loadedDraft.value = clone(draft.value);
        configEtag.value = etag;
    }

    async function loadGuilds() {
        guildLoading.value = true;
        pageError.value = "";
        try {
            const { response, body } = await api<{ guilds: Guild[] }>("/api/guilds");
            if (!response.ok) throw new Error(body.message || "Could not load Discord guilds.");
            guildOptions.value = body.guilds;
        } catch (error) {
            pageError.value = errorMessage(error);
        } finally {
            guildLoading.value = false;
        }
    }

    // Switches to another guild, unless the user keeps their unsaved draft.
    // Returns false when they did, so the picker can show the old guild again.
    function selectGuild(guildId: string): boolean {
        if (guildId === draft.value.guild_id) return true;
        if (isDirty.value && !window.confirm("Discard unsaved changes and switch guilds?")) {
            return false;
        }
        void loadGuild(guildId);
        return true;
    }

    async function loadGuild(guildId: string) {
        const guild = guildOptions.value.find((item) => item.id === guildId);
        saveError.value = "";
        pageError.value = "";
        channels.value = null;
        channelsError.value = "";
        if (!guild || !guild.installed) {
            setLoadedDraft({ guild_id: guildId, channel_pools: [] }, null);
            return;
        }
        guildLoading.value = true;
        try {
            const { response, body } = await api<GuildConfig>(guildPath(guildId, "config"));
            if (body.code === "BOT_NOT_INSTALLED") guild.installed = false;
            if (!response.ok) throw new Error(body.message || "Could not load guild settings.");
            setLoadedDraft(body, response.headers.get("ETag"));
        } catch (error) {
            pageError.value = errorMessage(error);
            setLoadedDraft({ guild_id: guildId, channel_pools: [] }, null);
            return;
        } finally {
            guildLoading.value = false;
        }
        await loadChannels();
    }

    async function loadChannels() {
        const guildId = draft.value.guild_id;
        channelsLoading.value = true;
        channelsError.value = "";
        try {
            const { response, body } = await api<{ channels: Channel[] }>(
                guildPath(guildId, "channels"),
            );
            if (!response.ok) {
                throw new Error(body.message || "Could not load the server's channels.");
            }
            // The admin may have switched guilds while this loaded.
            if (guildId !== draft.value.guild_id) return;
            channels.value = body.channels;
            const adopted = core.adoptPoolsByName(draft.value, channels.value);
            if (adopted > 0) {
                notify("Matched existing pools to their channels. Save to keep it.");
            }
        } catch (error) {
            if (guildId === draft.value.guild_id) channelsError.value = errorMessage(error);
        } finally {
            channelsLoading.value = false;
        }
    }

    function addPool() {
        draft.value.channel_pools.push({
            client_id: nextPoolId++,
            ...core.poolToDraft(core.POOL_DEFAULTS),
        });
    }

    function removePool(index: number) {
        draft.value.channel_pools.splice(index, 1);
    }

    function movePool(index: number, direction: number) {
        const pools = draft.value.channel_pools;
        const target = index + direction;
        if (target < 0 || target >= pools.length) return;
        [pools[index], pools[target]] = [pools[target]!, pools[index]!];
    }

    function changeIdleUnit(pool: EditorPool, newUnit: IdleUnit) {
        const seconds = core.durationToSeconds(pool.idle_value, pool.idle_unit);
        pool.idle_unit = newUnit;
        if (seconds !== null) pool.idle_value = String(seconds / core.UNIT_SECONDS[newUnit]);
    }

    function resetDraft() {
        if (!loadedDraft.value) return;
        if (isDirty.value && !window.confirm("Discard your unsaved changes?")) return;
        draft.value = clone(loadedDraft.value);
        saveError.value = "";
        notify("Changes discarded");
    }

    async function saveGuild() {
        const result = validation.value;
        const guild = selectedGuild.value;
        if (!result.valid || isSaving.value || !guild?.installed) return;
        isSaving.value = true;
        saveError.value = "";
        const headers: Record<string, string> = {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken(),
        };
        if (configEtag.value) headers["If-Match"] = configEtag.value;
        else headers["If-None-Match"] = "*";
        try {
            const { response, body } = await api<GuildConfig>(
                guildPath(draft.value.guild_id, "config"),
                {
                    method: "PUT",
                    headers,
                    body: JSON.stringify({ channel_pools: result.value.channel_pools }),
                },
            );
            if (body.code === "BOT_NOT_INSTALLED") guild.installed = false;
            if (response.status === 412) {
                throw new Error(
                    "Someone else saved newer settings. Your draft is preserved; " +
                        "reload the guild before saving again.",
                );
            }
            if (!response.ok) {
                throw new Error(body.message || `The server returned HTTP ${response.status}.`);
            }
            setLoadedDraft(body, response.headers.get("ETag"));
            guild.configured = true;
            guild.revision = body.revision ?? null;
            notify("Guild saved");
        } catch (error) {
            saveError.value = "Could not save the guild: " + errorMessage(error);
        } finally {
            isSaving.value = false;
        }
    }

    return {
        guildOptions,
        guildLoading,
        channels,
        channelsLoading,
        channelsError,
        channelGroups,
        draft,
        isDirty,
        isSaving,
        saveError,
        pageError,
        selectedGuild,
        validation,
        loadGuilds,
        selectGuild,
        loadChannels,
        addPool,
        removePool,
        movePool,
        changeIdleUnit,
        resetDraft,
        saveGuild,
    };
}

export type GuildEditor = ReturnType<typeof useGuildEditor>;
