# PrüfAI local product walkthrough

This is a safe, synthetic development walkthrough. It is not a production
deployment and it contains no official provider material.

## Start the services

1. Copy the repository examples into local environment files. Keep credentials
   local and never commit them.
2. Start PostgreSQL/pgvector and Redis using the repository compose file. The
   repository's pinned MinIO image currently requires registry access; if it
   cannot be pulled, candidate audio object-storage flows remain unavailable.
3. Apply migrations with `DATABASE_URL=... python -m alembic upgrade head`.
4. Seed the demo once:

   ```text
   DATABASE_URL=postgresql+psycopg://prufai:prufai_local_only@localhost:5432/prufai \
   PYTHONPATH=services/api python -m app.scripts.seed_demo
   ```

   The command is DEV-only and idempotent. It refuses `ENVIRONMENT=production`.
5. Start the API with `make api-dev` (or uvicorn on port 8002 when port 8000
   is already in use) and the web app with `NEXT_PUBLIC_API_BASE_URL=http://localhost:8002
   NEXT_PUBLIC_DEMO_MODE=true npm run dev -- --port 3001`.

## Demo access

All four accounts use the same local-only password: `Demo-PrufAI-2026!`.

| Account | Role | Walkthrough |
| --- | --- | --- |
| `candidate@demo.prufai.local` | Candidate | assignment, submitted attempt, released result |
| `reviewer@demo.prufai.local` | Institution reviewer / exam manager | writing and speaking review queues |
| `admin@demo.prufai.local` | Institution administrator | moderation, appeals, exam studio |
| `history@demo.prufai.local` | Historical candidate fixture | immutable submitted evidence |

These credentials are synthetic and must never be reused outside local development.

## Click-through order

1. Sign in as the candidate and open **Candidate exam**. Inspect the assignment,
   submitted state, and **View released results**.
2. Open **Exam Studio** to see the governed draft → blueprint → publish flow.
3. Sign out and enter as the reviewer. Open **Writing review** and **Speaking
   review**. Speaking keeps original audio as primary evidence and transcription
   as derived evidence.
4. Sign out and enter as the administrator. Visit **Result moderation**, then
   **Appeals**. Open a case to see versioned governance language.

The shell intentionally shows useful loading/empty/error states when a service
or optional integration is unavailable. Phase 8 Step 2 result release remains
on `phase/8-result-release` and is not exposed on `main`.
