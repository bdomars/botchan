<script setup lang="ts">
import { useEditor } from "../composables/editorContext";

const { validation } = useEditor();
</script>

<template>
    <section class="intro" aria-labelledby="intro-title">
        <div>
            <p class="eyebrow">One guild at a time</p>
            <h2 id="intro-title">Shape your voice channel pools.</h2>
            <p>
                Tune the limits and cleanup timing, then save the validated guild
                configuration directly to BotChan.
            </p>
        </div>
        <div class="status-chip" :class="{ valid: validation.valid }">
            <span class="status-dot" aria-hidden="true"></span>
            {{ validation.valid ? "Ready to save" : "Draft needs attention" }}
        </div>
    </section>
</template>
