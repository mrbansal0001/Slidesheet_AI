from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_var
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.services.tenancy import seed_test_account

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    # Only ever auto-create on the SQLite dev/test fallback. On PostgreSQL, Alembic (alembic/)
    # is the single source of schema truth — letting create_all() run there too would let the
    # dev-convenience DDL path silently drift out of sync with the tracked migrations, and (once
    # the app connects via the least-privilege app_db_role from migration 0005) would just fail,
    # since that role has no DDL grants.
    if settings.auto_create_tables and engine.dialect.name == "sqlite":
        Base.metadata.create_all(bind=engine)
    if settings.seed_test_email and settings.seed_test_password:
        db = SessionLocal()
        try:
            seed_test_account(db, settings.seed_test_email, settings.seed_test_password, settings.seed_test_org)
        finally:
            db.close()
    yield


configure_logging()
settings = get_settings()
frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.include_router(api_router, prefix="/api/v1")
app.mount("/assets", StaticFiles(directory=frontend_dir), name="frontend-assets")


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    token = request_id_var.set(request_id)
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
    finally:
        request_id_var.reset(token)


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    return FileResponse(frontend_dir / "index.html")


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    return FileResponse(frontend_dir / "login.html")
