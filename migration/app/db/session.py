from __future__ import annotations

import re
from contextvars import ContextVar

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

_URL_CREDENTIALS_RE = re.compile(r"^(?P<scheme>[\w+]+://)(?P<user>[^:@/]+)(?::[^@]*)?@(?P<rest>.+)$")


def derive_role_url(owner_url: str, role: str, password: str) -> str:
    """Swap owner_url's credentials for `role`'s, keeping everything else (host, port, dbname,
    query params) unchanged — so the app role's connection can never drift from the owner's by
    accident (different host, stale copy-pasted password, ...).

    Preserves a `.`-suffix on the owner username if present, since managed poolers that route by
    username suffix (e.g. Supabase's Supavisor, which expects `<role>.<project-ref>`) need that
    same suffix on every role connecting through them, not just the owner — otherwise the
    connection is rejected before Postgres auth even runs. A plain Postgres/Docker connection has
    no such suffix, so this is a no-op there.
    """
    match = _URL_CREDENTIALS_RE.match(owner_url)
    if match is None:
        return owner_url
    suffix = match.group("user").split(".", 1)[1] if "." in match.group("user") else None
    new_user = f"{role}.{suffix}" if suffix else role
    return f"{match.group('scheme')}{new_user}:{password}@{match.group('rest')}"


# Runtime API/worker traffic connects as the least-privilege `app_db_role` (created by migration
# 0005) rather than the owner role migrations run as, so that PostgreSQL RLS (migration 0006)
# actually applies — table owners/superusers bypass RLS regardless of FORCE ROW LEVEL SECURITY.
# Derived automatically from `database_url` by default (same host/port/db, different
# user/password) rather than requiring a hand-assembled second connection string — `app_database_url`
# is only for the rare case where the app role genuinely needs a different host (e.g. a replica).
# Alembic always connects via `database_url` directly (see alembic/env.py), independent of this.
if settings.database_url.startswith("sqlite"):
    runtime_database_url = settings.database_url
else:
    runtime_database_url = settings.app_database_url or derive_role_url(
        settings.database_url, settings.app_db_role, settings.app_db_password
    )
connect_args = {"check_same_thread": False} if runtime_database_url.startswith("sqlite") else {}
engine = create_engine(runtime_database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

# Set once per request (in app.core.security.get_principal) or once per worker task
# (in app.workers.tasks) — mirrors the request_id_var pattern in app/core/logging.py.
current_org_id_var: ContextVar[str | None] = ContextVar("current_org_id", default=None)


@event.listens_for(Session, "after_begin")
def _apply_tenant_context(session, transaction, connection) -> None:
    """Re-applies the current tenant on every new transaction (i.e. after every commit too).

    PostgreSQL RLS policies (migration 0006) key off the `app.current_org_id` GUC. `SET LOCAL`
    resets at transaction end, and this codebase commits multiple times within a single request
    (e.g. app/services/ingestion.py), so a one-shot `SET LOCAL` right after opening the session
    would silently stop applying after the first commit. Hooking `after_begin` re-applies it on
    every transaction instead. No-op on SQLite (no RLS concept there) and when no tenant context
    is set (startup seeding, Alembic migrations — see their own `dialect.name` guards).
    """
    if connection.dialect.name != "postgresql":
        return
    org_id = current_org_id_var.get()
    if org_id is None:
        return
    connection.execute(text("SELECT set_config('app.current_org_id', :org_id, true)"), {"org_id": org_id})


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

