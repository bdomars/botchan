// Shapes of the admin API's JSON responses (see botchan_api/admin/app.py).

export interface User {
    id: string;
    name: string;
    avatar_url: string | null;
}

export type Session =
    | { authenticated: false }
    | { authenticated: true; operator: true; csrf_token: string; user: User };

export interface GuildSummary {
    id: string;
    // null once the bot has left: only its stored config is known.
    name: string | null;
    icon_url: string | null;
    member_count: number | null;
    installed: boolean;
    needs_permissions: boolean;
    configured: boolean;
    revision: number | null;
    updated_at: string | null;
    install_url: string;
}

export interface GuildPage {
    guilds: GuildSummary[];
    total: number;
    limit: number;
    offset: number;
}

export interface Editor {
    id: string;
    name: string;
}

export interface RecentChange {
    guild_id: string;
    guild_name: string | null;
    revision: number;
    editor: Editor;
    as_operator: boolean;
    created_at: string;
}

export interface Stats {
    guilds: {
        installed: number;
        configured: number;
        unconfigured: number;
        needs_permissions: number;
        orphaned: number;
        members: number;
    };
    pools: number;
    revisions: { total: number; by_operator: number; last_7_days: number };
    operators: number;
    recent_changes: RecentChange[];
}

// The pools are shown and edited as raw JSON, so their shape is left open.
export type Pool = Record<string, unknown>;

export interface GuildConfig {
    guild_id: string;
    channel_pools: Pool[];
    revision: number | null;
    updated_at: string | null;
}

export interface Channel {
    id: string;
    name: string;
    category: string | null;
}

export interface VersionSummary {
    revision: number;
    editor: Editor;
    as_operator: boolean;
    pool_count: number;
    created_at: string;
}

export interface VersionPage {
    versions: VersionSummary[];
    next_before: number | null;
}

export interface Version {
    guild_id: string;
    revision: number;
    channel_pools: Pool[];
    editor: Editor;
    as_operator: boolean;
    created_at: string;
}

// The error body failing endpoints return; validation errors add details.
export interface ErrorBody {
    code?: string;
    message?: string;
    details?: { loc?: (string | number)[]; msg: string }[];
}
