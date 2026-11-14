<script setup lang="ts">
import { useEditor } from "../composables/editorContext";

const { draft, guildLoading, guildOptions, selectedGuild, validation, selectGuild } = useEditor();

function onChange(event: Event) {
    const select = event.target as HTMLSelectElement;
    // Kept the unsaved draft: show the guild it belongs to again.
    if (!selectGuild(select.value)) select.value = draft.value.guild_id;
}
</script>

<template>
    <article class="panel guild-panel">
        <div class="panel-heading">
            <div>
                <p class="section-number">01</p>
                <h2>Guild</h2>
            </div>
            <p>The Discord server BotChan will manage.</p>
        </div>
        <label class="field">
            <span class="field-label">Guild</span>
            <select
                :value="draft.guild_id"
                :disabled="guildLoading"
                :aria-invalid="Boolean(validation.errors.guild_id)"
                aria-describedby="guild-id-help guild-id-error"
                @change="onChange"
            >
                <option disabled value="">Select a guild</option>
                <option
                    v-for="guild in guildOptions"
                    :key="guild.id"
                    :value="guild.id"
                >{{ guild.name }}{{ guild.installed ? "" : " — BotChan not installed" }} ({{ guild.id }})</option>
            </select>
            <span id="guild-id-help" class="field-help">
                Choose the Discord server BotChan will manage.
            </span>
            <span
                v-if="validation.errors.guild_id"
                id="guild-id-error"
                class="field-error"
                role="alert"
            >
                {{ validation.errors.guild_id }}
            </span>
        </label>
    </article>

    <article v-if="selectedGuild && !selectedGuild.installed" class="panel install-panel">
        <div>
            <p class="eyebrow">Installation required</p>
            <h2>Add BotChan to {{ selectedGuild.name }}</h2>
            <p>BotChan needs permission to manage voice channels before this server can be configured.</p>
        </div>
        <a class="button button-primary" :href="selectedGuild.install_url">Add BotChan</a>
    </article>

    <article v-if="selectedGuild?.needs_permissions" class="panel install-panel">
        <div>
            <p class="eyebrow">Missing permission</p>
            <h2>BotChan is missing a permission</h2>
            <p>BotChan needs Manage Roles as well as Manage Channels, so it can copy permissions from a pool's base channel to the rest of the pool. Without it, changes to the base channel's permissions won't be copied to the rest of the pool, and new channels can't be created if the base channel uses permissions BotChan doesn't have.</p>
        </div>
        <a class="button button-primary" :href="selectedGuild.install_url">Update permissions</a>
    </article>
</template>
