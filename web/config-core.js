(function (root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    root.BotChanConfig = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    "use strict";

    // Discord rejects longer channel names.
    const MAX_CHANNEL_NAME_LENGTH = 100;
    // Matches MIN_IDLE_SECONDS in botchan/config.py.
    const MIN_IDLE_SECONDS = 60;
    const POOL_DEFAULTS = Object.freeze({
        base_channel_id: "",
        base_name: "",
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
            channel_pools: [],
        };
    }

    function draftFingerprint(draft) {
        return JSON.stringify({
            guild_id: String(draft.guild_id || ""),
            channel_pools: (draft.channel_pools || []).map(function (pool) {
                return {
                    base_channel_id: String(pool.base_channel_id || ""),
                    min_channels: String(pool.min_channels),
                    max_channels: String(pool.max_channels),
                    idle_value: String(pool.idle_value),
                    idle_unit: String(pool.idle_unit),
                };
            }),
        });
    }

    function poolToDraft(pool) {
        const idle = secondsToDisplay(pool.idle_seconds);
        return {
            base_channel_id: pool.base_channel_id ? String(pool.base_channel_id) : "",
            // The name as last saved: shown when the channel has since gone.
            base_name: String(pool.base_name ?? ""),
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

    function channelName(baseName, number) {
        return number === 1 ? baseName : baseName + " #" + number;
    }

    // Counted in code points, as Python and Discord count them, so an emoji
    // is one character rather than two.
    function nameLength(name) {
        return Array.from(name).length;
    }

    // Mirrors the bot: "<base> #N" with N above 1 is that pool's extra.
    function looksLikeExtraOf(name, baseName) {
        const prefix = baseName + " #";
        if (!name.startsWith(prefix)) {
            return false;
        }
        const number = name.slice(prefix.length);
        return /^\d+$/.test(number) && Number(number) > 1;
    }

    function findChannel(channels, channelId) {
        return (channels || []).find(function (channel) {
            return channel.id === channelId;
        }) || null;
    }

    // The base channel's current name, falling back to the name last saved.
    function poolBaseName(pool, channels) {
        const channel = findChannel(channels, String(pool.base_channel_id || ""));
        return channel ? channel.name : String(pool.base_name || "");
    }

    function groupChannels(channels) {
        const groups = [];
        (channels || []).forEach(function (channel) {
            const last = groups[groups.length - 1];
            if (last && last.category === channel.category) {
                last.channels.push(channel);
            } else {
                groups.push({ category: channel.category, channels: [channel] });
            }
        });
        return groups;
    }

    // Why a channel cannot be the base of pools[poolIndex], or "".
    function channelUnavailableReason(channel, pools, poolIndex, channels) {
        for (let index = 0; index < pools.length; index += 1) {
            if (index === poolIndex) {
                continue;
            }
            const other = pools[index];
            if (String(other.base_channel_id || "") === channel.id) {
                return "used by another pool";
            }
            const otherName = poolBaseName(other, channels);
            if (otherName && looksLikeExtraOf(channel.name, otherName)) {
                return "an extra of the " + otherName + " pool";
            }
        }
        return "";
    }

    // Pools saved before the editor picked channels only know a name. Match
    // each to the one voice channel with that name, if there is exactly one.
    function adoptPoolsByName(draft, channels) {
        let adopted = 0;
        (draft.channel_pools || []).forEach(function (pool) {
            if (pool.base_channel_id || !pool.base_name) {
                return;
            }
            const matches = (channels || []).filter(function (channel) {
                return channel.name === pool.base_name;
            });
            if (matches.length === 1) {
                pool.base_channel_id = matches[0].id;
                adopted += 1;
            }
        });
        return adopted;
    }

    // channels is the guild's voice channel list, or null while it loads.
    function validateDraft(draft, channels) {
        const errors = {
            guild_id: "",
            channel_pools: "",
            pools: [],
        };
        const channelList = Array.isArray(channels) ? channels : null;

        const guildId = String(draft && draft.guild_id !== undefined ? draft.guild_id : "").trim();
        if (!/^[1-9]\d*$/.test(guildId)) {
            errors.guild_id = "Select a guild.";
        }

        const pools = draft && Array.isArray(draft.channel_pools) ? draft.channel_pools : [];
        if (pools.length === 0) {
            errors.channel_pools = "Add at least one channel pool.";
        }

        const channelIds = new Map();
        const normalizedPools = pools.map(function (pool, index) {
            const poolErrors = {
                base_channel_id: "",
                min_channels: "",
                max_channels: "",
                idle_seconds: "",
            };
            const channelId = String(pool.base_channel_id || "").trim();
            if (!channelId) {
                poolErrors.base_channel_id = "Pick the voice channel this pool grows from.";
            } else if (channelIds.has(channelId)) {
                poolErrors.base_channel_id = "Each pool needs its own base channel.";
                errors.pools[channelIds.get(channelId)].base_channel_id =
                    "Each pool needs its own base channel.";
            } else {
                channelIds.set(channelId, index);
                if (channelList && !findChannel(channelList, channelId)) {
                    poolErrors.base_channel_id =
                        "This channel was deleted in Discord, so the pool is paused. Pick another one.";
                }
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

            const baseName = channelList ? poolBaseName(pool, channelList) : "";
            if (
                baseName &&
                maxChannels !== null &&
                nameLength(channelName(baseName, maxChannels)) > MAX_CHANNEL_NAME_LENGTH
            ) {
                poolErrors.max_channels =
                    "\u201c" + channelName(baseName, maxChannels) + "\u201d would pass Discord's " +
                    MAX_CHANNEL_NAME_LENGTH + " character limit. Lower the maximum or " +
                    "shorten the channel's name in Discord.";
            }

            const idleSeconds = durationToSeconds(pool.idle_value, pool.idle_unit);
            if (idleSeconds === null) {
                poolErrors.idle_seconds =
                    "Timeout must resolve to a whole, non-negative number of seconds.";
            } else if (idleSeconds < MIN_IDLE_SECONDS) {
                poolErrors.idle_seconds =
                    "Timeout must be at least a minute, so people hopping in and out " +
                    "don't make BotChan create and delete channels over and over.";
            }

            errors.pools.push(poolErrors);
            return {
                base_channel_id: channelId,
                min_channels: minChannels,
                max_channels: maxChannels,
                idle_seconds: idleSeconds,
            };
        });

        if (channelList) {
            pools.forEach(function (pool, index) {
                const name = poolBaseName(pool, channelList);
                pools.forEach(function (other, otherIndex) {
                    const otherName = poolBaseName(other, channelList);
                    if (index === otherIndex || !name || !otherName || errors.pools[index].base_channel_id) {
                        return;
                    }
                    if (name === otherName) {
                        errors.pools[index].base_channel_id =
                            "Another pool's channel has the same name. Rename one of them in Discord.";
                    } else if (looksLikeExtraOf(name, otherName)) {
                        errors.pools[index].base_channel_id =
                            "This looks like an extra channel of the " + otherName + " pool.";
                    }
                });
            });
        }

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

        return JSON.stringify(
            {
                guild_id: validation.value.guild_id,
                channel_pools: validation.value.channel_pools,
            },
            null,
            2,
        );
    }

    return Object.freeze({
        MAX_CHANNEL_NAME_LENGTH: MAX_CHANNEL_NAME_LENGTH,
        MIN_IDLE_SECONDS: MIN_IDLE_SECONDS,
        POOL_DEFAULTS: POOL_DEFAULTS,
        adoptPoolsByName: adoptPoolsByName,
        channelName: channelName,
        channelUnavailableReason: channelUnavailableReason,
        groupChannels: groupChannels,
        poolBaseName: poolBaseName,
        UNIT_SECONDS: UNIT_SECONDS,
        defaultDraft: defaultDraft,
        draftFingerprint: draftFingerprint,
        durationToSeconds: durationToSeconds,
        poolToDraft: poolToDraft,
        secondsToDisplay: secondsToDisplay,
        serializeGuild: serializeGuild,
        validateDraft: validateDraft,
    });
});
