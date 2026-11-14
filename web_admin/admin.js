(function () {
    "use strict";

    if (!window.Vue || !window.BotChanAdmin) {
        document.getElementById("boot-error").hidden = false;
        return;
    }

    const { createApp } = window.Vue;
    const core = window.BotChanAdmin;
    const AUTH_ERRORS = {
        not_operator: "That Discord account is not a BotChan operator.",
        oauth_failed: "Discord login did not complete. Try again.",
        discord_unavailable: "Discord could not be reached. Try again.",
    };
    const numberFormat = new Intl.NumberFormat();

    function emptyGuildState() {
        return { summary: null, notFound: false };
    }

    createApp({
        data() {
            return {
                booting: true,
                session: { authenticated: false },
                authError: "",
                pageError: "",
                toast: "",
                toastTimer: null,
                route: core.parseRoute(window.location.hash),
                lookupText: "",
                filterTimer: null,
                // Bumped on every navigation, so late responses for a page
                // you have already left are ignored.
                loadToken: 0,
                stats: { data: null, loading: false },
                guilds: { data: null, loading: false },
                guild: emptyGuildState(),
                config: { channelPools: [], etag: null },
                editor: { text: "", original: "", editing: false, saving: false, error: "" },
                channels: { list: null, loading: false, error: "" },
                versions: { list: [], nextBefore: null, loading: false },
                viewing: null,
                showDiff: false,
            };
        },

        computed: {
            authenticated() {
                return Boolean(this.session.authenticated);
            },
            editorParse() {
                return core.parseBody(this.editor.text);
            },
            editorDirty() {
                return this.editor.editing && this.editor.text !== this.editor.original;
            },
            baseChannelIds() {
                return new Set(this.config.channelPools.map((pool) => pool.base_channel_id).filter(Boolean));
            },
            viewingText() {
                return this.viewing ? core.formatBody(this.viewing.channel_pools) : "";
            },
            // What rolling back to the viewed revision would change.
            viewingDiff() {
                return core.diffLines(core.formatBody(this.config.channelPools), this.viewingText);
            },
        },

        async mounted() {
            window.addEventListener("hashchange", this.onHashChange);
            window.addEventListener("beforeunload", this.beforeUnload);
            const params = new URLSearchParams(window.location.search);
            const authError = params.get("auth_error");
            if (authError) {
                this.authError = AUTH_ERRORS[authError] || "Login failed.";
                window.history.replaceState({}, "", "/" + window.location.hash);
            }
            try {
                const response = await fetch("/api/session", { headers: { Accept: "application/json" } });
                this.session = await response.json();
            } catch (error) {
                this.pageError = "Could not reach the admin API: " + error.message;
            } finally {
                this.booting = false;
            }
            if (this.authenticated) await this.loadRoute();
        },

        beforeUnmount() {
            window.removeEventListener("hashchange", this.onHashChange);
            window.removeEventListener("beforeunload", this.beforeUnload);
        },

        methods: {
            // --- navigation -------------------------------------------------

            go(route, { replace = false } = {}) {
                const hash = core.buildHash(route);
                if (hash === window.location.hash || (hash === "#/" && !window.location.hash)) {
                    this.route = core.parseRoute(hash);
                    this.loadRoute();
                } else if (replace) {
                    window.location.replace(hash);
                } else {
                    window.location.hash = hash;
                }
            },

            onHashChange(event) {
                const next = core.parseRoute(window.location.hash);
                const leavingGuild = this.route.name === "guild" &&
                    !(next.name === "guild" && next.guildId === this.route.guildId);
                if (leavingGuild && this.editorDirty && !window.confirm("Discard your unsaved edit?")) {
                    // Put the old address back without loading anything.
                    const back = new URL(event.oldURL).hash;
                    window.history.replaceState(null, "", back);
                    return;
                }
                this.route = next;
                this.loadRoute();
            },

            lookup() {
                const text = this.lookupText.trim();
                if (!text) return;
                this.lookupText = "";
                this.go(core.lookupRoute(text));
            },

            filterInput(value) {
                window.clearTimeout(this.filterTimer);
                this.filterTimer = window.setTimeout(() => {
                    this.go({ ...this.route, q: value.trim(), offset: 0 }, { replace: true });
                }, 250);
            },

            page(direction) {
                const limit = this.guilds.data?.limit || core.PAGE_SIZE;
                this.go({ ...this.route, offset: Math.max(0, this.route.offset + direction * limit) });
            },

            async loadRoute() {
                const token = ++this.loadToken;
                if (this.route.name === "overview") return this.loadStats();
                if (this.route.name === "guilds") return this.loadGuilds(token);
                return this.loadGuild(token);
            },

            // --- data ------------------------------------------------------

            async api(path, options = {}) {
                const headers = Object.assign({ Accept: "application/json" }, options.headers || {});
                if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = this.session.csrf_token;
                const response = await fetch(path, Object.assign({}, options, { headers }));
                const text = await response.text();
                let body = {};
                if (text) {
                    try { body = JSON.parse(text); } catch (_error) { body = { message: text }; }
                }
                if (response.status === 401 || body.code === "NOT_AN_OPERATOR") {
                    this.session = { authenticated: false };
                    this.authError = body.message || "Your session has ended. Log in again.";
                }
                return { response, body };
            },

            async loadStats() {
                this.stats.loading = true;
                try {
                    const { response, body } = await this.api("/api/stats");
                    if (!response.ok) throw new Error(body.message || `HTTP ${response.status}`);
                    this.stats.data = body;
                } catch (error) {
                    this.pageError = "Could not load stats: " + error.message;
                } finally {
                    this.stats.loading = false;
                }
            },

            async loadGuilds(token) {
                const query = new URLSearchParams({
                    q: this.route.q,
                    status: this.route.status,
                    offset: String(this.route.offset),
                    limit: String(core.PAGE_SIZE),
                });
                this.guilds.loading = true;
                try {
                    const { response, body } = await this.api("/api/guilds?" + query);
                    if (token !== this.loadToken) return;
                    if (!response.ok) throw new Error(body.message || `HTTP ${response.status}`);
                    this.guilds.data = body;
                } catch (error) {
                    if (token === this.loadToken) this.pageError = "Could not load guilds: " + error.message;
                } finally {
                    if (token === this.loadToken) this.guilds.loading = false;
                }
            },

            async loadGuild(token) {
                const guildId = this.route.guildId;
                const sameGuild = this.guild.summary?.id === guildId;
                if (!sameGuild) {
                    this.guild = emptyGuildState();
                    this.config = { channelPools: [], etag: null };
                    this.editor = { text: "", original: "", editing: false, saving: false, error: "" };
                    this.channels = { list: null, loading: false, error: "" };
                    this.versions = { list: [], nextBefore: null, loading: false };
                    this.viewing = null;
                }
                const { response, body } = await this.api(`/api/guilds/${guildId}`);
                if (token !== this.loadToken) return;
                if (response.status === 404) {
                    this.guild.notFound = true;
                    return;
                }
                if (!response.ok) {
                    this.pageError = body.message || `Could not load guild: HTTP ${response.status}`;
                    return;
                }
                this.guild.summary = body;
                await Promise.all([
                    this.loadConfig(),
                    this.loadVersions(false),
                    sameGuild && this.channels.list ? null : this.loadChannels(),
                ]);
            },

            async loadConfig() {
                const guildId = this.route.guildId;
                const { response, body } = await this.api(`/api/guilds/${guildId}/config`);
                if (guildId !== this.route.guildId) return;
                if (!response.ok) {
                    this.pageError = body.message || `Could not load config: HTTP ${response.status}`;
                    return;
                }
                this.config = { channelPools: body.channel_pools, etag: response.headers.get("ETag") };
                if (!this.editor.editing) {
                    const text = core.formatBody(body.channel_pools);
                    this.editor.text = text;
                    this.editor.original = text;
                }
            },

            async loadChannels() {
                const guildId = this.route.guildId;
                if (!this.guild.summary?.installed) return;
                this.channels = { list: null, loading: true, error: "" };
                const { response, body } = await this.api(`/api/guilds/${guildId}/channels`);
                if (guildId !== this.route.guildId) return;
                this.channels.loading = false;
                if (!response.ok) this.channels.error = body.message || `HTTP ${response.status}`;
                else this.channels.list = body.channels;
            },

            async loadVersions(more) {
                const guildId = this.route.guildId;
                const query = new URLSearchParams({ limit: "25" });
                if (more && this.versions.nextBefore) query.set("before", String(this.versions.nextBefore));
                this.versions.loading = true;
                const { response, body } = await this.api(`/api/guilds/${guildId}/versions?` + query);
                if (guildId !== this.route.guildId) return;
                this.versions.loading = false;
                if (!response.ok) {
                    this.pageError = body.message || `Could not load revisions: HTTP ${response.status}`;
                    return;
                }
                this.versions.list = more ? this.versions.list.concat(body.versions) : body.versions;
                this.versions.nextBefore = body.next_before;
            },

            // --- editor ----------------------------------------------------

            startEditing() {
                this.editor.editing = true;
                this.editor.error = "";
                this.editor.original = this.editor.text;
                this.$nextTick(() => this.$refs.editor?.focus());
            },

            cancelEditing() {
                if (this.editorDirty && !window.confirm("Discard your edit?")) return;
                this.editor.editing = false;
                this.editor.error = "";
                this.editor.text = core.formatBody(this.config.channelPools);
                this.editor.original = this.editor.text;
            },

            formatEditor() {
                if (this.editorParse.ok) this.editor.text = core.formatBody(this.editorParse.value.channel_pools);
            },

            editorKeydown(event) {
                if (!this.editor.editing) return;
                if (event.key === "Tab" && !event.shiftKey) {
                    event.preventDefault();
                    const area = event.target;
                    const start = area.selectionStart;
                    this.editor.text = this.editor.text.slice(0, start) + "  " + this.editor.text.slice(area.selectionEnd);
                    this.$nextTick(() => { area.selectionStart = area.selectionEnd = start + 2; });
                } else if ((event.ctrlKey || event.metaKey) && event.key === "s") {
                    event.preventDefault();
                    if (this.editorParse.ok && this.editorDirty) this.saveEditor();
                }
            },

            async saveEditor() {
                if (!this.editorParse.ok) return;
                const saved = await this.putConfig(this.editorParse.value.channel_pools);
                if (saved) {
                    this.editor.editing = false;
                    this.notify(`Saved revision ${saved}`);
                }
            },

            // Returns the new revision, or null with the reason in editor.error.
            async putConfig(channelPools) {
                const guildId = this.route.guildId;
                this.editor.saving = true;
                this.editor.error = "";
                const headers = { "Content-Type": "application/json" };
                if (this.config.etag) headers["If-Match"] = this.config.etag;
                else headers["If-None-Match"] = "*";
                try {
                    const { response, body } = await this.api(`/api/guilds/${guildId}/config`, {
                        method: "PUT",
                        headers,
                        body: JSON.stringify({ channel_pools: channelPools }),
                    });
                    if (response.status === 412) {
                        this.editor.error = "Someone saved this guild since you loaded it. Copy your edit, cancel, and start again from the new revision.";
                        return null;
                    }
                    if (!response.ok) {
                        const details = (body.details || []).map((item) => `${(item.loc || []).join(".")}: ${item.msg}`);
                        this.editor.error = [body.message || `HTTP ${response.status}`, ...details].join("\n");
                        return null;
                    }
                    this.config = { channelPools: body.channel_pools, etag: response.headers.get("ETag") };
                    this.editor.text = core.formatBody(body.channel_pools);
                    this.editor.original = this.editor.text;
                    this.guild.summary.revision = body.revision;
                    this.guild.summary.updated_at = body.updated_at;
                    this.guild.summary.configured = true;
                    await this.loadVersions(false);
                    return body.revision;
                } catch (error) {
                    this.editor.error = "Save failed: " + error.message;
                    return null;
                } finally {
                    this.editor.saving = false;
                }
            },

            // --- revisions -------------------------------------------------

            async fetchRevision(revision) {
                const { response, body } = await this.api(`/api/guilds/${this.route.guildId}/versions/${revision}`);
                if (!response.ok) throw new Error(body.message || `HTTP ${response.status}`);
                return body;
            },

            async viewRevision(revision) {
                if (this.viewing?.revision === revision) {
                    this.viewing = null;
                    return;
                }
                try {
                    this.viewing = await this.fetchRevision(revision);
                } catch (error) {
                    this.pageError = `Could not load revision ${revision}: ${error.message}`;
                }
            },

            async rollback(revision) {
                const name = this.guild.summary.name || this.guild.summary.id;
                const message = `Roll ${name} back to revision ${revision}?\n\n` +
                    `This saves revision ${revision}'s pools as a new revision on top of ${this.guild.summary.revision}.`;
                if (!window.confirm(message)) return;
                let target;
                try {
                    target = await this.fetchRevision(revision);
                } catch (error) {
                    this.pageError = `Could not load revision ${revision}: ${error.message}`;
                    return;
                }
                const saved = await this.putConfig(target.channel_pools);
                if (saved) {
                    this.notify(`Rolled back to revision ${revision} as revision ${saved}`);
                } else {
                    this.pageError = "Rollback failed: " + this.editor.error;
                    this.editor.error = "";
                }
            },

            // --- misc ------------------------------------------------------

            statusBadges(guild) {
                const badges = [];
                if (!guild.installed) badges.push({ text: "bot removed", kind: "badge-muted" });
                else if (!guild.configured) badges.push({ text: "no config", kind: "badge-muted" });
                else badges.push({ text: "configured", kind: "badge-ok" });
                if (guild.needs_permissions) badges.push({ text: "missing permissions", kind: "badge-warn" });
                return badges;
            },

            number(value) {
                return numberFormat.format(value ?? 0);
            },

            ago(iso) {
                return core.relativeTime(iso);
            },

            async copyText(text) {
                try {
                    await navigator.clipboard.writeText(text);
                    this.notify("Copied");
                } catch (_error) {
                    this.notify("Copy failed");
                }
            },

            async logout() {
                if (this.editorDirty && !window.confirm("Log out and discard your edit?")) return;
                await this.api("/auth/logout", { method: "POST" });
                window.location.assign("/");
            },

            beforeUnload(event) {
                if (!this.editorDirty) return;
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
