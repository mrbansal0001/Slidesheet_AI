from unittest.mock import patch

import openai

from app.workers.celery_app import celery_app
from app.workers.tasks import _RETRYABLE_ERRORS, ingest_document_task


def test_retryable_errors_are_transient_openai_failures_only():
    assert _RETRYABLE_ERRORS == (
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.RateLimitError,
        openai.InternalServerError,
    )
    # A corrupt/unparseable file won't parse any differently on retry, so ValueError (raised by
    # ingest_document for "no readable text") must NOT be in the retryable set.
    assert not issubclass(ValueError, _RETRYABLE_ERRORS)


def test_task_is_configured_to_retry_with_backoff():
    assert ingest_document_task.autoretry_for == _RETRYABLE_ERRORS
    assert ingest_document_task.retry_backoff is True
    assert ingest_document_task.retry_jitter is True
    assert ingest_document_task.max_retries == 5


def test_retryable_error_is_retried_until_it_succeeds():
    """Runs the actual task (in Celery's synchronous/eager mode, no broker needed) to prove
    autoretry_for really re-invokes ingest_document rather than just being configured and unused.
    """
    attempts = {"count": 0}

    def _fails_once_then_succeeds(db, document_id, organization_id):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise openai.APIConnectionError(request=None)
        return None

    celery_app.conf.task_always_eager = True
    try:
        with patch("app.workers.tasks.ingest_document", side_effect=_fails_once_then_succeeds):
            with patch("app.workers.tasks.SessionLocal"):
                result = ingest_document_task.apply(args=("doc-1", "org-1"))
    finally:
        celery_app.conf.task_always_eager = False
        celery_app.conf.task_eager_propagates = False

    assert result.successful()
    assert attempts["count"] == 2


def test_non_retryable_error_fails_immediately_without_retrying():
    attempts = {"count": 0}

    def _always_raises_value_error(db, document_id, organization_id):
        attempts["count"] += 1
        raise ValueError("No readable text was found in this document")

    celery_app.conf.task_always_eager = True
    try:
        with patch("app.workers.tasks.ingest_document", side_effect=_always_raises_value_error):
            with patch("app.workers.tasks.SessionLocal"):
                result = ingest_document_task.apply(args=("doc-1", "org-1"))
    finally:
        celery_app.conf.task_always_eager = False
        celery_app.conf.task_eager_propagates = False

    assert result.failed()
    assert attempts["count"] == 1
