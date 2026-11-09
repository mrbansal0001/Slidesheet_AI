from __future__ import annotations

import re

from pydantic import BaseModel, EmailStr, field_validator

_PASSWORD_MIN_LENGTH = 10


def _validate_password_strength(password: str) -> str:
    if len(password) < _PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {_PASSWORD_MIN_LENGTH} characters long")
    if not re.search(r"[A-Za-z]", password):
        raise ValueError("Password must contain at least one letter")
    if not re.search(r"\d", password):
        raise ValueError("Password must contain at least one number")
    return password


class RegisterRequest(BaseModel):
    organization_name: str
    email: EmailStr
    password: str

    @field_validator("organization_name")
    @classmethod
    def organization_name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Organization name is required")
        return value

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        return _validate_password_strength(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: str
    email: str | None
    organization_id: str
    organization_name: str
    role: str
