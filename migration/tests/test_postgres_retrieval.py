"""Integration tests against a real PostgreSQL+pgvector instance.

The rest of the suite runs on SQLite for speed and hermeticity, which means the pgvector/tsvector
SQL path in retrieval.py was previously never exercised by any automated test (an acknowledged
gap — see PROJECT.md Section 12). These tests close that gap, but only run when a real Postgres
instance is reachable: set TEST_POSTGRES_URL, e.g.

    export TEST_POSTGRES_URL=postgresql+psycopg://chatbot:chatbot@localhost:5544/chatbot

If it's not set or not reachable, every test in this file is skipped rather than failing — so a
normal `pytest` run on a machine without a Postgres container still passes.
"""
from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.models import Document, DocumentChunk, DocumentStatus
from app.services.retrieval import retrieve_chunks

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
def pg_session():
    # alembic/env.py sets its connection URL from get_settings().database_url — overriding
    # Config.set_main_option alone isn't enough since env.py re-reads settings itself. Point the
    # actual settings source at Postgres for the duration of this fixture, then restore it.
    # app.db.session's SQLite `engine`/`SessionLocal` were already bound at import time (before
    # this fixture ever runs), so the rest of the suite's DB access is completely unaffected.
    original_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_POSTGRES_URL
    get_settings.cache_clear()

    engine = create_engine(TEST_POSTGRES_URL)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))

        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")

        session_factory = sessionmaker(bind=engine)
        session = session_factory()
        try:
            yield session
        finally:
            session.close()
            with engine.begin() as conn:
                conn.execute(text("DROP SCHEMA public CASCADE"))
                conn.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()
        if original_database_url is not None:
            os.environ["DATABASE_URL"] = original_database_url
        else:
            os.environ.pop("DATABASE_URL", None)
        get_settings.cache_clear()


def _make_ready_document(session, org_id: str, filename: str) -> Document:
    document = Document(
        organization_id=org_id,
        uploaded_by="user-a",
        filename=filename,
        content_type="application/test",
        storage_key=f"{org_id}/{filename}",
        size_bytes=10,
        status=DocumentStatus.ready,
    )
    session.add(document)
    session.flush()
    return document


def _unit_vector(dims: int, hot_index: int) -> list[float]:
    vector = [0.0] * dims
    vector[hot_index] = 1.0
    return vector


def test_tsvector_column_and_gin_index_exist(pg_session):
    rows = pg_session.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'document_chunks' AND indexname = 'ix_chunks_text_search_gin'"
        )
    ).fetchall()
    assert len(rows) == 1


def test_lexical_match_surfaces_a_chunk_that_vector_search_alone_would_miss(pg_session):
    org_id = "org-hybrid"
    document = _make_ready_document(pg_session, org_id, "handbook.pptx")

    # The question's embedding is closest to chunk A (vector match), but chunk B contains the
    # exact keyword from the question and chunk A does not — a pure vector-only search would
    # never surface chunk B. Hybrid (RRF) search should include it.
    query_embedding = _unit_vector(1536, hot_index=0)

    chunk_a = DocumentChunk(
        organization_id=org_id,
        document_id=document.id,
        chunk_index=0,
        source="Slide 1",
        text="General company information with no distinctive keyword overlap here.",
        chunk_metadata={},
        embedding=_unit_vector(1536, hot_index=0),  # closest vector match
    )
    chunk_b = DocumentChunk(
        organization_id=org_id,
        document_id=document.id,
        chunk_index=1,
        source="Slide 2",
        text="The quarterly zephyrion revenue figure was exceptionally strong.",
        chunk_metadata={},
        embedding=_unit_vector(1536, hot_index=1000),  # far from the query vector
    )
    pg_session.add_all([chunk_a, chunk_b])
    pg_session.commit()

    results = retrieve_chunks(
        pg_session,
        org_id,
        query_embedding,
        "What was the zephyrion revenue figure?",
        [],
        limit=8,
    )

    sources = {chunk.source for chunk in results}
    assert "Slide 2" in sources  # surfaced via the lexical side of the fusion


def test_hybrid_retrieval_respects_tenant_and_document_filters(pg_session):
    org_a = "org-a"
    org_b = "org-b"
    doc_a = _make_ready_document(pg_session, org_a, "a.xlsx")
    doc_b = _make_ready_document(pg_session, org_b, "b.xlsx")

    pg_session.add_all(
        [
            DocumentChunk(
                organization_id=org_a,
                document_id=doc_a.id,
                chunk_index=0,
                source="Sheet: A",
                text="alpha revenue figures",
                chunk_metadata={},
                embedding=_unit_vector(1536, hot_index=0),
            ),
            DocumentChunk(
                organization_id=org_b,
                document_id=doc_b.id,
                chunk_index=0,
                source="Sheet: B",
                text="alpha revenue figures",
                chunk_metadata={},
                embedding=_unit_vector(1536, hot_index=0),
            ),
        ]
    )
    pg_session.commit()

    results = retrieve_chunks(
        pg_session, org_a, _unit_vector(1536, hot_index=0), "alpha revenue", [], limit=8
    )

    assert len(results) == 1
    assert results[0].document_id == doc_a.id


def test_retrieve_chunks_ignores_non_ready_documents(pg_session):
    org_id = "org-status"
    document = Document(
        organization_id=org_id,
        uploaded_by="user-a",
        filename="processing.xlsx",
        content_type="application/test",
        storage_key=f"{org_id}/processing.xlsx",
        size_bytes=10,
        status=DocumentStatus.processing,
    )
    pg_session.add(document)
    pg_session.flush()
    pg_session.add(
        DocumentChunk(
            organization_id=org_id,
            document_id=document.id,
            chunk_index=0,
            source="Sheet: X",
            text="not yet ready",
            chunk_metadata={},
            embedding=_unit_vector(1536, hot_index=0),
        )
    )
    pg_session.commit()

    results = retrieve_chunks(
        pg_session, org_id, _unit_vector(1536, hot_index=0), "not yet ready", [], limit=8
    )

    assert results == []
