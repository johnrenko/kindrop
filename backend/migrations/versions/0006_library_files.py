"""Track Library Files for the OPDS Catalog and its Basic Auth settings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 0001 runs create_all on the current models, so a fresh database
    # already has this table and these columns; only alter older databases.
    inspector = inspect(op.get_bind())
    if "library_files" not in inspector.get_table_names():
        op.create_table(
            "library_files",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "revision_id", sa.String(36), sa.ForeignKey("revisions.id"), unique=True
            ),
            sa.Column("title", sa.String(500), nullable=False),
            sa.Column("series", sa.String(500), nullable=False),
            sa.Column("path", sa.String(2000), nullable=True),
            sa.Column("format", sa.String(10), nullable=True),
            sa.Column("size", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("error", sa.Text, nullable=True),
            sa.Column("mirrored_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_downloaded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("download_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_library_files_status", "library_files", ["status"])
    settings_columns = {
        column["name"] for column in inspect(op.get_bind()).get_columns("app_settings")
    }
    if "catalog_enabled" not in settings_columns:
        op.add_column(
            "app_settings",
            sa.Column("catalog_enabled", sa.Boolean, nullable=False, server_default="0"),
        )
    if "catalog_username" not in settings_columns:
        op.add_column("app_settings", sa.Column("catalog_username", sa.String(100), nullable=True))
    if "catalog_password" not in settings_columns:
        op.add_column("app_settings", sa.Column("catalog_password", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_table("library_files")
    op.drop_column("app_settings", "catalog_enabled")
    op.drop_column("app_settings", "catalog_username")
    op.drop_column("app_settings", "catalog_password")
