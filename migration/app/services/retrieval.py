from __future__ import annotations

import json
import math
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Document, DocumentChunk, DocumentStatus


@dataclass
class RetrievedChunk:
    document_id: str
    filename: str
    source: str
    text: str
    score: float


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0


def retrieve_chunks(
    db: Session,
    organization_id: str,
    query_embedding: list[float],
    question: str,
    document_ids: list[str],
    limit: int,
) -> list[RetrievedChunk]:
    """Retrieve the most relevant chunks for `question`.

    On PostgreSQL this fuses two independent rankings — vector similarity and lexical full-text
    match — via Reciprocal Rank Fusion (RRF), so a question containing an exact term (a product
    code, a name) that vector search alone might under-rank still surfaces via the lexical side,
    while a paraphrased question with no keyword overlap still works via the vector side.

    On SQLite (dev/test fallback, no full-text search available) this stays vector-only cosine
    similarity, same as before hybrid search was added.
    """
    settings = get_settings()
    query = (
        db.query(DocumentChunk, Document.filename)
        .join(Document, Document.id == DocumentChunk.document_id)
        .filter(
            DocumentChunk.organization_id == organization_id,
            Document.organization_id == organization_id,
            Document.status == DocumentStatus.ready,
        )
    )
    if document_ids:
        query = query.filter(DocumentChunk.document_id.in_(document_ids))

    if db.bind and db.bind.dialect.name == "postgresql":
        conditions = [
            "c.organization_id = :organization_id",
            "d.organization_id = :organization_id",
            "d.status = 'ready'",
        ]
        params: dict = {
            "organization_id": organization_id,
            "embedding": json.dumps(query_embedding),
            "question": question,
            "candidate_limit": settings.retrieval_candidates,
            "rrf_k": settings.retrieval_rrf_k,
            "limit": limit,
        }
        if document_ids:
            conditions.append("c.document_id = ANY(:document_ids)")
            params["document_ids"] = document_ids
        where_clause = " AND ".join(conditions)

        rows = db.execute(
            text(
                f"""
                WITH vector_candidates AS (
                    SELECT c.id, c.document_id, d.filename, c.source, c.text,
                           ROW_NUMBER() OVER (
                               ORDER BY c.embedding <=> CAST(:embedding AS vector)
                           ) AS rnk
                    FROM document_chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE {where_clause}
                    ORDER BY c.embedding <=> CAST(:embedding AS vector)
                    LIMIT :candidate_limit
                ),
                lexical_candidates AS (
                    SELECT c.id, c.document_id, d.filename, c.source, c.text,
                           ROW_NUMBER() OVER (
                               ORDER BY ts_rank(
                                   c.text_search, plainto_tsquery('english', :question)
                               ) DESC
                           ) AS rnk
                    FROM document_chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE {where_clause}
                      AND c.text_search @@ plainto_tsquery('english', :question)
                    ORDER BY ts_rank(
                        c.text_search, plainto_tsquery('english', :question)
                    ) DESC
                    LIMIT :candidate_limit
                )
                SELECT document_id, filename, source, text,
                       SUM(1.0 / (:rrf_k + rnk)) AS score
                FROM (
                    SELECT * FROM vector_candidates
                    UNION ALL
                    SELECT * FROM lexical_candidates
                ) combined
                GROUP BY id, document_id, filename, source, text
                ORDER BY score DESC
                LIMIT :limit
                """
            ),
            params,
        ).mappings()
        return [RetrievedChunk(**dict(row)) for row in rows]

    # SQLite development fallback: vector-only cosine similarity, no full-text search available.
    candidates = query.all()
    ranked = [
        RetrievedChunk(
            document_id=chunk.document_id,
            filename=filename,
            source=chunk.source,
            text=chunk.text,
            score=_cosine_similarity(query_embedding, chunk.embedding),
        )
        for chunk, filename in candidates
    ]
    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked[:limit]


def get_summary_chunks(
    db: Session,
    organization_id: str,
    document_ids: list[str],
    max_chunks: int = 80,
) -> list[RetrievedChunk]:
    query = (
        db.query(DocumentChunk, Document.filename)
        .join(Document, Document.id == DocumentChunk.document_id)
        .filter(
            DocumentChunk.organization_id == organization_id,
            Document.organization_id == organization_id,
            Document.status == DocumentStatus.ready,
        )
        .order_by(Document.filename, DocumentChunk.chunk_index)
    )
    if document_ids:
        query = query.filter(DocumentChunk.document_id.in_(document_ids))
    return [
        RetrievedChunk(
            document_id=chunk.document_id,
            filename=filename,
            source=chunk.source,
            text=chunk.text,
            score=1.0,
        )
        for chunk, filename in query.limit(max_chunks).all()
    ]

