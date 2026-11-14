<script setup lang="ts">
import { computed } from "vue";
import * as core from "../core";
import type { IdleUnit } from "../core";
import { useEditor } from "../composables/editorContext";
import type { EditorPool } from "../composables/useGuildEditor";
import type { Channel } from "../types";

const props = defineProps<{ pool: EditorPool; index: number }>();

const { channels, channelGroups, draft, validation, changeIdleUnit, movePool, removePool } =
    useEditor();

// The form edits the pool in place: it is an item of the editor's reactive draft.
const pool = computed(() => props.pool);
const errors = computed(() => validation.value.errors.pools[props.index]);
const name = computed(() => core.poolBaseName(pool.value, channels.value));
const isLast = computed(() => props.index === draft.value.channel_pools.length - 1);

const baseMissing = computed(() =>
    Boolean(
        channels.value &&
            pool.value.base_channel_id &&
            !channels.value.some((channel) => channel.id === pool.value.base_channel_id),
    ),
);

function unavailableReason(channel: Channel): string {
    return core.channelUnavailableReason(
        channel,
        draft.value.channel_pools,
        props.index,
        channels.value,
    );
}

function channelLabel(channel: Channel): string {
    const reason = unavailableReason(channel);
    return reason ? `${channel.name} (${reason})` : channel.name;
}

function onUnitChange(event: Event) {
    changeIdleUnit(pool.value, (event.target as HTMLSelectElement).value as IdleUnit);
}

// The channels the pool would have at its maximum; dashed ones are extras.
const exampleChannels = computed(() => {
    const baseName = name.value || "Channel";
    const minimum = Number(pool.value.min_channels);
    const maximum = Number(pool.value.max_channels);
    const validMin = Number.isSafeInteger(minimum) && minimum > 0;
    const validMax = Number.isSafeInteger(maximum) && maximum > 0;
    if (!validMin && !validMax) return [];
    const count = validMax && maximum >= minimum ? maximum : validMin ? minimum : maximum;
    return Array.from({ length: Math.min(count, 100) }, (_, index) => ({
        number: index + 1,
        name: core.channelName(baseName, index + 1),
        extra: validMin && index + 1 > minimum,
    }));
});
</script>

<template>
    <article class="panel pool-card">
        <div class="pool-card-heading">
            <div>
                <span class="pool-index">{{ String(index + 1).padStart(2, "0") }}</span>
                <h3>{{ name || "New pool" }}</h3>
            </div>
            <div class="icon-actions">
                <button
                    class="icon-button"
                    type="button"
                    :disabled="index === 0"
                    :aria-label="'Move ' + (name || 'pool') + ' up'"
                    title="Move up"
                    @click="movePool(index, -1)"
                >↑</button>
                <button
                    class="icon-button"
                    type="button"
                    :disabled="isLast"
                    :aria-label="'Move ' + (name || 'pool') + ' down'"
                    title="Move down"
                    @click="movePool(index, 1)"
                >↓</button>
                <button
                    class="icon-button danger"
                    type="button"
                    :aria-label="'Remove ' + (name || 'pool')"
                    title="Remove pool"
                    @click="removePool(index)"
                >×</button>
            </div>
        </div>

        <div class="field-grid">
            <label class="field field-wide">
                <span class="field-label">Base channel</span>
                <select
                    v-model="pool.base_channel_id"
                    :disabled="channels === null"
                    :aria-invalid="Boolean(errors?.base_channel_id)"
                >
                    <option value="" disabled>
                        {{ channels === null ? "Loading channels…" : "Choose a voice channel" }}
                    </option>
                    <option
                        v-if="baseMissing"
                        :value="pool.base_channel_id"
                        disabled
                    >{{ pool.base_name || "Unknown channel" }} (deleted in Discord)</option>
                    <optgroup
                        v-for="(group, groupIndex) in channelGroups"
                        :key="groupIndex"
                        :label="group.category ?? 'No category'"
                    >
                        <option
                            v-for="channel in group.channels"
                            :key="channel.id"
                            :value="channel.id"
                            :disabled="Boolean(unavailableReason(channel))"
                        >{{ channelLabel(channel) }}</option>
                    </optgroup>
                </select>
                <span class="field-help">
                    To change the pool's name or settings, edit this channel in Discord.
                </span>
                <span v-if="errors?.base_channel_id" class="field-error" role="alert">
                    {{ errors.base_channel_id }}
                </span>
            </label>

            <label class="field">
                <span class="field-label">Minimum channels</span>
                <input
                    v-model="pool.min_channels"
                    type="number"
                    min="1"
                    step="1"
                    inputmode="numeric"
                    :aria-invalid="Boolean(errors?.min_channels)"
                >
                <span v-if="errors?.min_channels" class="field-error" role="alert">
                    {{ errors.min_channels }}
                </span>
            </label>

            <label class="field">
                <span class="field-label">Maximum channels</span>
                <input
                    v-model="pool.max_channels"
                    type="number"
                    min="1"
                    step="1"
                    inputmode="numeric"
                    :aria-invalid="Boolean(errors?.max_channels)"
                >
                <span v-if="errors?.max_channels" class="field-error" role="alert">
                    {{ errors.max_channels }}
                </span>
            </label>

            <div class="field field-wide">
                <span :id="'idle-label-' + pool.client_id" class="field-label">
                    Empty channel timeout
                </span>
                <div
                    class="duration-input"
                    role="group"
                    :aria-labelledby="'idle-label-' + pool.client_id"
                >
                    <input
                        v-model="pool.idle_value"
                        type="number"
                        min="0"
                        step="any"
                        inputmode="decimal"
                        :aria-invalid="Boolean(errors?.idle_seconds)"
                    >
                    <select :value="pool.idle_unit" aria-label="Timeout unit" @change="onUnitChange">
                        <option value="seconds">seconds</option>
                        <option value="minutes">minutes</option>
                        <option value="hours">hours</option>
                    </select>
                </div>
                <span class="field-help">
                    Empty extras are removed after this delay, which must be at least a minute.
                </span>
                <span v-if="errors?.idle_seconds" class="field-error" role="alert">
                    {{ errors.idle_seconds }}
                </span>
            </div>
        </div>

        <div
            class="channel-preview"
            aria-label="Channel pool preview. Solid channels are always kept; dashed channels are temporary extras."
        >
            <span
                v-for="channel in exampleChannels"
                :key="channel.number"
                :class="{ extra: channel.extra }"
            >🔊 {{ channel.name }}</span>
        </div>
    </article>
</template>
