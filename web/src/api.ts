import { ref } from "vue";
import type { ErrorBody, Session } from "./types";

// The logged-in user; null until /api/session has answered.
export const session = ref<Session | null>(null);

export function csrfToken(): string {
    return session.value?.authenticated ? session.value.csrf_token : "";
}

export async function loadSession(): Promise<Session> {
    const response = await fetch("/api/session", { headers: { Accept: "application/json" } });
    session.value = (await response.json()) as Session;
    return session.value;
}

export interface ApiResponse<T> {
    response: Response;
    // The parsed body. Callers check response.ok before trusting it as T;
    // on failure it holds the API's error code and message instead.
    body: T & ErrorBody;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<ApiResponse<T>> {
    const response = await fetch(path, {
        ...init,
        headers: { Accept: "application/json", ...init.headers },
    });
    if (response.status === 401) session.value = { authenticated: false };
    const text = await response.text();
    let body: unknown = {};
    if (text) {
        try {
            body = JSON.parse(text);
        } catch {
            body = { message: text };
        }
    }
    return { response, body: body as T & ErrorBody };
}

export function guildPath(guildId: string, rest: string): string {
    return `/api/guilds/${encodeURIComponent(guildId)}/${rest}`;
}

export function errorMessage(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
}
