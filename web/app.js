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

    function addClientIds(draft) {
        return {
            guild_id: String(draft.guild_id ?? ""),
            channel_pools: draft.channel_pools.map(function (pool) {
                return Object.assign({ client_id: nextPoolId++ }, pool);
            }),
        };
    }

    createApp({
        data() {
            return {
                draft: addClientIds(core.defaultDraft()),
                guildOptions: [
                    { id: "123", name: "ODL" },
                    { id: "321", name: "Test" },
                ],
                isSaving: false,
                saveError: "",
                toast: "",
                toastTimer: null,
            };
        },

        computed: {
            validation() {
                return core.validateDraft(this.draft);
            },
        },

        methods: {
            addPool() {
                const pool = core.poolToDraft(core.POOL_DEFAULTS);
                pool.base_name = this.uniqueDefaultName();
                this.draft.channel_pools.push(
                    Object.assign({ client_id: nextPoolId++ }, pool),
                );
                this.$nextTick(function () {
                    const cards = document.querySelectorAll(".pool-card");
                    const lastCard = cards[cards.length - 1];
                    lastCard?.querySelector("input")?.focus();
                });
            },

            uniqueDefaultName() {
                const names = new Set(
                    this.draft.channel_pools.map(function (pool) {
                        return String(pool.base_name).trim();
                    }),
                );
                if (!names.has("Other Games")) {
                    return "Other Games";
                }
                let suffix = 2;
                while (names.has("Channel Pool " + suffix)) {
                    suffix += 1;
                }
                return "Channel Pool " + suffix;
            },

            removePool(index) {
                if (this.draft.channel_pools.length === 1) {
                    return;
                }
                this.draft.channel_pools.splice(index, 1);
            },

            movePool(index, direction) {
                const target = index + direction;
                if (target < 0 || target >= this.draft.channel_pools.length) {
                    return;
                }
                const pools = this.draft.channel_pools;
                [pools[index], pools[target]] = [pools[target], pools[index]];
            },

            changeIdleUnit(pool, newUnit) {
                const currentSeconds = core.durationToSeconds(
                    pool.idle_value,
                    pool.idle_unit,
                );
                pool.idle_unit = newUnit;
                if (currentSeconds !== null) {
                    pool.idle_value = String(
                        currentSeconds / core.UNIT_SECONDS[newUnit],
                    );
                }
            },

            exampleChannels(pool) {
                const baseName = String(pool.base_name || "").trim() || "Channel";
                const minChannels = Number(pool.min_channels);
                const maxChannels = Number(pool.max_channels);
                const validMinimum = Number.isSafeInteger(minChannels) && minChannels > 0;
                const validMaximum = Number.isSafeInteger(maxChannels) && maxChannels > 0;

                if (!validMinimum && !validMaximum) {
                    return [];
                }

                // Keep partially edited, invalid limits from creating an enormous DOM.
                const previewLimit = Math.min(
                    validMaximum && maxChannels >= minChannels
                        ? maxChannels
                        : validMinimum
                          ? minChannels
                          : maxChannels,
                    100,
                );

                return Array.from({ length: previewLimit }, function (_, index) {
                    const number = index + 1;
                    return {
                        number: number,
                        name: baseName + " #" + number,
                        extra: validMinimum && number > minChannels,
                    };
                });
            },

            resetDraft() {
                if (
                    !window.confirm("Reset this draft? Your unsaved changes will be lost.")
                ) {
                    return;
                }
                this.draft = addClientIds(core.defaultDraft());
                this.saveError = "";
                this.notify("Draft reset");
            },

            async saveGuild() {
                if (!this.validation.valid || this.isSaving) {
                    return;
                }
                this.isSaving = true;
                this.saveError = "";
                try {
                    const response = await window.fetch("/api/guildspec", {
                        method: "POST",
                        headers: {
                            Accept: "application/json",
                            "Content-Type": "application/json",
                        },
                        body: core.serializeGuild(this.draft),
                    });
                    if (!response.ok) {
                        const detail = (await response.text()).trim();
                        throw new Error(
                            detail || "The server returned HTTP " + response.status + ".",
                        );
                    }
                    this.notify("Guild saved");
                } catch (error) {
                    this.saveError = "Could not save the guild: " + error.message;
                } finally {
                    this.isSaving = false;
                }
            },

            notify(message) {
                this.toast = message;
                window.clearTimeout(this.toastTimer);
                this.toastTimer = window.setTimeout(() => {
                    this.toast = "";
                }, 2600);
            },
        },
    }).mount("#app");
})();
