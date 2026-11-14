import { createApp } from "vue";
import App from "./App.vue";
import { router } from "./router";
import { authError } from "./state";
import "./styles.css";

const AUTH_ERRORS: Record<string, string> = {
    not_operator: "That Discord account is not a BotChan operator.",
    oauth_failed: "Discord login did not complete. Try again.",
    discord_unavailable: "Discord could not be reached. Try again.",
};

// A failed login comes back as /?auth_error=…. Read it and tidy the address
// before the router starts, as it keeps its own state in the history entry.
const reason = new URLSearchParams(window.location.search).get("auth_error");
if (reason) {
    authError.value = AUTH_ERRORS[reason] || "Login failed.";
    window.history.replaceState(null, "", "/" + window.location.hash);
}

createApp(App).use(router).mount("#app");
