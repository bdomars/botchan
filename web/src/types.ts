// Shapes of the API's JSON responses (see botchan_api/app.py).

export interface User {
    id: string;
    name: string;
    avatar_url: string | null;
}

export type Session =
    | { authenticated: false }
    | { authenticated: true; csrf_token: string; user: User };

export interface Guild {
    id: string;
    name: string;
    icon_url: string | null;
    installed: boolean;
    needs_permissions: boolean;
    configured: boolean;
    revision: number | null;
    install_url: string;
}

export interface Channel {
    id: string;
    name: string;
    category: string | null;
}

// A pool as the API stores it. Pools saved before the editor picked
// channels have only a base_name.
export interface PoolConfig {
    base_channel_id?: string | null;
    base_name?: string | null;
    min_channels: number;
    max_channels: number;
    idle_seconds: number;
}

export interface GuildConfig {
    guild_id: string;
    channel_pools: PoolConfig[];
    revision?: number | null;
    updated_at?: string | null;
}

// The error body every failing endpoint returns.
export interface ErrorBody {
    code?: string;
    message?: string;
}
