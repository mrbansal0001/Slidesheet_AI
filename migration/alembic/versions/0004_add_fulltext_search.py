"""Add tsvector full-text column + GIN index for hybrid retrieval (PostgreSQL only).

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite (dev/test fallback) has no full-text search; retrieval stays vector-only there,
        # same as it already is for the HNSW index added in 0001.
        return

    op.execute(
        """
        ALTER TABLE document_chunks
        ADD COLUMN IF NOT EXISTS text_search tsvector
        GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_chunks_text_search_gin
        ON document_chunks USING gin (text_search)
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_chunks_text_search_gin")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS text_search")
