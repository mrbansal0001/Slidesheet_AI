"""Enable PostgreSQL Row-Level Security on tenant-scoped tables (defense in depth).

App-layer code already filters every tenant query by `organization_id` (see
app/services/retrieval.py, app/services/chat.py, app/api/routes/documents.py,
app/api/routes/conversations.py). This migration adds a database-level backstop so a missed or
future-buggy filter fails closed instead of leaking cross-tenant rows.

Policies key off the `app.current_org_id` session GUC, set per-request by the `after_begin`
listener in app/db/session.py (populated from the authenticated principal in
app/core/security.get_principal, or explicitly in app/workers/tasks.py for Celery jobs).

Caveats for anyone extending this later:
- FORCE ROW LEVEL SECURITY makes policies apply to the table owner too, but PostgreSQL always
  exempts superusers, no exception. If you're testing this locally as a superuser role (e.g. the
  default `postgres` user), RLS will silently appear to do nothing — that's expected, not a bug.
- A migration or script that runs DML against these tables without `app.current_org_id` set will
  see zero rows on SELECT and a policy violation on INSERT/UPDATE, since an unset GUC reads as
  NULL and `organization_id = NULL` is never true.
- `users`, `organizations`, `refresh_tokens`, and `memberships` are intentionally NOT covered here:
  the first three aren't per-tenant data, and `memberships` has no query path today that needs
  cross-org visibility scoped away — revisit if that changes.

Revision ID: 0006
Revises: 0005
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "documents",
    "document_chunks",
    "conversations",
    "messages",
    "ingestion_jobs",
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in TENANT_TABLES:
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;')
        op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY;')
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (organization_id = current_setting('app.current_org_id', true))
            WITH CHECK (organization_id = current_setting('app.current_org_id', true));
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in TENANT_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON {table};')
        op.execute(f'ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;')
        op.execute(f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;')
