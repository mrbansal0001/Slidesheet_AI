from __future__ import annotations

import logging

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    Document,
    DocumentChunk,
    DocumentStatus,
    IngestionJob,
    JobStatus,
)
from app.services.openai_service import OpenAIService
from app.services.parsing import chunk_sections, parse_document
from app.services.storage import StorageService

logger = logging.getLogger(__name__)


def ingest_document(db: Session, document_id: str, organization_id: str) -> None:
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.organization_id == organization_id,
        )
        .one()
    )
    job = (
        db.query(IngestionJob)
        .filter(
            IngestionJob.document_id == document_id,
            IngestionJob.organization_id == organization_id,
        )
        .one()
    )

    document.status = DocumentStatus.processing
    job.status = JobStatus.running
    db.commit()

    try:
        content = StorageService().get(document.storage_key)
        chunks = chunk_sections(parse_document(document.filename, content))
        if not chunks:
            raise ValueError("No readable text was found in this document")

        embeddings = OpenAIService().embed(
            [chunk.text for chunk in chunks], organization_id=organization_id
        )
        db.execute(
            delete(DocumentChunk).where(
                DocumentChunk.document_id == document.id,
                DocumentChunk.organization_id == organization_id,
            )
        )
        embedding_model = get_settings().openai_embedding_model
        for index, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            db.add(
                DocumentChunk(
                    organization_id=organization_id,
                    document_id=document.id,
                    chunk_index=index,
                    source=chunk.source,
                    text=chunk.text,
                    chunk_metadata=chunk.metadata,
                    embedding=embedding,
                    embedding_model=embedding_model,
                    embedding_dims=len(embedding),
                )
            )

        document.status = DocumentStatus.ready
        document.error_message = None
        job.status = JobStatus.completed
        job.error_message = None
        db.commit()
    except Exception as exc:
        # Note for the Celery path (app/workers/tasks.py): on a retryable OpenAI error, this
        # still marks failed before the task-level autoretry re-runs ingest_document from
        # scratch — so status can flicker to "failed" and back to "ready" across retries rather
        # than showing an in-between "retrying" state. Kept simple deliberately: the alternative
        # is a new status value threaded through Document/IngestionJob/the API/the frontend for a
        # purely cosmetic improvement, and the status is still eventually consistent — it settles
        # on "failed" only once retries are actually exhausted.
        logger.exception("Document ingestion failed", extra={"document_id": document_id})
        db.rollback()
        document = db.get(Document, document_id)
        job = db.query(IngestionJob).filter(IngestionJob.document_id == document_id).one()
        document.status = DocumentStatus.failed
        document.error_message = str(exc)[:1_000]
        job.status = JobStatus.failed
        job.error_message = str(exc)[:1_000]
        db.commit()
        raise
