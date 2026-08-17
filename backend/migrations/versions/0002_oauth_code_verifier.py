"""Store the PKCE code verifier alongside the OAuth state."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 0001 crée le schéma depuis les modèles courants, qui incluent
    # déjà cette colonne sur une base vierge : n'ajouter que si absente.
    columns = {col["name"] for col in sa.inspect(op.get_bind()).get_columns("app_settings")}
    if "oauth_code_verifier" not in columns:
        op.add_column(
            "app_settings", sa.Column("oauth_code_verifier", sa.String(200), nullable=True)
        )


def downgrade() -> None:
    columns = {col["name"] for col in sa.inspect(op.get_bind()).get_columns("app_settings")}
    if "oauth_code_verifier" in columns:
        op.drop_column("app_settings", "oauth_code_verifier")
