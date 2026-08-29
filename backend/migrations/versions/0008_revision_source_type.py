"""Identify whether a revision originated in Drive or a local upload."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("revisions") as batch:
        batch.add_column(sa.Column("source_type", sa.String(length=16), nullable=True))
    op.execute("UPDATE revisions SET source_type = 'drive' WHERE source_type IS NULL")
    with op.batch_alter_table("revisions") as batch:
        batch.alter_column("source_type", nullable=False, server_default="drive")
        batch.create_index("ix_revisions_source_type", ["source_type"])


def downgrade() -> None:
    with op.batch_alter_table("revisions") as batch:
        batch.drop_index("ix_revisions_source_type")
        batch.drop_column("source_type")
