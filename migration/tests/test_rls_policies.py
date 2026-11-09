"""Defense-in-depth tests for PostgreSQL RLS (migrations 0005/0006).

Gated the same way as test_postgres_retrieval.py: skipped unless a real Postgres instance is
reachable. These deliberately bypass the app layer (raw SQL, no organization_id filter) to prove
the database itself — not just retrieval.py's WHERE clauses — refuses cross-tenant access.
"""
from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.db.session import derive_role_url

TEST_POSTGRES_URL = os.environ.get(
    "TEST_POSTGRES_URL", "postgresql+psycopg://chatbot:chatbot@localhost:5544/chatbot"
)


def _postgres_available() -> bool:
    try:
        engine = create_engine(TEST_POSTGRES_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(),
    reason=f"No reachable PostgreSQL at {TEST_POSTGRES_URL} — set TEST_POSTGRES_URL to run these",
)


@pytest.fixture
def rls_env():
    # Same override dance as test_postgres_retrieval.py's pg_session fixture: point the actual
    # settings source at Postgres for the duration of this fixture, then restore it.
    original_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_POSTGRES_URL
    get_settings.cache_clear()

    owner_engine = create_engine(TEST_POSTGRES_URL)
    try:
        with owner_engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))

        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")

        settings = get_settings()
        app_url = derive_role_url(TEST_POSTGRES_URL, settings.app_db_role, settings.app_db_password)
        app_engine = create_engine(app_url)
        try:
            yield owner_engine, app_engine
        finally:
            app_engine.dispose()
    finally:
        with owner_engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        owner_engine.dispose()
        if original_database_url is not None:
            os.environ["DATABASE_URL"] = original_database_url
        else:
            os.environ.pop("DATABASE_URL", None)
        get_settings.cache_clear()


def _seed_two_tenants(owner_engine) -> None:
    # created_at/updated_at only get populated by the ORM's Python-side default=utcnow — there's
    # no DB-level default, so raw SQL (as used here to bypass the app layer entirely) has to
    # supply them explicitly.
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO documents "
                "(id, organization_id, uploaded_by, filename, content_type, storage_key, "
                "size_bytes, status, created_at, updated_at) VALUES "
                "('doc-a', 'org-a', 'user-a', 'a.pptx', 'application/test', 'org-a/a.pptx', 10, 'ready', now(), now()), "
                "('doc-b', 'org-b', 'user-b', 'b.pptx', 'application/test', 'org-b/b.pptx', 10, 'ready', now(), now())"
            )
        )


def test_app_role_cannot_see_another_tenants_row_even_without_a_where_clause(rls_env):
    owner_engine, app_engine = rls_env
    _seed_two_tenants(owner_engine)

    with app_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.current_org_id', 'org-a', false)"))
        # No organization_id filter here on purpose — this is exactly the "forgot the WHERE
        # clause" failure mode RLS exists to catch.
        rows = conn.execute(text("SELECT id FROM documents")).fetchall()

    assert [row.id for row in rows] == ["doc-a"]


def test_app_role_cannot_insert_a_row_tagged_for_a_different_tenant(rls_env):
    _, app_engine = rls_env

    with app_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.current_org_id', 'org-a', false)"))
        with pytest.raises(Exception):
            conn.execute(
                text(
                    "INSERT INTO documents "
                    "(id, organization_id, uploaded_by, filename, content_type, storage_key, "
                    "size_bytes, status, created_at, updated_at) VALUES "
                    "('doc-x', 'org-b', 'user-a', 'x.pptx', 'application/test', "
                    "'org-b/x.pptx', 10, 'ready', now(), now())"
                )
            )


def test_app_role_sees_nothing_when_tenant_context_is_unset(rls_env):
    owner_engine, app_engine = rls_env
    _seed_two_tenants(owner_engine)

    with app_engine.connect() as conn:
        rows = conn.execute(text("SELECT id FROM documents")).fetchall()

    assert rows == []
