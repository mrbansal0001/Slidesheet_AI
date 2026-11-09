from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import Principal, get_principal
from app.db.models import Document, IngestionJob
from app.db.session import get_db
from app.schemas.documents import DocumentResponse, UploadResponse
from app.services.ingestion import ingest_document
from app.services.storage import StorageService
from app.services.tenancy import ensure_principal_records

router = APIRouter(prefix="/documents", tags=["documents"])
ALLOWED_EXTENSIONS = {".pptx", ".xlsx"}


@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> UploadResponse:
    settings = get_settings()
    ensure_principal_records(db, principal)
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Only .pptx and .xlsx files are supported")

    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="File exceeds the configured upload limit")
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    document = Document(
        organization_id=principal.organization_id,
        uploaded_by=principal.user_id,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        storage_key="pending",
        size_bytes=len(content),
    )
    db.add(document)
    db.flush()
    document.storage_key = f"{principal.organization_id}/{document.id}/{filename}"
    job = IngestionJob(
        organization_id=principal.organization_id,
        document_id=document.id,
    )
    db.add(job)
    db.commit()
    StorageService().put(document.storage_key, content)

    if settings.ingestion_mode == "celery":
        from app.workers.tasks import ingest_document_task

        ingest_document_task.delay(document.id, principal.organization_id)
    else:
        try:
            ingest_document(db, document.id, principal.organization_id)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Ingestion failed: {exc}") from exc

    db.refresh(document)
    return UploadResponse(document=document, ingestion_job_id=job.id)


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> list[Document]:
    ensure_principal_records(db, principal)
    base_query = db.query(Document).filter(Document.organization_id == principal.organization_id)
    response.headers["X-Total-Count"] = str(base_query.count())
    return (
        base_query.order_by(Document.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> Document:
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.organization_id == principal.organization_id,
        )
        .one_or_none()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
def delete_document(
    document_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> Response:
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.organization_id == principal.organization_id,
        )
        .one_or_none()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    storage_key = document.storage_key
    db.delete(document)
    db.commit()
    StorageService().delete(storage_key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
