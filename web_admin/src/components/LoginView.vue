<script setup lang="ts">
import { authError } from "../state";
</script>

<template>
    <main class="content login">
        <h1>Operators only</h1>
        <p class="muted">Log in with a Discord account that has been granted operator access.</p>
        <p v-if="authError" class="alert-inline">{{ authError }}</p>
        <a class="button primary" href="/auth/discord">Log in with Discord</a>
    </main>
</template>
