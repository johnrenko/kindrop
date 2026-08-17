"""Track scan pause requests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 0001 runs create_all on the current models, so a fresh database
    # already has this column; only alter databases created before it existed.
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("scans")}
    if "pause_requested" not in columns:
        op.add_column(
            "scans",
            sa.Column("pause_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    op.drop_column("scans", "pause_requested")
