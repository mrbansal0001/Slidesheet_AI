"""Add embedding model/version provenance columns to document_chunks.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # As with 0002: 0001 builds its schema from the live Base.metadata, so a fresh database
    # running `alembic upgrade head` already has these columns by the time 0001 finishes. Guard
    # on the inspected schema so this revision is a no-op there, and a real ALTER on a database
    # that only ran migrations up through 0002, before these columns existed in the models.
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    chunk_columns = {column["name"] for column in inspector.get_columns("document_chunks")}
    with op.batch_alter_table("document_chunks") as batch_op:
        if "embedding_model" not in chunk_columns:
            batch_op.add_column(sa.Column("embedding_model", sa.String(100), nullable=True))
        if "embedding_dims" not in chunk_columns:
            batch_op.add_column(sa.Column("embedding_dims", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("document_chunks") as batch_op:
        batch_op.drop_column("embedding_dims")
        batch_op.drop_column("embedding_model")
