import { authError, session } from "./state";
import type { ErrorBody, Session } from "./types";

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

// Every request but a GET carries the CSRF token. A 401, or an account that
// is no longer an operator, logs the page out.
export async function api<T>(path: string, init: RequestInit = {}): Promise<ApiResponse<T>> {
    const headers: Record<string, string> = {
        Accept: "application/json",
        ...(init.headers as Record<string, string> | undefined),
    };
    if (init.method && init.method !== "GET" && session.value.authenticated) {
        headers["X-CSRF-Token"] = session.value.csrf_token;
    }
    const response = await fetch(path, { ...init, headers });
    const text = await response.text();
    let body: T & ErrorBody = {} as T & ErrorBody;
    if (text) {
        try {
            body = JSON.parse(text) as T & ErrorBody;
        } catch {
            body = { message: text } as T & ErrorBody;
        }
    }
    if (response.status === 401 || body.code === "NOT_AN_OPERATOR") {
        session.value = { authenticated: false };
        authError.value = body.message || "Your session has ended. Log in again.";
    }
    return { response, body };
}

export function guildPath(guildId: string, rest = ""): string {
    return `/api/guilds/${guildId}` + (rest ? "/" + rest : "");
}
