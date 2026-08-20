"""Track the RFC 822 Message-ID of every delivery attempt."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 0001 runs create_all on the current models, so a fresh database
    # already has this column; only alter databases created before it existed.
    columns = {
        column["name"] for column in inspect(op.get_bind()).get_columns("delivery_attempts")
    }
    if "rfc822_message_id" not in columns:
        op.add_column(
            "delivery_attempts", sa.Column("rfc822_message_id", sa.String(300), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("delivery_attempts", "rfc822_message_id")
