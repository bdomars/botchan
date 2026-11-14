// Pure helpers for the admin frontend: routing, guild lookup, the JSON editor
// and revision diffs. No DOM access, so they are unit tested directly.

export const GUILD_FILTERS = [
    "all",
    "configured",
    "unconfigured",
    "needs_permissions",
    "orphaned",
] as const;
export type GuildFilter = (typeof GUILD_FILTERS)[number];

export const PAGE_SIZE = 50;

export interface GuildFilters {
    q: string;
    status: GuildFilter;
    offset: number;
}

// Where the app can be; routes.ts maps these to vue-router locations.
export type Route =
    | { name: "overview" }
    | ({ name: "guilds" } & GuildFilters)
    | { name: "guild"; guildId: string };

export function isSnowflake(value: string): boolean {
    return /^[1-9][0-9]{0,19}$/.test(value);
}

function isGuildFilter(value: string | null): value is GuildFilter {
    return (GUILD_FILTERS as readonly (string | null)[]).includes(value);
}

// A query value as vue-router hands it over: missing, one value, or repeated.
type QueryValue = string | null | undefined | (string | null)[];

function firstValue(value: QueryValue): string | null {
    return (Array.isArray(value) ? value[0] : value) ?? null;
}

// The guild list's "?q=x&status=orphaned&offset=50", with anything
// malformed falling back to its default.
export function parseGuildFilters(query: Record<string, QueryValue>): GuildFilters {
    const status = firstValue(query.status);
    const offset = Number.parseInt(firstValue(query.offset) || "0", 10);
    return {
        q: firstValue(query.q) || "",
        status: isGuildFilter(status) ? status : "all",
        offset: Number.isSafeInteger(offset) && offset > 0 ? offset : 0,
    };
}

// The query for those filters, leaving defaults out to keep URLs short.
export function guildFiltersQuery(filters: GuildFilters): Record<string, string> {
    const query: Record<string, string> = {};
    if (filters.q) query.q = filters.q;
    if (filters.status && filters.status !== "all") query.status = filters.status;
    if (filters.offset) query.offset = String(filters.offset);
    return query;
}

// The header lookup: a whole ID opens that guild, anything else searches.
export function lookupRoute(input: string): Route {
    const value = String(input || "").trim();
    if (isSnowflake(value) && value.length >= 17) return { name: "guild", guildId: value };
    return { name: "guilds", q: value, status: "all", offset: 0 };
}

// The database hands keys back in its own order; show them in the order
// people think about a pool, with anything unexpected after.
const POOL_KEY_ORDER = [
    "base_channel_id",
    "base_name",
    "min_channels",
    "max_channels",
    "idle_seconds",
];

function orderedPool(pool: unknown): unknown {
    if (pool === null || typeof pool !== "object" || Array.isArray(pool)) return pool;
    const source = pool as Record<string, unknown>;
    const ordered: Record<string, unknown> = {};
    for (const key of POOL_KEY_ORDER) if (key in source) ordered[key] = source[key];
    for (const key of Object.keys(source)) if (!(key in ordered)) ordered[key] = source[key];
    return ordered;
}

// What the editor shows and what a save sends: exactly the PUT body.
export function formatBody(channelPools: unknown[] | null | undefined): string {
    return JSON.stringify({ channel_pools: (channelPools || []).map(orderedPool) }, null, 2);
}

function lineAndColumn(text: string, position: number): { line: number; column: number } {
    const before = text.slice(0, position);
    const lines = before.split("\n");
    return { line: lines.length, column: lines[lines.length - 1]!.length + 1 };
}

export interface ConfigBody {
    channel_pools: unknown[];
}

export type ParseResult = { ok: true; value: ConfigBody } | { ok: false; error: string };

// Only the shape is checked here; the server validates the pools themselves.
export function parseBody(text: string): ParseResult {
    let value: unknown;
    try {
        value = JSON.parse(text);
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        const match = /position (\d+)/.exec(message);
        if (match) {
            const at = lineAndColumn(text, Number(match[1]));
            return {
                ok: false,
                error: `Invalid JSON at line ${at.line}, column ${at.column}: ${message}`,
            };
        }
        return { ok: false, error: "Invalid JSON: " + message };
    }
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
        return { ok: false, error: 'Expected an object like {"channel_pools": [...]}.' };
    }
    const extra = Object.keys(value).filter((key) => key !== "channel_pools");
    if (extra.length) return { ok: false, error: `Unexpected key: ${extra.join(", ")}.` };
    const pools = (value as { channel_pools?: unknown }).channel_pools;
    if (!Array.isArray(pools)) {
        return { ok: false, error: '"channel_pools" must be an array.' };
    }
    return { ok: true, value: { channel_pools: pools } };
}

export interface DiffLine {
    op: " " | "-" | "+";
    text: string;
}

// A line diff (longest common subsequence), for comparing two pretty
// printed configs.
export function diffLines(before: string, after: string): DiffLine[] {
    const a = String(before).split("\n");
    const b = String(after).split("\n");
    const rows = a.length + 1;
    const cols = b.length + 1;
    const lcs = new Array<number>(rows * cols).fill(0);
    const at = (i: number, j: number) => lcs[i * cols + j]!;
    for (let i = a.length - 1; i >= 0; i--) {
        for (let j = b.length - 1; j >= 0; j--) {
            lcs[i * cols + j] =
                a[i] === b[j] ? at(i + 1, j + 1) + 1 : Math.max(at(i + 1, j), at(i, j + 1));
        }
    }
    const result: DiffLine[] = [];
    let i = 0;
    let j = 0;
    while (i < a.length && j < b.length) {
        if (a[i] === b[j]) {
            result.push({ op: " ", text: a[i]! });
            i++;
            j++;
        } else if (at(i + 1, j) >= at(i, j + 1)) {
            result.push({ op: "-", text: a[i++]! });
        } else {
            result.push({ op: "+", text: b[j++]! });
        }
    }
    while (i < a.length) result.push({ op: "-", text: a[i++]! });
    while (j < b.length) result.push({ op: "+", text: b[j++]! });
    return result;
}

export function relativeTime(iso: string | null | undefined, now?: number): string {
    if (!iso) return "";
    const seconds = Math.round(((now ?? Date.now()) - Date.parse(iso)) / 1000);
    if (!Number.isFinite(seconds)) return "";
    const steps: [limit: number, unit: string, size: number][] = [
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
