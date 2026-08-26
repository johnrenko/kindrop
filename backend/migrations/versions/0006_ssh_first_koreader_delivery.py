"""Add SSH-first KOReader delivery configuration and state."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    settings = _columns("app_settings")
    additions = {
        "ssh_host": sa.Column(
            "ssh_host", sa.String(253), nullable=False, server_default="192.168.1.53"
        ),
        "ssh_port": sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="2222"),
        "ssh_user": sa.Column("ssh_user", sa.String(100), nullable=False, server_default="root"),
        "ssh_key_path": sa.Column(
            "ssh_key_path",
            sa.String(2000),
            nullable=False,
            server_default="/run/secrets/kindle_ssh_key",
        ),
        "ssh_known_hosts_path": sa.Column(
            "ssh_known_hosts_path",
            sa.String(2000),
            nullable=False,
            server_default="/data/kindle_known_hosts",
        ),
        "ssh_destination": sa.Column(
            "ssh_destination",
            sa.String(2000),
            nullable=False,
            server_default="/mnt/us/documents/KOReader/Kindrop",
        ),
        "kindle_reserve_mib": sa.Column(
            "kindle_reserve_mib", sa.Integer(), nullable=False, server_default="100"
        ),
    }
    for name, column in additions.items():
        if name not in settings:
            op.add_column("app_settings", column)

    # KPW6 was the former generic default. This installation targets the user's
    # Paperwhite 1/2, whose KCC profile is KPW (758x1024).
    settings_table = sa.table(
        "app_settings",
        sa.column("id", sa.Integer()),
        sa.column("preset", sa.JSON()),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(settings_table.c.id, settings_table.c.preset)):
        preset = dict(row.preset or {})
        if preset.get("kindle_profile") == "KPW6":
            preset["kindle_profile"] = "KPW"
            connection.execute(
                settings_table.update()
                .where(settings_table.c.id == row.id)
                .values(preset=preset)
            )

    candidates = _columns("candidates")
    if "optimize" not in candidates:
        op.add_column("candidates", sa.Column("optimize", sa.Boolean(), nullable=True))

    jobs = _columns("jobs")
    if "optimize" not in jobs:
        op.add_column(
            "jobs", sa.Column("optimize", sa.Boolean(), nullable=False, server_default=sa.true())
        )
    if "delivery_transport" not in jobs:
        # Every pre-migration Job belonged to the historical Gmail workflow.
        op.add_column(
            "jobs",
            sa.Column(
                "delivery_transport", sa.String(16), nullable=False, server_default="gmail"
            ),
        )

    deliveries = _columns("deliveries")
    if "transport" not in deliveries:
        op.add_column(
            "deliveries",
            sa.Column("transport", sa.String(16), nullable=False, server_default="gmail"),
        )
    if "remote_path" not in deliveries:
        op.add_column("deliveries", sa.Column("remote_path", sa.String(2000), nullable=True))
    if "remote_sha256" not in deliveries:
        op.add_column("deliveries", sa.Column("remote_sha256", sa.String(64), nullable=True))


def downgrade() -> None:
    for name in ("remote_sha256", "remote_path", "transport"):
        if name in _columns("deliveries"):
            op.drop_column("deliveries", name)
    for name in ("delivery_transport", "optimize"):
        if name in _columns("jobs"):
            op.drop_column("jobs", name)
    if "optimize" in _columns("candidates"):
        op.drop_column("candidates", "optimize")
    for name in (
        "kindle_reserve_mib",
        "ssh_destination",
        "ssh_known_hosts_path",
        "ssh_key_path",
        "ssh_user",
        "ssh_port",
        "ssh_host",
    ):
        if name in _columns("app_settings"):
            op.drop_column("app_settings", name)
