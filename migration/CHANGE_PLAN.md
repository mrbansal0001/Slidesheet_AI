# SlideSheet AI — Change Plan Tracker

Source: `SlideSheet_AI_Architecture_and_Changes.md` (pasted into conversation, not committed as a
separate file here — this tracker mirrors its roadmap so progress persists across sessions).

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[skip]` intentionally deferred (reason noted)

## Post-hoc simplification pass (2026-08-23)

After P4-1/P4-2/P4-3/P4-5 landed, stepped back to cut avoidable complexity rather than keep
adding features:

- Removed `APP_DATABASE_URL` as a required setting. `app/db/session.py` now derives the app
  role's connection automatically from `DATABASE_URL` + `APP_DB_ROLE`/`APP_DB_PASSWORD` (same
  host/port/db, swapped credentials, preserving a pooler-routing username suffix if present) via
  a new `derive_role_url()` helper. This directly removes the failure mode that caused two of
  this session's bugs (a hand-typed second connection string drifting out of sync with the
  first — wrong pooler suffix, stale password). `conftest.py`'s now-unnecessary
  `APP_DATABASE_URL` override was removed too — `session.py` checks the *effective* `DATABASE_URL`
  dialect first, so SQLite tests are safe by construction, not by remembering to clear an extra
  var. `tests/test_rls_policies.py` now imports the same helper instead of duplicating it.
- **Found and fixed a real bug while verifying the above**: pointed `TEST_POSTGRES_URL` at the
  same Supabase project as `DATABASE_URL` to confirm the derivation still worked — which wiped
  the real dev data via `test_rls_policies.py`'s schema-reset fixture (a mistake; that test
  suite must only ever target a disposable project). Restoring it surfaced that migrations 0007
  and 0008 aren't safe to run against a brand-new database: migration 0001 builds its schema
  from the *live* `Base.metadata`, so a fresh DB already has `family_id`/`ip_login_attempts` by
  the time 0007/0008 run, which then fail with "already exists" — 0002 already guards against
  this same situation (see its docstring) but 0007/0008 didn't. Fixed by adding the same
  inspect-before-altering guard. Re-verified end-to-end afterward (login, upload, embeddings,
  chat) against the restored Supabase project.

## Verification note (done before starting)

The source doc lists 3 "correctness bugs to fix first." Verified against actual code:

- **2.1 Enqueue-before-commit race [CRITICAL]** — **NOT REAL.** `app/api/routes/documents.py`
  already calls `db.commit()` (line 56) *before* `ingest_document_task.delay()` (line 61). No fix
  needed; not tracked below.
- **2.2 Unbounded single embed call [HIGH]** — real, confirmed. Tracked as P1-1.
- **2.3 Readiness ignores Redis [MEDIUM]** — real, confirmed. Tracked as P1-2.

---

## Phase 1 — Correctness fixes (this week)

- [x] P1-1: Sub-batch embeddings (`app/services/openai_service.py`) — cap each OpenAI embeddings
      call at 256 inputs, run multiple batches concurrently via a thread pool. Done — see PROJECT.md Changelog.
- [x] P1-2: Add Redis check to `/health/ready` when `INGESTION_MODE=celery` (`app/api/routes/health.py`).
      Done — see PROJECT.md Changelog.
- [x] P1-3: Tests for both (`tests/test_openai_service.py`, extended `tests/test_health.py`).
      3 new tests in test_openai_service.py + 3 new tests in test_health.py. Full suite: 13 passed.

## Phase 2 — Measure first

- [x] P2-1: Structured JSON logs keyed on existing `X-Request-ID`; capture latency per request.
      Done — see PROJECT.md Changelog.
- [x] P2-2: Per-request/per-org token & cost capture on OpenAI calls. Done — see PROJECT.md
      Changelog. Cost is `null` until real pricing is supplied via config (no hardcoded price).
- [skip] P2-3: Langfuse (self-hosted) integration — **user confirmed: defer, will add later.**
- [skip] P2-4: Tiny eval set — needs user-provided sample questions/answers/docs to be meaningful;
      a fabricated eval set would just be theater.

## Phase 3 — Quality

- [x] P3-1: `tsvector` full-text column + GIN index on `document_chunks` (migration 0004, Postgres-only).
      Done — see PROJECT.md Changelog.
- [x] P3-2: Reciprocal Rank Fusion of lexical + vector results. Done — verified against a real
      Postgres+pgvector instance, not just written untested. See PROJECT.md Changelog for the
      RRF-vs-cosine score-scale caveat re: `retrieval_min_score`.
- [x] P3-3: Re-ranker — cheap LLM pass (gpt-4o-mini), not a cross-encoder, to avoid a new ML
      dependency. Done — see PROJECT.md Changelog.
- [ ] P3-3b (deferred, user decision 2026-07-11): Swap the LLM-based re-ranker for a local
      cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2` via `sentence-transformers`) to
      eliminate the per-request OpenAI cost from reranking. Trade-off discussed: brings in
      `torch` (the exact heavy ML dependency originally avoided), no training needed (pretrained
      model, load-once-reuse), expected net latency win per-request (no network round-trip) but
      shifts compute cost onto the API/worker's own CPU — worth watching under concurrent load.
      **Explicitly deferred by user — do later, not now.**
- [x] P3-4: Similarity-threshold rejection ("insufficient evidence" path). Implemented in
      `chat.py` (checks `chunks[0].score`) rather than `retrieval.py`, since `retrieval.py`
      doesn't know about the summary-vs-similarity distinction needed to gate this correctly.
      Default `retrieval_min_score=0.0` (disabled) — see PROJECT.md Changelog for why.
- [x] P3-5: Token-based chunking. Section-boundary respect was already true in this codebase
      (verified, not assumed); switched sizing from chars to tokens via tiktoken. Done — see
      PROJECT.md Changelog.
- [x] P3-6: Embedding model/version provenance columns (`embedding_model`, `embedding_dims` on
      `document_chunks`). Done — see PROJECT.md Changelog.

## Phase 4 — Hardening

- [x] P4-1: Postgres Row-Level Security policies (defense in depth beyond app-layer filters).
      Migrations 0005 (least-privilege `chatbot_app` role — RLS has no effect on the owner/
      superuser connection Alembic itself uses) + 0006 (FORCE RLS + tenant_isolation policy on
      documents/document_chunks/conversations/messages/ingestion_jobs, keyed on the
      `app.current_org_id` GUC). App/worker now set that GUC per-request/per-task via a
      SQLAlchemy `after_begin` listener (`app/db/session.py`) fed by a contextvar set in
      `get_principal` (API) and `workers/tasks.py` (Celery) — mirrors the existing
      `request_id_var` pattern in `app/core/logging.py`. Runtime now connects via
      `APP_DATABASE_URL`/`app_db_role` instead of the owner `DATABASE_URL` when configured;
      `docker-compose.yml` updated to wire that role for both `api` and `worker`. Tested against
      real Postgres in `tests/test_rls_policies.py` (same TEST_POSTGRES_URL gating as
      test_postgres_retrieval.py) by connecting as the app role and confirming cross-tenant reads/
      writes are blocked even with no `organization_id` filter in the query at all.
      Also verified live end-to-end against a real Supabase Postgres project (login → upload →
      embed → chat), which surfaced two real bugs the SQLite/local-Postgres test path couldn't:
      (1) `get_principal` had to become `async def` — FastAPI runs plain `def` dependencies in a
      worker thread, and a contextvar `.set()` made there is invisible outside that thread, so
      `app.current_org_id` was silently never applied and every insert got rejected by RLS;
      (2) managed providers (confirmed on Supabase) install pgvector into an `extensions` schema
      that only the owner role's search_path includes by default — migration 0005 now grants
      `chatbot_app` USAGE there and sets its search_path to match, guarded so it's a no-op on
      plain Postgres/Docker where no such schema exists.
      `users`/`organizations`/`refresh_tokens`/`memberships` intentionally left out of RLS scope —
      see migration 0006's docstring for why.
- [x] P4-2: Refresh-token family/lineage — revoke entire family on reuse detection. Migration
      0007 adds `refresh_tokens.family_id` (existing rows backfilled to their own id). On
      `/auth/refresh`, presenting an already-revoked token now revokes every non-revoked token
      sharing its `family_id`, not just itself — so a stolen-then-replayed token kills the
      legitimate client's session too, forcing re-login, rather than only blocking the thief's
      single replay attempt. `logout` still revokes only the one token it's given (leaving other
      devices' sessions alone), unlike reuse detection's family-wide revoke — see `auth.py`.
      Tested in `tests/test_auth.py` (register → refresh → replay-the-old-token → confirms the
      newest token is also dead) and verified live against real Supabase.
- [x] P4-3: IP-based throttling for login (in addition to existing per-email lockout). Migration
      0008 adds `ip_login_attempts` (source IP, failed count, lockout expiry — mirrors the
      existing per-`User` lockout fields). Catches email enumeration (spraying many different
      addresses from one source) that per-email lockout structurally can't see, since that only
      engages once an attacker has settled on one email. Threshold is looser than the per-email
      one (20 vs 5 failures) since one IP can legitimately be many users behind NAT.
      `request.client.host` is used directly — if this ever sits behind a reverse proxy, that
      needs to become a trusted `X-Forwarded-For` read instead, or every client behind the proxy
      shares one bucket. Tested in `tests/test_auth.py` (enumeration across distinct never-seen
      emails still trips the lockout; a real success resets the counter) and verified live
      against Supabase (20 failed logins across 20 distinct emails, 21st rejected with 429).
- [x] P4-4: Pagination on `GET /documents`, `GET /conversations`. Done — see PROJECT.md
      Changelog. "Messages" pagination out of scope: no message-listing endpoint exists at all
      currently (verified, not assumed) — that's a separate pre-existing gap, not this task.
- [x] P4-5: Celery retry/backoff (`autoretry_for`). `ingest_document_task` (`app/workers/tasks.py`)
      now retries on transient OpenAI errors only (`APIConnectionError`, `APITimeoutError`,
      `RateLimitError`, `InternalServerError` — deliberately not `ValueError`/parse errors, which
      won't succeed differently on retry) with exponential backoff + jitter, up to 5 attempts.
      The OpenAI client itself already retries transient errors twice before raising (see
      `openai_service.py`), so this catches longer outages the SDK-level retry doesn't cover.
      Known tradeoff, documented in `ingestion.py`: `Document`/`IngestionJob.status` can flicker
      to "failed" and back to "ready" across retries rather than showing an in-between
      "retrying" state — accepted to avoid threading a new status value through the model/API/
      frontend for a cosmetic improvement; it's still eventually consistent.
      "Dead-letter path" from the original item: satisfied by the existing persisted failure
      record (`Document`/`IngestionJob.error_message`, already surfaced via `GET /documents`)
      once retries are exhausted — no separate DLQ queue/table built, since nothing in this
      codebase consumes one. Tested in `tests/test_ingestion_retry.py`, including an eager-mode
      Celery run that proves a retryable error is actually retried (not just configured) and a
      non-retryable one fails immediately.
- [ ] P4-6: SSE streaming for chat responses (`StreamingResponse` + `EventSource` in frontend).
- [ ] P4-7: Decouple LLM provider behind a swappable interface (mirror `StorageService` pattern).

## Phase 5 — Scale-driven (deferred — infra/measurement gated)

- [skip] P5-1: S3/MinIO storage — only justified once running more than one host.
- [skip] P5-2: Real IdP + JWKS — external identity-provider decision, needs user input.
- [skip] P5-3: HNSW partition/partial index tuning — needs production query measurements first.
- [skip] P5-4: arq migration evaluation — only if Celery operationally "starts to chafe"; not yet observed.

---

## Working notes

Updated as each item lands — see conversation for rationale/trade-offs behind each change.
