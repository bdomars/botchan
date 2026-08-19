"""Create API sessions and versioned guild configuration."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_backend"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_sessions",
        sa.Column("id_hash", sa.String(length=64), primary_key=True),
        sa.Column("discord_user_id", sa.String(length=20), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("global_name", sa.String(length=128), nullable=True),
        sa.Column("avatar", sa.String(length=128), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("csrf_token", sa.String(length=128), nullable=False),
        sa.Column("guild_cache", postgresql.JSONB(), nullable=True),
        sa.Column("guild_cache_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_oauth_sessions_discord_user_id", "oauth_sessions", ["discord_user_id"])
    op.create_index("ix_oauth_sessions_expires_at", "oauth_sessions", ["expires_at"])
    op.create_table(
        "guild_configs",
        sa.Column("guild_id", sa.String(length=20), primary_key=True),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("editor_discord_user_id", sa.String(length=20), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "guild_config_versions",
        sa.Column("guild_id", sa.String(length=20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column("editor_discord_user_id", sa.String(length=20), nullable=False),
        sa.Column("editor_name", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["guild_id"], ["guild_configs.guild_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("guild_id", "revision"),
    )
    op.create_index(
        "ix_guild_config_versions_editor_discord_user_id",
        "guild_config_versions",
        ["editor_discord_user_id"],
    )


def downgrade() -> None:
    op.drop_table("guild_config_versions")
    op.drop_table("guild_configs")
    op.drop_table("oauth_sessions")
