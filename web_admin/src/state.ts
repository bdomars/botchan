// App-wide state every view shares: the operator's session, the page-level
// error and the toast.

import { computed, ref } from "vue";
import type { Session } from "./types";

const TOAST_MS = 2600;

export const session = ref<Session>({ authenticated: false });
export const authenticated = computed(() => session.value.authenticated);
// Why the operator is looking at the login page, if they were sent there.
export const authError = ref("");
export const pageError = ref("");
export const toast = ref("");

let toastTimer: number | undefined;

export function notify(message: string) {
    toast.value = message;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
        toast.value = "";
    }, TOAST_MS);
}

export function errorMessage(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
}

export async function copyText(text: string) {
    try {
        await navigator.clipboard.writeText(text);
        notify("Copied");
    } catch {
        notify("Copy failed");
    }
}
