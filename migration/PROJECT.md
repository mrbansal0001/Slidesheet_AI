# SlideSheet AI — Project and System Design Guide

## 1. Project purpose

SlideSheet AI is a multi-user document-question-answering application for
PowerPoint (`.pptx`) and Excel (`.xlsx`) files.

A user uploads a document, the system extracts and divides its content into
smaller chunks, converts those chunks into vector embeddings, stores them, and
retrieves the most relevant evidence when the user asks a question. An LLM then
produces an answer grounded in that evidence and returns citations containing
the filename and slide or sheet.

This architecture is a production-oriented learning foundation. It demonstrates
the important boundaries of a real service while remaining small enough to run
locally with Docker Compose.

---

## 2. High-level architecture

```text
                         ┌─────────────────────────────┐
                         │       SlideSheet AI UI      │
                         │ HTML + CSS + JavaScript     │
                         └──────────────┬──────────────┘
                                        │ HTTP / JSON
                                        ▼
                         ┌─────────────────────────────┐
                         │        FastAPI API          │
                         │ auth, validation, routing   │
                         └──────┬─────────┬────────────┘
                                │         │
                   enqueue task│         │ read/write metadata,
                                │         │ conversations and vectors
                                ▼         ▼
                    ┌───────────────┐  ┌────────────────────────┐
                    │ Redis broker  │  │ PostgreSQL + pgvector  │
                    └───────┬───────┘  └────────────────────────┘
                            │
                            ▼
                    ┌───────────────┐
                    │ Celery worker │
                    │ parse/chunk/  │
                    │ embed/index   │
                    └───┬───────┬───┘
                        │       │
              read file│       │ embeddings
                        ▼       ▼
                 ┌──────────┐  ┌────────────────────┐
                 │ Storage  │  │     OpenAI API     │
                 │ volume   │  │ embeddings + chat  │
                 └──────────┘  └────────────────────┘
```

Docker Compose currently starts four application services:

| Service | Responsibility |
|---|---|
| `api` | Serves the UI and versioned REST API |
| `worker` | Executes document-ingestion jobs outside the request path |
| `db` | Runs PostgreSQL 16 with the pgvector extension |
| `redis` | Brokers tasks between the API and Celery worker |

Two Docker volumes provide persistence:

| Volume | Stores |
|---|---|
| `postgres_data` | Database tables, messages and embeddings |
| `document_storage` | Original uploaded files |

The OpenAI API is external and does not run inside Docker.

---

## 3. Main system-design techniques

### 3.1 Separation of concerns

The application is separated into layers:

```text
API routes -> schemas -> services -> database/storage/external APIs
```

- `app/api/routes`: HTTP concerns such as request parsing and status codes
- `app/schemas`: request and response contracts
- `app/services`: parsing, ingestion, retrieval, storage and generation logic
- `app/db`: persistence models and database sessions
- `app/core`: configuration, security and logging
- `app/workers`: asynchronous job execution

Why it helps:

- Components are easier to test independently.
- HTTP code is not mixed with document parsing.
- Storage or model providers can be replaced behind a service boundary.
- Failures are easier to locate.

This is a layered architecture, not a set of independent microservices. For the
current project size, a modular monolith plus a worker is simpler and safer than
prematurely splitting every function into its own network service.

### 3.2 Stateless API design

The FastAPI process does not keep a user's vector index or conversation only in
RAM. Durable state is stored in PostgreSQL and file storage.

Why it helps:

- Restarting the API does not delete application state.
- More than one API replica can serve the same users.
- A load balancer can route requests to any healthy replica.

The original prototype's global in-memory chatbot could not provide this
property because each process had a different FAISS index.

### 3.3 Asynchronous work queue

Document ingestion is expensive:

```text
upload -> parse -> chunk -> embed -> persist
```

In production mode, the API creates a job and sends it through Redis to a Celery
worker. The worker performs the expensive processing.

Why it helps:

- Upload requests do not occupy an API worker for a long time.
- Failed jobs can be observed and retried.
- API and worker capacity can be scaled independently.
- A slow embedding provider does not block unrelated API requests.

Local development can set `INGESTION_MODE=sync` to avoid requiring Redis and a
worker.

### 3.4 Multi-tenancy through row-level ownership

Most persistent records contain `organization_id`; conversations also contain
`user_id`.

Every document, chunk and conversation query applies the authenticated tenant
boundary. Before retrieval, requested document IDs are checked again against
the organization.

```text
JWT -> Principal(user_id, organization_id)
    -> tenant-filtered database query
    -> tenant-filtered vector retrieval
```

Why it helps:

- One organization cannot query another organization's documents.
- One user cannot open another user's conversation merely by guessing an ID.
- Tenant isolation is enforced by the backend, not trusted to the browser.

Important future hardening: enable PostgreSQL Row-Level Security (RLS) as a
second database-level isolation layer. The current implementation enforces
tenancy in application queries.

### 3.5 State machines for long-running operations

Documents and ingestion jobs have explicit states:

```text
Document: uploaded -> processing -> ready
                               \-> failed

Job:      queued -> running -> completed
                          \-> failed
```

Why it helps:

- The UI can communicate progress.
- Failed ingestion does not look like an empty successful document.
- Support and monitoring can identify the failed stage.
- Chat only searches documents whose status is `ready`.

### 3.6 Database migrations

SQLAlchemy models describe the application schema. Alembic applies versioned
schema changes to an existing database.

Why it helps:

- Schema updates are repeatable across developer, test and production systems.
- Existing data does not need to be deleted for every model change.
- Deployments know which schema version is installed.

The initial migration also enables PostgreSQL's `vector` extension and creates
an HNSW vector index.

### 3.7 Dependency inversion through service boundaries

`StorageService` hides where uploaded files are stored. Today it uses a local
path. A future S3 or MinIO implementation can preserve the same `put`, `get`
and `delete` behavior.

`OpenAIService` similarly contains embedding and answer-generation calls.

Why it helps:

- Infrastructure choices do not leak through every API route.
- Tests can replace model calls with deterministic mocks.
- Providers can be changed with less application-wide rewriting.

### 3.8 Health checks and dependency ordering

The API provides:

```text
GET /api/v1/health/live
GET /api/v1/health/ready
```

Docker Compose also checks PostgreSQL with `pg_isready` and Redis with `PING`.
The API and worker wait for those services to become healthy.

Why it helps:

- A platform can distinguish a running process from an actually usable one.
- Containers do not start in a random dependency order.
- A load balancer can avoid routing traffic to an unready API.

### 3.9 Request correlation

Middleware adds an `X-Request-ID` to every response, accepting an existing one
from an upstream proxy when present.

Why it helps:

- Logs from a client, proxy and API can be connected to one request.
- Production debugging becomes much easier than searching by timestamp alone.

### 3.10 Configuration through environment variables

Settings such as database URL, models, JWT secret, storage path and ingestion
mode come from environment variables.

Why it helps:

- The same image runs in local, test and deployment environments.
- Secrets are not hardcoded into source files.
- SQLite/synchronous and PostgreSQL/Celery modes use the same codebase.

---

## 4. Component choices and alternatives

### 4.1 FastAPI

Why selected:

- Good type-driven request validation through Pydantic
- Automatic OpenAPI documentation at `/docs`
- Simple dependency injection for database sessions and authentication
- Strong fit for Python document and AI libraries
- Supports asynchronous endpoints where useful

Alternatives:

| Alternative | When it may be better |
|---|---|
| Django + Django REST Framework | Larger application needing a built-in admin, ORM and complete account system |
| Flask | Very small API where minimal framework behavior is preferred |
| Node.js/NestJS | TypeScript-first team or shared frontend/backend language |
| Go | Very high request throughput and small static deployment binaries |

Why FastAPI here: the workload depends more on Python parsing and AI tooling
than raw HTTP throughput, and FastAPI keeps the API contract concise.

### 4.2 PostgreSQL

Why selected:

- Reliable concurrent multi-user database
- Transactions, constraints, indexes and backups
- Stores relational data and vector data together
- Mature tooling such as pgAdmin
- Easy migration from a local SQLite development mode

Alternatives:

| Alternative | Tradeoff |
|---|---|
| SQLite | Excellent for local development, but limited concurrent writes and operational tooling |
| MySQL | Strong relational database, but pgvector makes PostgreSQL a more natural vector choice |
| MongoDB | Flexible documents, but this application's users, memberships and conversations are relational |
| DynamoDB | Highly scalable managed key-value storage, but more complex querying and local development |

Why PostgreSQL here: one database can safely handle organizations, users,
documents, conversations and vector search at the expected learning/demo scale.

### 4.3 pgvector

Why selected:

- Embeddings live beside document metadata.
- Tenant and document filters can be applied in the same query.
- Supports cosine distance and HNSW indexes.
- Avoids operating another database initially.

Alternatives:

| Alternative | When it may be better |
|---|---|
| Qdrant | Dedicated vector workloads, richer payload filtering and larger vector scale |
| Pinecone | Fully managed vector service with minimal operations |
| Weaviate | Integrated vector database with additional search features |
| FAISS | Fast in-process experimentation but no built-in persistence or multi-user server behavior |
| Elasticsearch/OpenSearch | Strong hybrid keyword and vector search at larger operational complexity |

Why pgvector here: it keeps the architecture and free local setup small while
still representing a real production vector-search pattern.

### 4.4 Redis

Why selected:

- Fast and simple Celery broker
- Useful later for rate limiting, caching and short-lived coordination
- Small local Docker image

Alternatives:

| Alternative | Tradeoff |
|---|---|
| RabbitMQ | Stronger dedicated messaging semantics, but more operational overhead |
| PostgreSQL-backed queue | Fewer services, but less natural for high-volume background tasks |
| AWS SQS / Google Pub/Sub | Excellent managed production queues, but cloud-specific and not fully free locally |

Why Redis here: easy learning experience and enough capability for ingestion
jobs and future rate limiting.

### 4.5 Celery

Why selected:

- Mature Python background-job framework
- Retry, acknowledgement and worker-scaling support
- Works with Redis
- Keeps parsing libraries in Python

Alternatives:

| Alternative | Tradeoff |
|---|---|
| RQ | Simpler Redis queue, fewer workflow features |
| Dramatiq | Clean API and reliable processing, smaller ecosystem |
| FastAPI BackgroundTasks | Easy, but tasks are tied to the API process and not durable |
| Temporal | Powerful durable workflows, but excessive complexity for this stage |

Why Celery here: it demonstrates the standard broker/worker architecture without
introducing a large workflow platform.

### 4.6 SQLAlchemy

Why selected:

- Mature Python ORM and SQL toolkit
- Supports both SQLite and PostgreSQL
- Integrates directly with Alembic
- Allows ORM queries and explicit SQL for pgvector retrieval

Alternatives include Django ORM, SQLModel, Tortoise ORM and direct SQL.
SQLAlchemy provides the most flexibility for combining relational models with a
specialized vector query.

### 4.7 Alembic

Why selected:

- Standard migration tool for SQLAlchemy
- Versioned upgrades and downgrades
- Supports autogenerated migration drafts

An alternative is recreating tables at startup, but that is unsafe for
production data and cannot properly evolve existing schemas.

### 4.8 Local storage volume

Why selected now:

- Free and simple for Docker Compose
- Both API and worker can mount the same volume
- Easy to inspect while learning

Alternatives:

| Alternative | When it is preferable |
|---|---|
| MinIO | Local S3-compatible learning and multi-node object storage |
| Amazon S3 | Durable managed production object storage |
| Cloudflare R2 | S3-compatible object storage with different pricing/egress model |
| Supabase Storage | Convenient when already using Supabase |

Limitation: a local Docker volume is tied to one Docker host. Multiple machines
or ephemeral cloud instances require shared object storage such as S3 or MinIO.

### 4.9 OpenAI embeddings and chat

Why selected:

- One provider for both embeddings and answer generation
- Good model quality and straightforward SDK
- Low implementation complexity for a learning project

Alternatives:

| Alternative | Tradeoff |
|---|---|
| Ollama | Fully local and no per-request charge, but requires enough local CPU/GPU and model management |
| sentence-transformers | Free local embeddings; generation still needs another model |
| Gemini | Different free/paid quotas and model behavior |
| Groq-hosted open models | Fast generation, but separate embedding solution is needed |
| Azure OpenAI | Enterprise networking and governance, with more cloud configuration |

Important: changing the embedding model may change vector dimensions. The
database column and existing embeddings must be migrated or rebuilt together.

### 4.10 Vanilla HTML, CSS and JavaScript

Why selected:

- No frontend build pipeline
- Served directly by FastAPI
- Easy to understand for a learning project
- Low memory and deployment overhead

Alternatives:

| Alternative | When it may be better |
|---|---|
| React / Next.js | Complex state, routing, component library and authentication SDK integration |
| Vue | Component-based UI with a gentler learning curve |
| HTMX | Server-driven interactions with little JavaScript |

Why vanilla UI here: the application currently has a small number of screens.
A framework can be introduced when conversation history, account management and
more complex document workflows justify it.

### 4.11 Docker and Docker Compose

Why selected:

- Repeatable services and dependency versions
- One command starts the learning stack
- Named networking allows containers to use service names such as `db`
- Volumes preserve data when containers are recreated

Alternatives include installing every service directly, using Podman Compose,
or deploying to Kubernetes. Kubernetes would be disproportionate for the
current scale.

---

## 5. Data model

```text
Organization ──< Membership >── User
      |
      ├──< Document ──< DocumentChunk
      |        |
      |        └──── IngestionJob
      |
      └──< Conversation ──< Message
```

### Tables

#### `organizations`

Represents a tenant or workspace. Tenant-owned data is scoped to this ID.

#### `users`

Represents a user identity obtained from authentication.

#### `memberships`

Connects users to organizations and stores a role. Role-based authorization is
not fully enforced yet; the structure is ready for it.

#### `documents`

Stores metadata about an uploaded file:

- organization and uploader
- filename and MIME type
- storage key
- size and processing status
- processing error, if any

#### `document_chunks`

Stores searchable document segments:

- tenant and document IDs
- original order
- slide/sheet source
- extracted text and metadata
- 1,536-dimensional vector embedding

PostgreSQL uses the pgvector type. SQLite uses JSON as a development fallback.

#### `ingestion_jobs`

Tracks queue and worker processing state for a document.

#### `conversations`

Stores multiple conversations for each user and organization.

#### `messages`

Stores user and assistant messages plus citations returned with assistant
answers.

---

## 6. End-to-end flows

### 6.1 Authentication flow

```text
Browser -> Authorization: Bearer <JWT> -> FastAPI
FastAPI -> verify signature, expiration, sub and org_id
FastAPI -> Principal(user_id, organization_id)
```

Development mode can use:

```text
X-User-ID
X-Org-ID
```

This is deliberately insecure and must be disabled with:

```env
ALLOW_DEV_AUTH=false
```

The application verifies JWTs but does not currently implement registration,
password storage, login, password reset or token issuance. A real deployment
should integrate an identity provider and preferably verify asymmetric tokens
through JWKS.

### 6.2 Upload and ingestion flow

```text
1. User uploads .pptx or .xlsx.
2. API validates extension, emptiness and maximum size.
3. API stores file metadata and an ingestion job.
4. API writes the original file through StorageService.
5. API enqueues the document ID and tenant ID.
6. Celery worker loads the tenant-scoped document.
7. Worker parses slides/sheets and chunks long text.
8. Worker requests embeddings in a batch.
9. Worker replaces the document's old chunks in a transaction.
10. Worker marks the document and job completed.
11. UI polls until status becomes ready or failed.
```

### 6.3 Question-answering flow

```text
1. Browser sends conversation ID, question and selected document IDs.
2. API verifies conversation ownership.
3. API verifies all selected documents belong to the tenant.
4. For normal questions, the question is embedded.
5. PostgreSQL filters tenant/document rows and ranks vector similarity.
6. For broad summaries, chunks are read in document order instead.
7. Retrieved chunks are labeled and sent as evidence to the chat model.
8. The model is instructed to answer only from evidence and cite labels.
9. User and assistant messages are persisted.
10. UI displays answer plus filename, slide/sheet and excerpt citations.
```

The separate summary path prevents a common RAG failure where “summarize the
presentation” retrieves only one semantically prominent slide.

---

## 7. Reliability and robustness

### Current protections

- Database-backed state instead of process memory
- Tenant filtering at document, conversation and retrieval stages
- Explicit processing states and persisted errors
- Database migrations
- Dependency health checks
- Request IDs
- Upload size and extension validation
- API timeout and retry configuration for OpenAI
- Source citations
- Tests for parsing, API health, tenant isolation and upload-to-chat flow
- Persistent Docker volumes

### Failure behavior

| Failure | Current behavior |
|---|---|
| Invalid file type | API returns `415` |
| Empty or oversized file | API returns validation error |
| Parsing/embedding fails | Document and job become `failed` with an error message |
| OpenAI unavailable during chat | Request fails; API SDK retries transient calls |
| API restarts | Database and files remain |
| Worker restarts | Queued work remains in Redis subject to broker persistence/configuration |
| PostgreSQL unavailable | Readiness check fails and application operations fail |

### Important remaining improvements

- Configure Celery retry policies for transient embedding failures.
- Add idempotency keys to uploads to prevent duplicate work.
- Stream large uploads instead of reading the full file into API memory.
- Add MIME signature validation, not only file extensions.
- Add malware scanning.
- Add database backups and restore testing.
- Add structured JSON logs and metrics.
- Add dead-letter handling or a failed-job requeue endpoint.
- Add connection-pool sizing and statement timeouts.

---

## 8. Retrieval design and precision

### Current approach

For normal questions:

```text
question -> embedding -> cosine similarity -> top chunks -> LLM
```

PostgreSQL uses:

```sql
embedding <=> query_embedding
```

The initial migration creates an HNSW index with cosine operators.

For broad summaries:

```text
selected documents -> chunks in original order -> LLM
```

### Why chunking exists

Embedding an entire workbook or presentation as one vector would:

- exceed useful model context;
- blur unrelated sections into one representation;
- produce poor source granularity;
- make citations vague.

The parser preserves slide/sheet metadata before chunking, so retrieved evidence
can still cite the original location.

### Precision improvements not yet implemented

For stronger retrieval quality:

1. Add PostgreSQL full-text search for keyword matching.
2. Combine keyword and vector results using reciprocal-rank fusion.
3. Retrieve more candidates, then rerank with a cross-encoder or LLM.
4. Add similarity thresholds so weak evidence is rejected.
5. Add query rewriting for conversational follow-up questions.
6. Add OCR/vision extraction for screenshots and image-only slides.
7. Build an evaluation dataset with known questions, answers and sources.

Without an evaluation set, “better retrieval” is opinion rather than a measured
improvement.

---

## 9. Security model

### Implemented

- JWT signature and expiration verification
- Required `sub` and `org_id` claims
- Tenant-scoped backend queries
- Conversation ownership checks
- File size limits
- Secrets supplied through environment variables
- `.env` excluded from version control

### Not implemented yet

- Registration/login/token issuing
- Refresh-token rotation
- JWKS/asymmetric token verification
- Role/permission enforcement using membership roles
- Rate limiting and quotas
- CSRF strategy if authentication later uses cookies
- Malware scanning
- Prompt-injection/content-security policy
- Audit log
- Data-retention and deletion policy
- Encryption-key management

For an internet-facing deployment, development headers must be disabled.

---

## 10. Rate limiting, reverse proxy and load balancing

These are not currently implemented inside the Docker Compose project.

### Rate limiter

Recommended limits for a learning deployment:

```text
chat:    20 requests/minute/user
upload:   5 requests/hour/user
storage: configurable bytes/organization
usage:   configurable model tokens/day/organization
```

Redis can store counters. Possible libraries include `slowapi`, custom Redis
middleware, or limits applied by an API gateway.

Why it matters:

- Controls OpenAI cost
- Prevents accidental request loops
- Protects worker and database capacity

### Reverse proxy

Nginx, Caddy, Traefik or a cloud platform can provide:

- HTTPS termination
- maximum request-body limits
- compression and static caching
- security headers
- access logs
- routing to API replicas

It is not required to learn locally. Most hosting platforms already put a proxy
and TLS layer in front of the container.

### Load balancer

A load balancer is useful only when there is more than one API replica:

```text
               ┌── API replica 1
Load balancer ─┼── API replica 2
               └── API replica 3
```

The API's durable-state design supports this, but local file storage does not
support replicas across different machines. Shared object storage is required
before horizontal multi-host scaling.

---

## 11. Scaling strategy

### Stage 1: learning/local

```text
1 API + 1 worker + PostgreSQL + Redis on one machine
```

Use Docker Compose and named volumes.

### Stage 2: small public demo

```text
managed container
managed PostgreSQL + pgvector
managed Redis
S3-compatible storage
identity provider
platform HTTPS
```

One API and one worker are usually enough for low traffic.

### Stage 3: increasing traffic

- Add multiple API replicas behind a load balancer.
- Scale workers based on queue depth.
- Use shared object storage.
- Tune PostgreSQL indexes and connection pooling.
- Add caching where measurements show repeated work.
- Add rate limits and organization quotas.

### Stage 4: large retrieval workload

Only after measurement, consider:

- dedicated vector database;
- read replicas;
- separate ingestion and query databases;
- event streaming;
- Kubernetes or managed orchestration.

These are not automatically “more robust.” They add operational failure modes
and should be introduced only when the simpler architecture reaches a measured
limit.

---

## 12. Project structure

```text
migration/
├── app/
│   ├── api/
│   │   └── routes/          # HTTP endpoints
│   ├── core/                # config, auth, logging
│   ├── db/                  # SQLAlchemy models and sessions
│   ├── schemas/             # API request/response contracts
│   ├── services/            # domain and infrastructure logic
│   ├── workers/             # Celery app and tasks
│   └── main.py              # FastAPI composition root
├── frontend/                # SlideSheet AI web UI
├── alembic/                 # versioned database migrations
├── docs/                    # focused architecture notes
├── tests/                   # automated tests
├── Dockerfile               # API/worker image
├── docker-compose.yml       # local production-style stack
├── requirements.txt         # Python dependencies
└── PROJECT.md               # this guide
```

---

## 13. Local operating modes

### Lightweight SQLite mode

```env
DATABASE_URL=sqlite:///./migration.db
INGESTION_MODE=sync
ALLOW_DEV_AUTH=true
AUTO_CREATE_TABLES=true
```

Useful for quick development and tests. SQLite vectors are JSON values and
similarity is calculated in Python, so this mode does not test pgvector.

### PostgreSQL/pgvector mode

```bash
docker compose up --build
```

Uses:

```text
DATABASE_URL=postgresql+psycopg://chatbot:chatbot@db:5432/chatbot
INGESTION_MODE=celery
```

This is the mode that exercises the intended production architecture.

---

## 14. Verification commands

### Run tests

```bash
cd migration
source .venv/bin/activate
pytest -q
```

### Start the full stack

```bash
docker compose up --build
```

### Check services

```bash
docker compose ps
```

### Check pgvector

```bash
docker compose exec db \
  psql -U chatbot -d chatbot \
  -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"
```

### List database tables

```bash
docker compose exec db psql -U chatbot -d chatbot -c "\dt"
```

### Inspect document and chunk counts

```bash
docker compose exec db psql -U chatbot -d chatbot -c \
  "SELECT d.filename, d.status, COUNT(c.id) AS chunks
   FROM documents d
   LEFT JOIN document_chunks c ON c.document_id = d.id
   GROUP BY d.id
   ORDER BY d.created_at DESC;"
```

### Verify embedding dimensions

```bash
docker compose exec db psql -U chatbot -d chatbot -c \
  "SELECT vector_dims(embedding), COUNT(*)
   FROM document_chunks
   GROUP BY vector_dims(embedding);"
```

Expected embedding dimension with the current model is `1536`.

---

## 15. Architectural decisions summary

| Decision | Reason |
|---|---|
| Modular monolith | Simpler deployment and debugging at current scale |
| Separate worker | Expensive ingestion should not block API requests |
| PostgreSQL + pgvector | Relational and vector data in one reliable system |
| Redis + Celery | Durable background-job boundary with independent scaling |
| Tenant IDs on records | Backend-enforced multi-user isolation |
| JWT principal | Stateless identity suitable for multiple API replicas |
| Alembic | Safe, repeatable schema evolution |
| Local storage abstraction | Free local learning now, replaceable by S3 later |
| Vanilla frontend | No build-system overhead for the current UI complexity |
| Docker Compose | Repeatable local production-style environment |
| Ordered summary path | Whole-document summaries should not rely only on top-k similarity |
| Citations | Users can inspect the evidence behind generated answers |

---

## 16. Honest readiness assessment

The project currently demonstrates:

- persistent multi-tenant data;
- multiple conversations per user;
- asynchronous ingestion;
- pgvector retrieval;
- grounded answers with citations;
- migrations, health checks, Docker and automated tests.

It is suitable for learning, local use and a controlled demo.

Before calling it a hardened public production service, add:

1. a real identity provider;
2. PostgreSQL RLS and role authorization;
3. Redis-backed rate limits and usage quotas;
4. S3-compatible shared storage;
5. malware/MIME validation;
6. retries and dead-letter handling;
7. metrics, structured logging and alerting;
8. backups and restore drills;
9. OCR and retrieval evaluation;
10. TLS/proxy configuration supplied by the hosting platform or reverse proxy.

Robustness is not one library. It comes from clear ownership boundaries,
durable state, controlled failure behavior, observability, security and tested
recovery paths. This project establishes those boundaries without hiding the
remaining work.

---

## 17. Changelog

Chronological record of changes made after the initial migration, so the reasoning behind each
one stays attached to the project instead of living only in chat history. Each entry says what
changed, why, and which file(s) were touched. See `CHANGE_PLAN.md` for the full backlog this is
worked from.

<!-- Newest entries at the top. -->

### Pagination on GET /documents and GET /conversations (Phase 4 start)

Files touched: `app/api/routes/documents.py`, `app/api/routes/conversations.py`,
`tests/test_pagination.py` (new).

- Both endpoints previously returned *every* row for a tenant with no limit — fine at small
  scale, unbounded as a workspace grows. Added `limit` (default 50, max 200) and `offset`
  (default 0) query params, both validated by FastAPI (`Query(ge=..., le=...)` → `422` on
  out-of-range values, e.g. `limit=0` or `limit=201`).
- **Response body shape is unchanged** — still a plain JSON array, not a `{items, total}`
  envelope — specifically so this is non-breaking for the existing frontend (`app.js` does
  `state.documents = documents` assuming a bare array). Total count is instead exposed via an
  `X-Total-Count` response header, so a future paginated UI has what it needs without an API
  contract change today.
- **Scope note:** the architecture doc's P4-4 item also mentions paginating "messages," but no
  message-listing endpoint exists in this codebase at all (`conversations.py` only has
  `POST /{id}/messages` to send one — verified by grepping the file, not assumed). Nothing to
  paginate there yet; flagging this as a separate, pre-existing product gap (message history
  isn't fetchable at all today, not even unpaginated) rather than silently building a new endpoint
  under the "pagination" label.
- 4 new tests: default behavior still returns everything for a small tenant, pages are
  disjoint and correctly ordered (deterministic via explicit `created_at` timestamps rather than
  relying on tie-breaking), out-of-range params rejected with 422, and the same pattern verified
  for conversations. Full suite: 32 passed.

### Cheap-LLM re-ranker on top of the fused candidate pool

Files touched: `app/services/openai_service.py`, `app/services/chat.py`,
`tests/test_openai_service.py`, `tests/test_api_flow.py`.

- `retrieve_chunks()` is now called with `settings.retrieval_candidates` (20) instead of
  `retrieval_top_k` (8) as its `limit` — it returns a wider candidate pool on both the Postgres
  (RRF-fused) and SQLite (cosine) paths.
- New `OpenAIService.rerank(question, candidates, top_n, organization_id=None) -> list[int]`:
  sends the question plus the numbered candidates (truncated to 400 chars each, to keep this
  extra call cheap) to `gpt-4o-mini`, asking for a JSON array of the best `top_n` candidate
  numbers, most relevant first. Chosen over a cross-encoder specifically to avoid a new ML
  dependency (sentence-transformers/torch) in a project whose retrieval otherwise runs entirely
  inside Postgres + the OpenAI API it already calls — matches the architecture doc's "what NOT to
  add" guidance to prefer tuning/composing what's already there.
- **Parsing is defensive by design**: extracts the first `[...]`-shaped substring via regex
  (tolerates stray prose or markdown fences around the JSON), validates each number is an int
  within range and de-duplicates, and **falls back to the original candidate order** if parsing
  fails for any reason — logged as a `openai_rerank_parse_failed` warning rather than raising.
  Re-ranking is a quality improvement; it should never be able to break the chat flow.
- `chat.py`: after the similarity-threshold check (still evaluated against the pre-rerank
  fused/cosine score — re-ranking doesn't produce a comparable numeric score), if there are more
  candidates than `retrieval_top_k`, calls `rerank()` and maps its returned indices back onto the
  candidate list to build the final `chunks` used for context and citations.
- 4 new unit tests on `rerank()` (identity short-circuit, correct parse+reorder, fallback on
  unparseable output, robustness to out-of-range/duplicate indices) + 1 new end-to-end test that
  forces >`retrieval_top_k` chunks via a 150-row workbook, injects a deterministic fake re-ranker,
  and asserts the final citations reflect the fake's reversed order (not just the original
  retrieval order) — proving the wiring is actually applied, not just present. Full suite:
  28 passed (23 SQLite/Postgres-integration + 5 new).

### Hybrid retrieval — tsvector full-text + vector search fused via RRF (PostgreSQL only)

Files touched: `alembic/versions/0004_add_fulltext_search.py`, `app/services/retrieval.py`,
`app/services/chat.py`, `app/core/config.py`, `alembic.ini`,
`tests/test_postgres_retrieval.py` (new).

- **Migration 0004** adds a PostgreSQL-only generated column, `document_chunks.text_search
  tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED`, plus a GIN index. It's a
  DB-managed generated column (not something the app ever writes to), so it isn't modeled on the
  SQLAlchemy `DocumentChunk` class at all — mirrors how the existing pgvector cosine query is
  already raw SQL rather than ORM-mapped. No-ops on SQLite (guarded by dialect check), consistent
  with the HNSW index in migration 0001 also being Postgres-only.
- **`retrieve_chunks()`** on the PostgreSQL path now runs both an ANN vector search and a lexical
  `plainto_tsquery` search as CTEs, each returning its own top-`retrieval_candidates` ranking, then
  fuses them with **Reciprocal Rank Fusion**: `score = SUM(1 / (rrf_k + rank))` across whichever
  list(s) a chunk appears in. This means a question containing an exact term (a product code, a
  proper noun) that the embedding model doesn't weight heavily can still surface via the lexical
  side, while a paraphrased question with no keyword overlap still works via the vector side.
  `retrieval_rrf_k` defaults to `60` — the standard value from the RRF literature (Cormack et al.,
  2009), validated broadly, unlike the corpus-specific numbers elsewhere in this changelog that
  default to 0/disabled.
- `retrieve_chunks()` gained a `question: str` parameter (needed to build the lexical tsquery);
  `chat.py`'s call site updated accordingly.
- SQLite keeps its existing manual-cosine fallback unchanged — no lexical fusion there, since
  SQLite has no full-text search to fuse with; this was already the precedent for the HNSW index.
- **Caveat worth flagging explicitly:** `chunks[0].score` now means an RRF score on Postgres
  (small positive numbers, roughly bounded by `2/(rrf_k+1)`) instead of a raw 0–1 cosine
  similarity — a different scale/meaning than what the SQLite fallback still returns. The
  similarity-threshold feature added earlier in this changelog (`retrieval_min_score`) still
  defaults to `0` (disabled), so nothing currently breaks from this, but anyone enabling that
  threshold in Postgres/production needs to tune it against RRF-scale scores, not cosine-scale
  ones — exactly the kind of thing the still-deferred evaluation set (Phase 2) should settle
  empirically rather than guessing.
- **New test file `tests/test_postgres_retrieval.py`** — closes a previously-acknowledged gap
  (§12: "no test currently exercises the pgvector/Postgres-specific SQL path"). Runs the real
  Alembic migrations against an actual PostgreSQL+pgvector instance
  (`TEST_POSTGRES_URL`, defaults to `postgresql+psycopg://chatbot:chatbot@localhost:5544/chatbot`)
  and is skipped automatically if that instance isn't reachable, so it doesn't break `pytest` on a
  machine without one. Verifies: the tsvector column/GIN index actually exist; a chunk matched
  only by an exact keyword (not by vector similarity) is still surfaced by hybrid search; tenant
  and document-ID filters are still respected in the hybrid query; non-`ready` documents are still
  excluded. All 4 passed against a real container during development.
- `alembic.ini`: added `path_separator = os` to silence an Alembic deprecation warning surfaced
  while building the above test (unrelated cleanup, done while already in this file).
- Full suite: 23 passed (19 SQLite-based + 4 new Postgres integration tests).

### Token-based chunking (`app/services/parsing.py`, `requirements.txt`, `Dockerfile`)

`chunk_sections()` already split text per `ParsedSection` (i.e. never merged two different
slides/sheets into one chunk) — that part of the architecture doc's P3-5 concern didn't actually
apply to this codebase's `parsing.py`. What *was* real: sizing was character-based
(`chunk_size=1200` chars), a rough proxy for what actually matters — the embedding model's token
limits and per-request token budget.

- Added `tiktoken==0.13.0`. `chunk_sections()` now encodes each section's text with the encoding
  for the configured `openai_embedding_model` (`cl100k_base` for `text-embedding-3-small`) and
  slides a token-index window (`chunk_size_tokens=300`, `overlap_tokens=40`) instead of a
  character window. Still never crosses a section boundary — same guarantee as before, just
  correctly sized now.
- Falls back to `cl100k_base` if `tiktoken.encoding_for_model()` doesn't recognize a future model
  name, rather than failing ingestion outright.
- Metadata key renamed `character_offset` → `token_offset` (nothing else referenced the old key).
- `Dockerfile`: added a build-time step (`ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache` +
  pre-warming `cl100k_base`/`o200k_base`) so tiktoken's vocab files are baked into the image —
  runtime ingestion never needs outbound network access for tokenization, only the build step
  does (confirmed network access works in this environment before adding the dependency).
- 2 new tests verifying a short section stays whole, a long section splits without ever crossing
  into an adjacent section's text, and chunks carry `token_offset` metadata. Full suite:
  19 passed.

### Similarity-threshold rejection for weak evidence (`app/services/chat.py`, `app/core/config.py`)

Previously, vector retrieval always returned `retrieval_top_k` chunks regardless of how weak
their similarity to the question actually was — if the corpus genuinely couldn't answer a
question, the LLM was still handed 8 barely-related chunks and asked to do its best, which
invites confident-sounding hallucination instead of an honest "I don't know."

- Added `Settings.retrieval_min_score` (default `0.0`, i.e. disabled). **Deliberately not
  defaulting to a nonzero guess** — a good cosine-similarity cutoff is corpus- and
  embedding-model-specific and should come from measuring against an evaluation set (still
  deferred — see Phase 2 in `CHANGE_PLAN.md`), not an unverified number. Same reasoning as the
  `openai_*_price_per_1k_tokens` settings defaulting to 0.
- When enabled and the best-scoring chunk (`chunks[0].score`, already sorted descending by both
  the Postgres and SQLite retrieval paths) falls below the threshold, `answer_question()` returns
  a canned "insufficient evidence" response **without calling the chat model at all** — saves an
  OpenAI call on questions we already know can't be answered well.
- Explicitly does **not** apply to summary-style questions — `get_summary_chunks()` returns
  chunks in document order with a `score=1.0` sentinel, which isn't a real relevance signal, so
  gating on it would be meaningless.
- 2 new tests: one forces an unreachable threshold (`1.5`, above the max possible cosine
  similarity of `1.0`) and asserts the LLM is never called and citations are empty; another
  confirms the same unreachable threshold does *not* block a summary question. Full suite:
  17 passed.

### Embedding model/version provenance (`document_chunks.embedding_model` / `.embedding_dims`)

Files touched: `app/db/models.py`, `alembic/versions/0003_add_embedding_provenance.py`,
`app/services/ingestion.py`, `tests/test_api_flow.py`.

- Added two nullable columns to `DocumentChunk`: `embedding_model` (which model produced this
  vector) and `embedding_dims` (its dimensionality). Without this, upgrading
  `OPENAI_EMBEDDING_MODEL` in the future would silently mix old and new vectors in the same
  column with no way to detect it or know which documents need re-embedding.
- `ingest_document()` now stamps both fields from `Settings.openai_embedding_model` and
  `len(embedding)` on every inserted chunk.
- Migration `0003` follows the same idempotent, inspect-before-alter pattern as `0002` (0001
  rebuilds from live `Base.metadata`, so a fresh DB already has these columns by the time 0001
  finishes — verified by running `alembic upgrade head` against a throwaway fresh SQLite DB;
  0001 → 0002 → 0003 all applied cleanly, final `document_chunks` schema has both columns).
- Test suite extended to assert stored chunks carry the correct model name and dimension count
  after ingestion. Full suite: 15 passed.

### Structured JSON logging + per-call token/latency capture

Files touched: `app/core/logging.py`, `app/main.py`, `app/core/config.py`,
`app/services/openai_service.py`, `app/services/ingestion.py`, `app/services/chat.py`,
`tests/test_openai_service.py`, `tests/test_api_flow.py`.

- `app/core/logging.py`: replaced the plain-text log format with a JSON formatter. Added a
  `request_id_var` `ContextVar` plus a logging filter so every log line emitted during a request
  automatically carries that request's `X-Request-ID`, without threading it through every
  function call. Any `extra={...}` passed to a log call rides along as extra JSON fields.
- `app/main.py`: the existing request-id middleware now sets `request_id_var` for the request's
  duration and logs one `request_completed` line per request with method, path, status code, and
  duration in ms.
- `app/core/config.py`: added three pricing settings —
  `openai_embedding_price_per_1k_tokens`, `openai_chat_input_price_per_1k_tokens`,
  `openai_chat_output_price_per_1k_tokens` — all defaulting to `0.0` (cost estimation disabled).
  Deliberately not hardcoding a dollar figure since provider pricing changes over time; set these
  to your current rate card to enable `estimated_cost_usd` in the logs, otherwise only token
  counts and latency are captured.
- `app/services/openai_service.py`: `embed()` and `answer()` now log a structured
  `openai_embed` / `openai_chat` event per call with token usage (from the OpenAI response's
  `usage` field), duration, and estimated cost (`None` unless pricing is configured). Both methods
  gained an optional `organization_id` param purely for log correlation — public behavior/return
  values unchanged.
- `app/services/ingestion.py` and `app/services/chat.py`: pass `organization_id` into every
  `embed()`/`answer()` call, so `openai_embed`/`openai_chat` log lines are tenant-attributable
  in practice — e.g. `jq 'select(.organization_id=="org-123")'` over the log stream gives that
  org's OpenAI usage.
- Full suite: 15 passed (2 existing test doubles in `test_api_flow.py` updated to accept the new
  optional `organization_id` kwarg; no behavior change to what they assert).

### Readiness check now verifies Redis in Celery mode (`app/api/routes/health.py`)

`GET /api/v1/health/ready` previously only ran `SELECT 1` against Postgres. In
`INGESTION_MODE=celery`, the API could report `ready` while Redis (the Celery broker) was down —
uploads would be accepted and silently enqueued into a dead broker, with documents stuck in
`queued`/`uploaded` indefinitely and no visible failure. Readiness now also pings Redis
(`redis.from_url(...).ping()`, 2s connect timeout) whenever `ingestion_mode == "celery"`, returning
`503` if unreachable. Skipped entirely in `sync` mode, where no Redis is required at all — so local
dev/tests (which default to sync) are unaffected.

### Sub-batch embedding calls (`app/services/openai_service.py`)

`OpenAIService.embed()` previously sent every chunk from a document in a single
`embeddings.create()` call. A large `.pptx`/`.xlsx` producing more chunks than the endpoint's
per-request input/token ceiling would fail ingestion outright. Now splits into batches of 256
texts (well under the API's 2048-item limit given ~300-token chunks) and runs multiple batches
concurrently via a small thread pool. Public signature (`embed(texts) -> list[list[float]]`)
is unchanged, so callers and existing tests needed no changes.
