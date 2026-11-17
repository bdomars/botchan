"""Add bot_guilds: the servers the bot is in, as the bot itself sees them."""

import sqlalchemy as sa
from alembic import op

revision = "0003_bot_guilds"
down_revision = "0002_admin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_guilds",
        sa.Column("guild_id", sa.String(length=20), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("icon", sa.String(length=128), nullable=True),
        # Permission bits go past 2**31.
        sa.Column("permissions", sa.BigInteger(), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=True),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bot_guilds_present", "bot_guilds", ["present"])


def downgrade() -> None:
    op.drop_table("bot_guilds")
