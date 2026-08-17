"""Track merged volume jobs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 0001 runs create_all on the current models, so a fresh database
    # already has this column; only alter databases created before it existed.
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("jobs")}
    if "merged_candidate_ids" not in columns:
        op.add_column("jobs", sa.Column("merged_candidate_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "merged_candidate_ids")
