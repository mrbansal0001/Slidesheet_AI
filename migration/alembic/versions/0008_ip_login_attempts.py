"""Add ip_login_attempts for coarse IP-based login throttling (email-enumeration defense).

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Same reasoning as 0007: on a brand-new database, migration 0001 already builds this table
    # from the current models. Guard so this is a no-op there, and a real CREATE only on a
    # database that predates the IpLoginAttempt model.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "ip_login_attempts" in inspector.get_table_names():
        return

    op.create_table(
        "ip_login_attempts",
        sa.Column("ip_address", sa.String(length=45), primary_key=True),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ip_login_attempts")
