"""Create the least-privilege runtime role used to enforce RLS (PostgreSQL only).

Migration 0006 enables Row-Level Security on the tenant-scoped tables. RLS has no effect on a
connection using the table owner role, and none at all on a superuser — regardless of FORCE ROW
LEVEL SECURITY (PostgreSQL exempts superusers unconditionally). So the app/worker runtime has to
connect as a separate, non-superuser, non-owner role for migration 0006 to actually do anything.
This migration creates that role and grants it exactly the DML privileges the app needs (no DDL,
no BYPASSRLS). Point `APP_DATABASE_URL` at this role once it exists; see app/db/session.py.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
from sqlalchemy import text

from app.core.config import get_settings

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite has no roles/RLS concept; the dev/test fallback keeps using the owner connection.
        return

    settings = get_settings()
    role = settings.app_db_role
    # Escape embedded single quotes for the string literal below — role/password are
    # operator-supplied config, not user input, but this keeps the DDL correct either way.
    password = settings.app_db_password.replace("'", "''")
    db_name = bind.execute(text("SELECT current_database()")).scalar()

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE "{role}" LOGIN PASSWORD '{password}'
                    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    # Roles aren't schema objects — they survive `DROP SCHEMA ... CASCADE` and outlive a single
    # migration run, so re-running `upgrade head` (e.g. after rotating APP_DB_PASSWORD) would
    # otherwise silently leave a stale password on an already-existing role. Always sync it.
    op.execute(f'ALTER ROLE "{role}" WITH PASSWORD \'{password}\';')
    op.execute(f'GRANT CONNECT ON DATABASE "{db_name}" TO "{role}";')
    op.execute(f'GRANT USAGE ON SCHEMA public TO "{role}";')
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}";')
    op.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}";')
    op.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{role}";'
    )
    # Managed providers (confirmed on Supabase) install pgvector into a dedicated `extensions`
    # schema rather than `public`, and only put it on the *owner* role's default search_path —
    # a plain Postgres/Docker install has no such schema, so this is a no-op there.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'extensions') THEN
                GRANT USAGE ON SCHEMA extensions TO "{role}";
                ALTER ROLE "{role}" SET search_path = "$user", public, extensions;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    settings = get_settings()
    role = settings.app_db_role
    db_name = bind.execute(text("SELECT current_database()")).scalar()

    # Undo every grant from upgrade(), in the reverse order — DROP ROLE fails with
    # "DependentObjectsStillExist" if any of these are left attached.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'extensions') THEN
                REVOKE USAGE ON SCHEMA extensions FROM "{role}";
            END IF;
        END
        $$;
        """
    )
    op.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        f'REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM "{role}";'
    )
    op.execute(f'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM "{role}";')
    op.execute(f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM "{role}";')
    op.execute(f'REVOKE USAGE ON SCHEMA public FROM "{role}";')
    op.execute(f'REVOKE CONNECT ON DATABASE "{db_name}" FROM "{role}";')
    op.execute(f'DROP ROLE IF EXISTS "{role}";')
