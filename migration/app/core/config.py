from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Document Intelligence API"
    database_url: str = "sqlite:///./migration.db"
    # Advanced-only override for the runtime API/worker connection — leave blank. By default
    # app/db/session.py derives this automatically from database_url + app_db_role/app_db_password
    # (same host/port/db, different credentials); only set this if the app role genuinely needs a
    # different host than the owner connection (e.g. a read replica). See session.py for why a
    # separate role is used at all.
    app_database_url: str = ""
    # Role/password migration 0005 creates on PostgreSQL for the runtime connection above.
    # Irrelevant on SQLite (no roles/RLS concept there).
    app_db_role: str = "chatbot_app"
    app_db_password: str = "change-me-app-db-password"
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    # Coarser than the per-email lockout above — catches email enumeration (spraying many
    # different addresses from one source) that per-email lockout can't see, since that only
    # kicks in once an attacker has settled on one email. Higher threshold than max_failed_logins
    # since one IP can legitimately be many users (NAT/office network).
    max_failed_logins_per_ip: int = 20
    ip_lockout_minutes: int = 15
    allow_dev_auth: bool = True
    seed_test_email: str = ""
    seed_test_password: str = ""
    seed_test_org: str = "Test Org"
    auto_create_tables: bool = True
    ingestion_mode: str = "sync"
    redis_url: str = "redis://localhost:6379/0"
    storage_path: Path = Path("./storage")
    max_upload_bytes: int = 25 * 1024 * 1024
    retrieval_candidates: int = 20
    retrieval_top_k: int = 8
    # Minimum best-match similarity score (cosine, 0..1) required to answer from vector search
    # instead of returning an "insufficient evidence" response. Defaults to 0 (disabled) —
    # the right threshold is corpus/embedding-model dependent and should be set from measured
    # results against an evaluation set, not guessed. Does not apply to summary-style questions
    # (those intentionally use document order, not similarity ranking).
    retrieval_min_score: float = 0.0
    # Reciprocal Rank Fusion constant for combining vector + lexical candidate rankings
    # (PostgreSQL hybrid retrieval only). 60 is the standard default from the RRF literature
    # (Cormack et al. 2009), validated across many collections — unlike the settings above,
    # this isn't a corpus-specific guess.
    retrieval_rrf_k: int = 60

    # Optional cost estimation for structured logs. Left at 0 (disabled) by default because
    # provider pricing changes over time — set these to your current rate card to enable
    # per-call cost figures in logs; otherwise only token counts and latency are logged.
    openai_embedding_price_per_1k_tokens: float = 0.0
    openai_chat_input_price_per_1k_tokens: float = 0.0
    openai_chat_output_price_per_1k_tokens: float = 0.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

