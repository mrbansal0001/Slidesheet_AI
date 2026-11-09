from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Header, HTTPException, status

from app.core.config import get_settings
from app.db.session import current_org_id_var


@dataclass(frozen=True)
class Principal:
    user_id: str
    organization_id: str


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user_id: str, organization_id: str) -> tuple[str, int]:
    settings = get_settings()
    expires_in = settings.access_token_expire_minutes * 60
    payload = {
        "sub": user_id,
        "org_id": organization_id,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def generate_refresh_token() -> tuple[str, str, datetime]:
    """Returns (raw_token_to_send_to_client, hash_to_store, expires_at)."""
    settings = get_settings()
    raw_token = secrets.token_urlsafe(48)
    token_hash = hash_refresh_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    return raw_token, token_hash, expires_at


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def get_principal(
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
) -> Principal:
    # Must stay async (even though nothing here awaits): FastAPI runs plain `def` dependencies
    # in a worker thread, and current_org_id_var.set() below would then mutate a copy of the
    # context local to that thread — invisible to the rest of this request — instead of the
    # context the route body and app/db/session.py's after_begin listener actually see.
    settings = get_settings()

    if settings.allow_dev_auth and x_user_id and x_org_id:
        current_org_id_var.set(x_org_id)
        return Principal(user_id=x_user_id, organization_id=x_org_id)

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    try:
        payload = jwt.decode(
            authorization.removeprefix("Bearer ").strip(),
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "org_id", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc

    organization_id = str(payload["org_id"])
    current_org_id_var.set(organization_id)
    return Principal(user_id=str(payload["sub"]), organization_id=organization_id)

