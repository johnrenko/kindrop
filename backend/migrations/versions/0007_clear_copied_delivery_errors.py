"""Clear stale transport errors from successful Kindle copies."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jobs = sa.table(
        "jobs",
        sa.column("status", sa.String()),
        sa.column("error", sa.Text()),
    )
    op.execute(
        jobs.update()
        .where(jobs.c.status == "copied_to_kindle")
        .values(error=None)
    )


def downgrade() -> None:
    # Successful copies should not regain obsolete transport errors.
    pass
