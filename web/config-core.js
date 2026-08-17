(function (root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    root.BotChanConfig = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    "use strict";

    const GUILD_ID_MARKER = "__BOTCHAN_GUILD_ID__:";
    const POOL_DEFAULTS = Object.freeze({
        base_name: "Other Games",
        min_channels: 3,
        max_channels: 10,
        idle_seconds: 600,
    });
    const UNIT_SECONDS = Object.freeze({
        seconds: 1,
        minutes: 60,
        hours: 3600,
    });

    function defaultDraft() {
        return {
            guild_id: "",
            channel_pools: [poolToDraft(POOL_DEFAULTS)],
        };
    }

    function poolToDraft(pool) {
        const idle = secondsToDisplay(pool.idle_seconds);
        return {
            base_name: String(pool.base_name),
            min_channels: String(pool.min_channels),
            max_channels: String(pool.max_channels),
            idle_value: String(idle.value),
            idle_unit: idle.unit,
        };
    }

    function secondsToDisplay(seconds) {
        if (seconds !== 0 && seconds % UNIT_SECONDS.hours === 0) {
            return { value: seconds / UNIT_SECONDS.hours, unit: "hours" };
        }
        if (seconds !== 0 && seconds % UNIT_SECONDS.minutes === 0) {
            return { value: seconds / UNIT_SECONDS.minutes, unit: "minutes" };
        }
        return { value: seconds, unit: "seconds" };
    }

    function durationToSeconds(value, unit) {
        const factor = UNIT_SECONDS[unit];
        const numericValue = Number(value);
        if (
            factor === undefined ||
            String(value).trim() === "" ||
            !Number.isFinite(numericValue) ||
            numericValue < 0
        ) {
            return null;
        }
        const seconds = numericValue * factor;
        return Number.isSafeInteger(seconds) ? seconds : null;
    }

    function parsePositiveInteger(value) {
        const text = String(value).trim();
        if (!/^[1-9]\d*$/.test(text)) {
            return null;
        }
        const parsed = Number(text);
        return Number.isSafeInteger(parsed) ? parsed : null;
    }

    function validateDraft(draft) {
        const errors = {
            guild_id: "",
            channel_pools: "",
            pools: [],
        };

        const guildId = String(draft && draft.guild_id !== undefined ? draft.guild_id : "").trim();
        if (!/^[1-9]\d*$/.test(guildId)) {
            errors.guild_id = "Select a guild.";
        }

        const pools = draft && Array.isArray(draft.channel_pools) ? draft.channel_pools : [];
        if (pools.length === 0) {
            errors.channel_pools = "Add at least one channel pool.";
        }

        const names = new Map();
        const normalizedPools = pools.map(function (pool, index) {
            const poolErrors = {
                base_name: "",
                min_channels: "",
                max_channels: "",
                idle_seconds: "",
            };
            const baseName = String(pool.base_name ?? "").trim();
            if (!baseName) {
                poolErrors.base_name = "Base name is required.";
            } else if (names.has(baseName)) {
                poolErrors.base_name = "Base names must be unique in this guild.";
                const firstIndex = names.get(baseName);
                errors.pools[firstIndex].base_name =
                    "Base names must be unique in this guild.";
            } else {
                names.set(baseName, index);
            }

            const minChannels = parsePositiveInteger(pool.min_channels);
            const maxChannels = parsePositiveInteger(pool.max_channels);
            if (minChannels === null) {
                poolErrors.min_channels = "Minimum must be an integer of at least 1.";
            }
            if (maxChannels === null) {
                poolErrors.max_channels = "Maximum must be an integer of at least 1.";
            } else if (minChannels !== null && maxChannels < minChannels) {
                poolErrors.max_channels = "Maximum must be at least the minimum.";
            }

            const idleSeconds = durationToSeconds(pool.idle_value, pool.idle_unit);
            if (idleSeconds === null) {
                poolErrors.idle_seconds =
                    "Timeout must resolve to a whole, non-negative number of seconds.";
            }

            errors.pools.push(poolErrors);
            return {
                base_name: baseName,
                min_channels: minChannels,
                max_channels: maxChannels,
                idle_seconds: idleSeconds,
            };
        });

        const valid =
            !errors.guild_id &&
            !errors.channel_pools &&
            errors.pools.every(function (poolErrors) {
                return Object.values(poolErrors).every(function (message) {
                    return !message;
                });
            });

        return {
            valid: valid,
            errors: errors,
            value: valid
                ? {
                      guild_id: guildId,
                      channel_pools: normalizedPools,
                  }
                : null,
        };
    }

    function serializeGuild(draft) {
        const validation = validateDraft(draft);
        if (!validation.valid) {
            throw new Error("Cannot serialize an invalid guild draft.");
        }

        const markerValue = GUILD_ID_MARKER + validation.value.guild_id;
        const json = JSON.stringify(
            {
                guild_id: markerValue,
                channel_pools: validation.value.channel_pools,
            },
            null,
            2,
        );
        return json.replace(
            JSON.stringify(markerValue),
            validation.value.guild_id,
        );
    }

    return Object.freeze({
        POOL_DEFAULTS: POOL_DEFAULTS,
        UNIT_SECONDS: UNIT_SECONDS,
        defaultDraft: defaultDraft,
        durationToSeconds: durationToSeconds,
        poolToDraft: poolToDraft,
        secondsToDisplay: secondsToDisplay,
        serializeGuild: serializeGuild,
        validateDraft: validateDraft,
    });
});
