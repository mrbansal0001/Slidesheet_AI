from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    Principal,
    create_access_token,
    generate_refresh_token,
    get_principal,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.db.models import IpLoginAttempt, Membership, Organization, RefreshToken, User, new_id
from app.db.session import get_db
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_GENERIC_LOGIN_ERROR = "Invalid email or password"


def _aware(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on read even for timezone-aware columns; Postgres keeps it."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _issue_tokens(
    db: Session, user: User, organization_id: str, family_id: str | None = None
) -> TokenResponse:
    access_token, expires_in = create_access_token(user.id, organization_id)
    raw_refresh_token, token_hash, refresh_expires_at = generate_refresh_token()
    token_id = new_id()
    db.add(
        RefreshToken(
            id=token_id,
            user_id=user.id,
            token_hash=token_hash,
            # No family_id passed in means this is a fresh login, not a rotation — it starts a
            # new family rooted at its own id.
            family_id=family_id or token_id,
            expires_at=refresh_expires_at,
        )
    )
    db.commit()
    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh_token,
        expires_in=expires_in,
    )


def _membership_for(db: Session, user_id: str) -> Membership:
    membership = db.query(Membership).filter(Membership.user_id == user_id).first()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization membership")
    return membership


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    email = payload.email.lower()
    if db.query(User).filter(User.email == email).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists"
        )

    organization = Organization(id=new_id(), name=payload.organization_name)
    user = User(id=new_id(), email=email, password_hash=hash_password(payload.password))
    db.add(organization)
    db.add(user)
    db.flush()
    db.add(Membership(organization_id=organization.id, user_id=user.id, role="owner"))
    db.commit()

    return _issue_tokens(db, user, organization.id)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    # Coarse, IP-scoped check first — catches spraying many different emails from one source,
    # which per-email lockout below can't see (it only engages once an attacker settles on one).
    client_ip = request.client.host if request.client else "unknown"
    ip_attempt = db.get(IpLoginAttempt, client_ip)
    if ip_attempt is None:
        ip_attempt = IpLoginAttempt(ip_address=client_ip)
        db.add(ip_attempt)

    ip_locked_until = _aware(ip_attempt.locked_until)
    if ip_locked_until is not None and ip_locked_until > now:
        retry_minutes = max(1, int((ip_locked_until - now).total_seconds() // 60) + 1)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts from this network. Try again in {retry_minutes} minute(s).",
        )

    def _record_ip_failure() -> None:
        ip_attempt.failed_count += 1
        if ip_attempt.failed_count >= settings.max_failed_logins_per_ip:
            ip_attempt.locked_until = now + timedelta(minutes=settings.ip_lockout_minutes)
            ip_attempt.failed_count = 0

    email = payload.email.lower()
    user = db.query(User).filter(User.email == email).first()

    if user is None or user.password_hash is None:
        _record_ip_failure()
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_LOGIN_ERROR)

    locked_until = _aware(user.locked_until)
    if locked_until is not None and locked_until > now:
        retry_minutes = max(1, int((locked_until - now).total_seconds() // 60) + 1)
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Account temporarily locked. Try again in {retry_minutes} minute(s).",
        )

    if not verify_password(payload.password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= settings.max_failed_logins:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            user.failed_login_count = 0
        _record_ip_failure()
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_LOGIN_ERROR)

    user.failed_login_count = 0
    user.locked_until = None
    ip_attempt.failed_count = 0
    ip_attempt.locked_until = None
    db.commit()

    membership = _membership_for(db, user.id)
    return _issue_tokens(db, user, membership.organization_id)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenResponse:
    token_hash = hash_refresh_token(payload.refresh_token)
    stored = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()

    now = datetime.now(timezone.utc)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        )

    if stored.revoked_at is not None:
        # This exact token was already rotated away (or explicitly logged out) once before.
        # Seeing it again means either a client retried stale state, or someone replayed a
        # stolen token — we can't tell which, so treat it as theft and kill the whole lineage,
        # not just this one token, forcing a fresh login.
        db.query(RefreshToken).filter(
            RefreshToken.family_id == stored.family_id,
            RefreshToken.revoked_at.is_(None),
        ).update({"revoked_at": now})
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        )

    if _aware(stored.expires_at) <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        )

    user = db.get(User, stored.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    stored.revoked_at = now
    db.commit()

    membership = _membership_for(db, user.id)
    return _issue_tokens(db, user, membership.organization_id, family_id=stored.family_id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def logout(payload: RefreshRequest, db: Session = Depends(get_db)) -> None:
    token_hash = hash_refresh_token(payload.refresh_token)
    stored = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = datetime.now(timezone.utc)
        db.commit()


@router.get("/me", response_model=UserResponse)
def me(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> UserResponse:
    user = db.get(User, principal.user_id)
    organization = db.get(Organization, principal.organization_id)
    membership = db.get(
        Membership, {"organization_id": principal.organization_id, "user_id": principal.user_id}
    )
    return UserResponse(
        id=principal.user_id,
        email=user.email if user else None,
        organization_id=principal.organization_id,
        organization_name=organization.name if organization else principal.organization_id,
        role=membership.role if membership else "member",
    )
