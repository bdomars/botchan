"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("./config-core.js");

function validDraft() {
    return {
        guild_id: "123456789012345678",
        channel_pools: [
            {
                base_channel_id: "223456789012345678",
                min_channels: "3",
                max_channels: "10",
                idle_value: "10",
                idle_unit: "minutes",
            },
        ],
    };
}

test("validates and serializes a guild snowflake as an exact string", () => {
    const json = core.serializeGuild(validDraft());

    assert.equal(JSON.parse(json).guild_id, "123456789012345678");
    assert.match(json, /"idle_seconds": 600/);
    assert.equal(json.split("\n")[1], '  "guild_id": "123456789012345678",');
});

const CHANNELS = [
    { id: "223456789012345678", name: "Other Games", category: "Gaming" },
    { id: "223456789012345679", name: "Other Games #2", category: "Gaming" },
    { id: "223456789012345680", name: "Raid Rooms", category: "Gaming" },
    { id: "223456789012345681", name: "AFK", category: null },
];

function pool(channelId) {
    return {
        base_channel_id: channelId,
        min_channels: "1",
        max_channels: "2",
        idle_value: "1",
        idle_unit: "minutes",
    };
}

test("a pool needs a base channel of its own and valid bounds", () => {
    const unpicked = validDraft();
    unpicked.channel_pools[0].base_channel_id = "";
    assert.match(core.validateDraft(unpicked).errors.pools[0].base_channel_id, /Pick/);

    const duplicate = validDraft();
    duplicate.channel_pools.push(pool("223456789012345678"));
    const duplicateResult = core.validateDraft(duplicate);
    assert.equal(duplicateResult.valid, false);
    assert.match(duplicateResult.errors.pools[0].base_channel_id, /its own/);
    assert.match(duplicateResult.errors.pools[1].base_channel_id, /its own/);

    const badBounds = validDraft();
    badBounds.channel_pools[0].min_channels = "5";
    badBounds.channel_pools[0].max_channels = "4";
    assert.match(
        core.validateDraft(badBounds).errors.pools[0].max_channels,
        /at least the minimum/,
    );
});

test("the saved pool carries the channel id, not a name", () => {
    const result = core.validateDraft(validDraft(), CHANNELS);

    assert.equal(result.valid, true);
    assert.deepEqual(result.value.channel_pools[0], {
        base_channel_id: "223456789012345678",
        min_channels: 3,
        max_channels: 10,
        idle_seconds: 600,
    });
});

test("extras must wait at least a minute before they are removed", () => {
    const draft = validDraft();
    draft.channel_pools[0].idle_value = "59";
    draft.channel_pools[0].idle_unit = "seconds";
    assert.match(core.validateDraft(draft).errors.pools[0].idle_seconds, /at least a minute/);

    draft.channel_pools[0].idle_value = "1";
    draft.channel_pools[0].idle_unit = "minutes";
    assert.equal(core.validateDraft(draft).valid, true);
});

test("a base channel deleted in Discord must be replaced", () => {
    const draft = validDraft();
    draft.channel_pools[0].base_channel_id = "999999999999999999";

    // Unknown until the channel list arrives.
    assert.equal(core.validateDraft(draft, null).valid, true);
    assert.match(core.validateDraft(draft, CHANNELS).errors.pools[0].base_channel_id, /deleted/);
});

test("a base channel cannot be another pool's extra", () => {
    const draft = validDraft();
    draft.channel_pools.push(pool("223456789012345679"));

    const result = core.validateDraft(draft, CHANNELS);

    assert.match(result.errors.pools[1].base_channel_id, /extra channel of the Other Games pool/);
    assert.equal(
        core.channelUnavailableReason(CHANNELS[1], draft.channel_pools, 1, CHANNELS),
        "an extra of the Other Games pool",
    );
    assert.equal(
        core.channelUnavailableReason(CHANNELS[0], draft.channel_pools, 1, CHANNELS),
        "used by another pool",
    );
    assert.equal(core.channelUnavailableReason(CHANNELS[2], draft.channel_pools, 1, CHANNELS), "");
});

test("names that would pass Discord's limit are rejected, counting emoji once", () => {
    const longName = "🎮" + "x".repeat(96);
    const channels = [{ id: "223456789012345678", name: longName, category: null }];
    const draft = validDraft();

    draft.channel_pools[0].max_channels = "9";
    assert.equal(core.validateDraft(draft, channels).valid, true);

    draft.channel_pools[0].max_channels = "10";
    assert.match(core.validateDraft(draft, channels).errors.pools[0].max_channels, /100 character/);
});

test("pools saved by name adopt the one channel with that name", () => {
    const draft = {
        guild_id: "123456789012345678",
        channel_pools: [
            core.poolToDraft({ base_name: "Raid Rooms", min_channels: 1, max_channels: 3, idle_seconds: 60 }),
            core.poolToDraft({ base_name: "Gone", min_channels: 1, max_channels: 3, idle_seconds: 60 }),
        ],
    };

    assert.equal(core.adoptPoolsByName(draft, CHANNELS), 1);
    assert.equal(draft.channel_pools[0].base_channel_id, "223456789012345680");
    assert.equal(draft.channel_pools[1].base_channel_id, "");
});

test("channels are grouped by category in sidebar order", () => {
    assert.deepEqual(
        core.groupChannels(CHANNELS).map((group) => [group.category, group.channels.length]),
        [["Gaming", 3], [null, 1]],
    );
});

test("converts supported durations to exact integer seconds", () => {
    assert.equal(core.durationToSeconds("1.5", "minutes"), 90);
    assert.equal(core.durationToSeconds("0", "hours"), 0);
    assert.equal(core.durationToSeconds("0.1", "seconds"), null);
    assert.deepEqual(core.secondsToDisplay(7200), { value: 2, unit: "hours" });
    assert.deepEqual(core.secondsToDisplay(600), { value: 10, unit: "minutes" });
    assert.deepEqual(core.secondsToDisplay(90), { value: 90, unit: "seconds" });
});

test("new guilds start empty and draft fingerprints ignore client ids", () => {
    assert.deepEqual(core.defaultDraft(), { guild_id: "", channel_pools: [] });
    const first = validDraft();
    const second = structuredClone(first);
    first.channel_pools[0].client_id = 1;
    second.channel_pools[0].client_id = 99;
    assert.equal(core.draftFingerprint(first), core.draftFingerprint(second));
});
