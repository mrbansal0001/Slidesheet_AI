import redis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health/live")
def liveness() -> dict:
    return {"status": "ok"}


def _check_redis(redis_url: str) -> None:
    redis.from_url(redis_url, socket_connect_timeout=2).ping()


@router.get("/health/ready")
def readiness(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))

    settings = get_settings()
    if settings.ingestion_mode == "celery":
        try:
            _check_redis(settings.redis_url)
        except redis.RedisError as exc:
            raise HTTPException(status_code=503, detail=f"Redis is not reachable: {exc}") from exc

    return {"status": "ready"}

