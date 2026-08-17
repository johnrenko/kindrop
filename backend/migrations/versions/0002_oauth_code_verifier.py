"""Store the PKCE code verifier alongside the OAuth state."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("oauth_code_verifier", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("app_settings", "oauth_code_verifier")
