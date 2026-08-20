(function () {
    "use strict";

    const bootError = document.getElementById("boot-error");
    if (!window.Vue || !window.BotChanConfig) {
        bootError.hidden = false;
        return;
    }

    const { createApp } = window.Vue;
    const core = window.BotChanConfig;
    let nextPoolId = 1;

    function addClientIds(config) {
        return {
            guild_id: String(config.guild_id ?? ""),
            channel_pools: (config.channel_pools || []).map(function (pool) {
                return Object.assign({ client_id: nextPoolId++ }, core.poolToDraft(pool));
            }),
        };
    }

    function clone(value) {
        return JSON.parse(JSON.stringify(value));
    }

    createApp({
        data() {
            return {
                session: null,
                authLoading: true,
                guildLoading: false,
                guildOptions: [],
                draft: addClientIds(core.defaultDraft()),
                loadedDraft: null,
                configEtag: null,
                isSaving: false,
                saveError: "",
                pageError: "",
                toast: "",
                toastTimer: null,
            };
        },

        computed: {
            validation() {
                return core.validateDraft(this.draft);
            },
            selectedGuild() {
                return this.guildOptions.find((guild) => guild.id === this.draft.guild_id) || null;
            },
            isDirty() {
                return Boolean(
                    this.loadedDraft &&
                    core.draftFingerprint(this.draft) !== core.draftFingerprint(this.loadedDraft),
                );
            },
        },

        async mounted() {
            window.addEventListener("beforeunload", this.beforeUnload);
            await this.initialize();
        },

        beforeUnmount() {
            window.removeEventListener("beforeunload", this.beforeUnload);
        },

        methods: {
            async initialize() {
                this.authLoading = true;
                const authError = new URLSearchParams(window.location.search).get("auth_error");
                if (authError) {
                    this.pageError = "Discord login did not complete. Please try again.";
                    window.history.replaceState({}, "", "/");
                }
                try {
                    const response = await fetch("/api/session", { headers: { Accept: "application/json" } });
                    this.session = await response.json();
                    if (this.session.authenticated) {
                        await this.loadGuilds();
                    }
                } catch (error) {
                    this.pageError = "Could not connect to BotChan: " + error.message;
                } finally {
                    this.authLoading = false;
                }
            },

            async loadGuilds() {
                this.guildLoading = true;
                this.pageError = "";
                try {
                    const response = await fetch("/api/guilds", { headers: { Accept: "application/json" } });
                    const body = await this.responseBody(response);
                    if (!response.ok) throw new Error(body.message || "Could not load Discord guilds.");
                    this.guildOptions = body.guilds;
                } catch (error) {
                    this.pageError = error.message;
                } finally {
                    this.guildLoading = false;
                }
            },

            async selectGuild(event) {
                const guildId = event.target.value;
                if (guildId === this.draft.guild_id) return;
                if (this.isDirty && !window.confirm("Discard unsaved changes and switch guilds?")) {
                    event.target.value = this.draft.guild_id;
                    return;
                }
                const guild = this.guildOptions.find((item) => item.id === guildId);
                this.saveError = "";
                this.pageError = "";
                if (!guild || !guild.installed) {
                    this.setLoadedDraft({ guild_id: guildId, channel_pools: [] }, null);
                    return;
                }
                this.guildLoading = true;
                try {
                    const response = await fetch(`/api/guilds/${encodeURIComponent(guildId)}/config`, {
                        headers: { Accept: "application/json" },
                    });
                    const body = await this.responseBody(response);
                    if (body.code === "BOT_NOT_INSTALLED") guild.installed = false;
                    if (!response.ok) throw new Error(body.message || "Could not load guild settings.");
                    this.setLoadedDraft(body, response.headers.get("ETag"));
                } catch (error) {
                    this.pageError = error.message;
                    this.setLoadedDraft({ guild_id: guildId, channel_pools: [] }, null);
                } finally {
                    this.guildLoading = false;
                }
            },

            setLoadedDraft(config, etag) {
                this.draft = addClientIds(config);
                this.loadedDraft = clone(this.draft);
                this.configEtag = etag;
            },

            addPool() {
                const pool = core.poolToDraft(core.POOL_DEFAULTS);
                pool.base_name = this.uniqueDefaultName();
                this.draft.channel_pools.push(Object.assign({ client_id: nextPoolId++ }, pool));
                this.$nextTick(function () {
                    const cards = document.querySelectorAll(".pool-card");
                    cards[cards.length - 1]?.querySelector("input")?.focus();
                });
            },

            uniqueDefaultName() {
                const names = new Set(this.draft.channel_pools.map((pool) => String(pool.base_name).trim()));
                if (!names.has("Other Games")) return "Other Games";
                let suffix = 2;
                while (names.has("Channel Pool " + suffix)) suffix += 1;
                return "Channel Pool " + suffix;
            },

            removePool(index) {
                this.draft.channel_pools.splice(index, 1);
            },

            movePool(index, direction) {
                const target = index + direction;
                if (target < 0 || target >= this.draft.channel_pools.length) return;
                [this.draft.channel_pools[index], this.draft.channel_pools[target]] = [
                    this.draft.channel_pools[target],
                    this.draft.channel_pools[index],
                ];
            },

            changeIdleUnit(pool, newUnit) {
                const seconds = core.durationToSeconds(pool.idle_value, pool.idle_unit);
                pool.idle_unit = newUnit;
                if (seconds !== null) pool.idle_value = String(seconds / core.UNIT_SECONDS[newUnit]);
            },

            exampleChannels(pool) {
                const baseName = String(pool.base_name || "").trim() || "Channel";
                const minimum = Number(pool.min_channels);
                const maximum = Number(pool.max_channels);
                const validMin = Number.isSafeInteger(minimum) && minimum > 0;
                const validMax = Number.isSafeInteger(maximum) && maximum > 0;
                if (!validMin && !validMax) return [];
                const limit = Math.min(validMax && maximum >= minimum ? maximum : validMin ? minimum : maximum, 100);
                return Array.from({ length: limit }, (_, index) => ({
                    number: index + 1,
                    name: index == 0 ? baseName : baseName + " #" + (index + 1),
                    extra: validMin && index + 1 > minimum,
                }));
            },

            resetDraft() {
                if (!this.loadedDraft || (this.isDirty && !window.confirm("Discard your unsaved changes?"))) return;
                this.draft = clone(this.loadedDraft);
                this.saveError = "";
                this.notify("Changes discarded");
            },

            async saveGuild() {
                if (!this.validation.valid || this.isSaving || !this.selectedGuild?.installed) return;
                this.isSaving = true;
                this.saveError = "";
                const headers = {
                    Accept: "application/json",
                    "Content-Type": "application/json",
                    "X-CSRF-Token": this.session.csrf_token,
                };
                if (this.configEtag) headers["If-Match"] = this.configEtag;
                else headers["If-None-Match"] = "*";
                try {
                    const response = await fetch(`/api/guilds/${encodeURIComponent(this.draft.guild_id)}/config`, {
                        method: "PUT",
                        headers,
                        body: JSON.stringify({ channel_pools: this.validation.value.channel_pools }),
                    });
                    const body = await this.responseBody(response);
                    if (body.code === "BOT_NOT_INSTALLED") this.selectedGuild.installed = false;
                    if (response.status === 412) {
                        throw new Error("Someone else saved newer settings. Your draft is preserved; reload the guild before saving again.");
                    }
                    if (!response.ok) throw new Error(body.message || `The server returned HTTP ${response.status}.`);
                    this.setLoadedDraft(body, response.headers.get("ETag"));
                    this.notify("Guild saved");
                    await this.loadGuilds();
                } catch (error) {
                    this.saveError = "Could not save the guild: " + error.message;
                } finally {
                    this.isSaving = false;
                }
            },

            async logout() {
                if (this.isDirty && !window.confirm("Log out and discard unsaved changes?")) return;
                const response = await fetch("/auth/logout", {
                    method: "POST",
                    headers: { "X-CSRF-Token": this.session.csrf_token },
                });
                if (response.ok) window.location.assign("/");
                else this.pageError = "Could not log out.";
            },

            async responseBody(response) {
                if (response.status === 401) this.session = { authenticated: false };
                const text = await response.text();
                if (!text) return {};
                try { return JSON.parse(text); } catch (_error) { return { message: text }; }
            },

            beforeUnload(event) {
                if (!this.isDirty) return;
                event.preventDefault();
                event.returnValue = "";
            },

            notify(message) {
                this.toast = message;
                window.clearTimeout(this.toastTimer);
                this.toastTimer = window.setTimeout(() => { this.toast = ""; }, 2600);
            },
        },
    }).mount("#app");
})();
