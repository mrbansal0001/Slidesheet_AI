"""Add refresh_tokens.family_id for reuse-detection (revoke lineage, not just one token).

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # On a brand-new database, migration 0001 already builds the schema from the *current*
    # models (see its own docstring) — which already includes family_id, since it's now part of
    # the RefreshToken model. Guard so this migration is a no-op there, and a real ALTER only on
    # a database that predates this column (same pattern 0002 uses for the same reason).
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("refresh_tokens")}

    if "family_id" not in existing_columns:
        op.add_column("refresh_tokens", sa.Column("family_id", sa.String(length=36), nullable=True))
        # Existing tokens have no lineage to backfill from — each becomes its own single-token
        # family, which is exactly what a fresh login already produces.
        op.execute("UPDATE refresh_tokens SET family_id = id WHERE family_id IS NULL")
        op.alter_column("refresh_tokens", "family_id", nullable=False)

    existing_indexes = {index["name"] for index in inspector.get_indexes("refresh_tokens")}
    if "ix_refresh_tokens_family_id" not in existing_indexes:
        op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "family_id")
