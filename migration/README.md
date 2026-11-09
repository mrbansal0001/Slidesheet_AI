# Production migration

This folder is an isolated production-oriented replacement for the prototype in
the repository root. The original app is intentionally unchanged.

For a complete explanation of the architecture, design patterns, component
choices, alternatives, data model, flows and scaling path, read
[`PROJECT.md`](PROJECT.md).

## What changes

- Persistent users, organizations, documents, chunks, conversations, and jobs
- Tenant filtering on every document and retrieval query
- PostgreSQL + pgvector in deployment; SQLite fallback for local development
- Original file storage behind a storage service
- Optional Redis/Celery background ingestion
- JWT authentication, with explicit development headers for local testing
- Source citations containing filename plus slide/sheet
- Separate document-status APIs from document-content questions
- Health checks, structured configuration, migrations, and tests

## Local quick start

```bash
cd migration
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8001
```

Open `http://localhost:8001` for the web UI or `http://localhost:8001/docs`
for the API documentation.

In local development, the UI and direct API requests can use these headers:

```text
X-User-ID: demo-user
X-Org-ID: demo-org
```

Set `ALLOW_DEV_AUTH=false` in production. Production callers must send a JWT
whose claims include `sub` and `org_id`.

## Docker stack

```bash
docker compose up --build
```

The API runs on `http://localhost:8001`. PostgreSQL, pgvector, Redis, the API,
and an ingestion worker are included.

## Important production follow-ups

- Replace local file storage with S3/MinIO by implementing `StorageService`.
- Configure asymmetric JWT verification or your identity provider's JWKS.
- Add malware scanning before ingestion.
- Add OCR/vision for image-only slides.
- Put TLS and a load balancer/reverse proxy in front of the API.
- Run the evaluation set before changing prompts, chunking, or models.
