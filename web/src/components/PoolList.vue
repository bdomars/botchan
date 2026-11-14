<script setup lang="ts">
import { nextTick } from "vue";
import { useEditor } from "../composables/editorContext";
import PoolCard from "./PoolCard.vue";

const { channels, channelsError, channelsLoading, draft, validation, addPool, loadChannels } =
    useEditor();

// Adds a pool and focuses its base channel picker, the first thing to set.
async function add() {
    addPool();
    await nextTick();
    const cards = document.querySelectorAll(".pool-card");
    cards[cards.length - 1]?.querySelector("select")?.focus();
}
</script>

<template>
    <div class="pools-heading">
        <div>
            <p class="section-number">02</p>
            <h2>Channel pools</h2>
        </div>
        <div class="header-actions">
            <button
                class="button button-secondary"
                type="button"
                :disabled="channelsLoading"
                @click="loadChannels"
            >{{ channelsLoading ? "Loading…" : "Refresh channels" }}</button>
            <button class="button button-primary" type="button" @click="add">
                <span aria-hidden="true">＋</span> Add pool
            </button>
        </div>
    </div>

    <p class="field-help pools-intro">
        Each pool grows from a voice channel you set up in Discord. BotChan copies
        that channel's category, permissions, bitrate and other settings to every
        channel it adds, and names them after it.
    </p>

    <p v-if="channelsError" class="field-error standalone-error" role="alert">
        {{ channelsError }}
    </p>
    <p
        v-else-if="channels && channels.length === 0"
        class="field-error standalone-error"
        role="status"
    >
        This server has no voice channels yet. Create one in Discord, set it up the
        way you want the pool to look, then refresh.
    </p>

    <p v-if="validation.errors.channel_pools" class="field-error standalone-error" role="alert">
        {{ validation.errors.channel_pools }}
    </p>

    <PoolCard
        v-for="(pool, index) in draft.channel_pools"
        :key="pool.client_id"
        :pool="pool"
        :index="index"
    />
</template>
