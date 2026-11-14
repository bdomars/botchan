// Pure helpers for the admin frontend: routing, guild lookup, the JSON editor
// and revision diffs. No DOM access, so node --test can exercise them.
(function (root) {
    "use strict";

    const GUILD_FILTERS = ["all", "configured", "unconfigured", "needs_permissions", "orphaned"];
    const PAGE_SIZE = 50;

    function isSnowflake(value) {
        return /^[1-9][0-9]{0,19}$/.test(value);
    }

    // "#/", "#/guilds?q=x&status=orphaned&offset=50", "#/guilds/123".
    function parseRoute(hash) {
        const raw = String(hash || "").replace(/^#/, "");
        const [path, queryString = ""] = raw.split("?");
        const parts = path.split("/").filter(Boolean);
        const query = new URLSearchParams(queryString);
        if (parts[0] === "guilds" && parts.length === 2 && isSnowflake(parts[1])) {
            return { name: "guild", guildId: parts[1] };
        }
        if (parts[0] === "guilds" && parts.length === 1) {
            const status = query.get("status");
            const offset = Number.parseInt(query.get("offset") || "0", 10);
            return {
                name: "guilds",
                q: query.get("q") || "",
                status: GUILD_FILTERS.includes(status) ? status : "all",
                offset: Number.isSafeInteger(offset) && offset > 0 ? offset : 0,
            };
        }
        return { name: "overview" };
    }

    function buildHash(route) {
        if (route.name === "guild") return `#/guilds/${route.guildId}`;
        if (route.name === "guilds") {
            const query = new URLSearchParams();
            if (route.q) query.set("q", route.q);
            if (route.status && route.status !== "all") query.set("status", route.status);
            if (route.offset) query.set("offset", String(route.offset));
            const text = query.toString();
            return "#/guilds" + (text ? "?" + text : "");
        }
        return "#/";
    }

    // The header lookup: a whole ID opens that guild, anything else searches.
    function lookupRoute(input) {
        const value = String(input || "").trim();
        if (isSnowflake(value) && value.length >= 17) return { name: "guild", guildId: value };
        return { name: "guilds", q: value, status: "all", offset: 0 };
    }

    // The database hands keys back in its own order; show them in the order
    // people think about a pool, with anything unexpected after.
    const POOL_KEY_ORDER = ["base_channel_id", "base_name", "min_channels", "max_channels", "idle_seconds"];

    function orderedPool(pool) {
        if (pool === null || typeof pool !== "object" || Array.isArray(pool)) return pool;
        const ordered = {};
        for (const key of POOL_KEY_ORDER) if (key in pool) ordered[key] = pool[key];
        for (const key of Object.keys(pool)) if (!(key in ordered)) ordered[key] = pool[key];
        return ordered;
    }

    // What the editor shows and what a save sends: exactly the PUT body.
    function formatBody(channelPools) {
        return JSON.stringify({ channel_pools: (channelPools || []).map(orderedPool) }, null, 2);
    }

    function lineAndColumn(text, position) {
        const before = text.slice(0, position);
        const lines = before.split("\n");
        return { line: lines.length, column: lines[lines.length - 1].length + 1 };
    }

    // Returns { ok: true, value } or { ok: false, error }. Only the shape is
    // checked here; the server validates the pools themselves.
    function parseBody(text) {
        let value;
        try {
            value = JSON.parse(text);
        } catch (error) {
            const message = String(error.message || error);
            const match = /position (\d+)/.exec(message);
            if (match) {
                const at = lineAndColumn(text, Number(match[1]));
                return { ok: false, error: `Invalid JSON at line ${at.line}, column ${at.column}: ${message}` };
            }
            return { ok: false, error: "Invalid JSON: " + message };
        }
        if (value === null || typeof value !== "object" || Array.isArray(value)) {
            return { ok: false, error: 'Expected an object like {"channel_pools": [...]}.' };
        }
        const extra = Object.keys(value).filter((key) => key !== "channel_pools");
        if (extra.length) return { ok: false, error: `Unexpected key: ${extra.join(", ")}.` };
        if (!Array.isArray(value.channel_pools)) {
            return { ok: false, error: '"channel_pools" must be an array.' };
        }
        return { ok: true, value };
    }

    // A line diff (longest common subsequence), for comparing two pretty
    // printed configs. Entries are { op: " " | "-" | "+", text }.
    function diffLines(before, after) {
        const a = String(before).split("\n");
        const b = String(after).split("\n");
        const rows = a.length + 1;
        const cols = b.length + 1;
        const lcs = new Array(rows * cols).fill(0);
        for (let i = a.length - 1; i >= 0; i--) {
            for (let j = b.length - 1; j >= 0; j--) {
                lcs[i * cols + j] = a[i] === b[j]
                    ? lcs[(i + 1) * cols + j + 1] + 1
                    : Math.max(lcs[(i + 1) * cols + j], lcs[i * cols + j + 1]);
            }
        }
        const result = [];
        let i = 0;
        let j = 0;
        while (i < a.length && j < b.length) {
            if (a[i] === b[j]) {
                result.push({ op: " ", text: a[i] });
                i++;
                j++;
            } else if (lcs[(i + 1) * cols + j] >= lcs[i * cols + j + 1]) {
                result.push({ op: "-", text: a[i++] });
            } else {
                result.push({ op: "+", text: b[j++] });
            }
        }
        while (i < a.length) result.push({ op: "-", text: a[i++] });
        while (j < b.length) result.push({ op: "+", text: b[j++] });
        return result;
    }

    function relativeTime(iso, now) {
        if (!iso) return "";
        const seconds = Math.round(((now ?? Date.now()) - Date.parse(iso)) / 1000);
        if (!Number.isFinite(seconds)) return "";
        const steps = [
            [60, "s", 1],
            [3600, "m", 60],
            [86400, "h", 3600],
            [86400 * 60, "d", 86400],
        ];
        if (seconds < 5) return "just now";
        for (const [limit, unit, size] of steps) {
            if (seconds < limit) return `${Math.floor(seconds / size)}${unit} ago`;
        }
        return new Date(iso).toISOString().slice(0, 10);
    }

    const api = {
        GUILD_FILTERS,
        PAGE_SIZE,
        isSnowflake,
        parseRoute,
        buildHash,
        lookupRoute,
        formatBody,
        parseBody,
        diffLines,
        relativeTime,
    };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.BotChanAdmin = api;
})(typeof window !== "undefined" ? window : globalThis);
