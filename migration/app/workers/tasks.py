import openai

from app.db.session import SessionLocal, current_org_id_var
from app.services.ingestion import ingest_document
from app.workers.celery_app import celery_app

# The OpenAI SDK client itself already retries transient errors a couple of times (see
# openai_service.py's `max_retries=2`) before raising — so anything reaching us here already
# survived that and is either a longer-lived outage/rate limit, or one of the non-retryable
# error types below, which aren't in autoretry_for and so just fail the task immediately as
# ingest_document intends (e.g. a corrupt file won't parse any differently on retry).
_RETRYABLE_ERRORS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)


@celery_app.task(
    name="ingest_document",
    autoretry_for=_RETRYABLE_ERRORS,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def ingest_document_task(document_id: str, organization_id: str) -> None:
    # Celery tasks run outside any request context, so the tenant context that RLS relies on
    # (see app/db/session.py's after_begin listener) has to be set here explicitly.
    token = current_org_id_var.set(organization_id)
    try:
        with SessionLocal() as db:
            ingest_document(db, document_id, organization_id)
    finally:
        current_org_id_var.reset(token)

