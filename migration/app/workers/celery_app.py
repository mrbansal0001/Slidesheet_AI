from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery("document_ingestion", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(task_track_started=True, task_acks_late=True)

