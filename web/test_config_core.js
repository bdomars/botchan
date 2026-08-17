"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("./config-core.js");

function validDraft() {
    return {
        guild_id: "123456789012345678",
        channel_pools: [
            {
                base_name: "Other Games",
                min_channels: "3",
                max_channels: "10",
                idle_value: "10",
                idle_unit: "minutes",
            },
        ],
    };
}

test("validates and serializes a guild without rounding its snowflake", () => {
    const json = core.serializeGuild(validDraft());

    assert.match(json, /"guild_id": 123456789012345678/);
    assert.doesNotMatch(json, /"guild_id": "/);
    assert.match(json, /"idle_seconds": 600/);
    assert.equal(json.split("\n")[1], '  "guild_id": 123456789012345678,');
});

test("rejects duplicate pool names and invalid bounds", () => {
    const duplicate = validDraft();
    duplicate.channel_pools.push({
        base_name: "Other Games",
        min_channels: "1",
        max_channels: "2",
        idle_value: "0",
        idle_unit: "seconds",
    });
    const duplicateResult = core.validateDraft(duplicate);
    assert.equal(duplicateResult.valid, false);
    assert.match(duplicateResult.errors.pools[0].base_name, /unique/);
    assert.match(duplicateResult.errors.pools[1].base_name, /unique/);

    const badBounds = validDraft();
    badBounds.channel_pools[0].min_channels = "5";
    badBounds.channel_pools[0].max_channels = "4";
    assert.match(
        core.validateDraft(badBounds).errors.pools[0].max_channels,
        /at least the minimum/,
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
