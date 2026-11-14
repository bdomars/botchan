"""Add operators, admin sessions and the operator flag on config versions."""

import sqlalchemy as sa
from alembic import op

revision = "0002_admin"
down_revision = "0001_backend"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operators",
        sa.Column("discord_user_id", sa.String(length=20), primary_key=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "admin_sessions",
        sa.Column("id_hash", sa.String(length=64), primary_key=True),
        sa.Column("discord_user_id", sa.String(length=20), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("csrf_token", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_sessions_discord_user_id", "admin_sessions", ["discord_user_id"])
    op.create_index("ix_admin_sessions_expires_at", "admin_sessions", ["expires_at"])
    op.add_column(
        "guild_config_versions",
        sa.Column("as_operator", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("guild_config_versions", "as_operator")
    op.drop_table("admin_sessions")
    op.drop_table("operators")
