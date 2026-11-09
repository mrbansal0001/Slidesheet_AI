from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db.models import DocumentStatus


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    content_type: str
    size_bytes: int
    status: DocumentStatus
    error_message: str | None
    created_at: datetime


class UploadResponse(BaseModel):
    document: DocumentResponse
    ingestion_job_id: str

