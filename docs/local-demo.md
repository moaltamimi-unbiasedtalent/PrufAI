# PrüfAI local product walkthrough

This explicit development mode demonstrates one original German reading question,
a candidate assignment, a real save/resume/submit flow, and institution inspection
of persisted assignments and a human-reviewed synthetic practice result.
No official provider material, invented audio objects, transcripts or AI judgments
are included. Full Control Centre is deferred to a separate product milestone.

## Start the services

1. Install the repository's existing dependencies using `make install`. Do not
   upgrade dependencies for the walkthrough. Use PostgreSQL 16 with pgvector;
   the CI service image is `pgvector/pgvector:pg16`. Plain PostgreSQL without the
   vector extension cannot apply the existing migrations. Preserve existing
   volumes/databases. An existing native PostgreSQL/pgvector installation is also
   supported; use a dedicated local database rather than resetting developer data.
2. Start Redis if needed. MinIO is not required for this objective-only demo;
   local audio demonstration is unavailable. The required remote CI storage
   regressions still run against real MinIO.
3. Set `DATABASE_URL` to that PostgreSQL database and run, from `services/api`:

   ```sh
   python3 -m alembic heads
   python3 -m alembic upgrade head
   ```

4. From the repository root, reconcile the demo explicitly:

   ```sh
   ENVIRONMENT=development PRUFAI_DEMO_ENABLED=1 \
   PYTHONPATH=services/api python3 -m app.scripts.seed_demo
   ```

   `DATABASE_URL` must be present in this terminal. The command requires both
   development environment and the explicit flag, and refuses production/test.
   It repairs missing demo users, credentials, roles, hierarchy and assignments,
   without resetting submitted attempts or deleting unrelated data. The v2
   organisation leaves any legacy invalid v1 fixture intact and separate.

5. In separate terminals, start API and frontend:

   ```sh
   # Repository root; DATABASE_URL must still be configured.
   ENVIRONMENT=development OBJECT_STORAGE_BACKEND=memory \
   python3 -m uvicorn app.main:app --app-dir services/api --port 8002

   # apps/web
   PRUFAI_DEMO_ENABLED=1 PRUFAI_API_BASE_URL=http://127.0.0.1:8002 \
   npm run dev -- --port 3001
   ```

   Memory object storage is explicitly development-only here and is not used as
   evidence of audio persistence. The AI health service/worker is not required for
   this objective demo. No transcription or live AI proposals are advertised.

6. Open `http://localhost:3001/login`. Every browser API request uses the Next.js
   same-origin `/api` rewrite; only the server-side `PRUFAI_API_BASE_URL` selects
   the FastAPI target. Do not use the removed `NEXT_PUBLIC_API_BASE_URL` or
   `NEXT_PUBLIC_ORGANISATION_ID` configuration. Organisation choices come from
   actual active memberships returned by `/auth/me`.

Demo credentials/helpers/banner are rendered only when `NODE_ENV=development`
and `PRUFAI_DEMO_ENABLED=1`. Production builds exclude the demo guide even when
the flag is supplied. Access tokens stay in memory; the HttpOnly refresh cookie,
same-origin CSRF bootstrap and existing refresh/logout endpoints restore and
revoke server sessions. Logging out or switching accounts clears and locks the
account's offline queue. No tokens or response payloads are put in URL parameters.

## Demo access

All four accounts use the same local-only password: `Demo-PrufAI-2026!`.

| Account | Role | Walkthrough |
| --- | --- | --- |
| `candidate@demo.prufai.local` | Candidate | assigned original question; save twice, resume, submit |
| `reviewer@demo.prufai.local` | Existing EXAM_MANAGER role | institution candidate/exam/result inspection |
| `admin@demo.prufai.local` | Existing INSTITUTION_ADMIN role | institution inspection and authorized governance queues |
| `history@demo.prufai.local` | Candidate | existing genuine submitted objective attempt and practice result |

These credentials are synthetic and must never be reused outside local development.
Operators can set `PRUFAI_DEMO_PASSWORD` when seeding. Passwords are not printed by
the seed, persisted in browser token storage, or filled into the login form.
The default guide describes the default local password; with a custom seed
password, use the operator-provided value instead.

## Click-through order

1. Sign in as Candidate, open **My exams**, then **Prepare exam** and **Start**.
2. Select `Um 10 Uhr.` and wait for **Saved response revision: 1**. Select
   `Um 9 Uhr.` and wait for revision 2. Reload; the actual attempt URL resumes
   with the second response. Submit; the attempt becomes read-only. The seed
   does not silently reset this completed attempt on rerun.
3. Sign out using the server-backed action. Sign in as Reviewer, then open
   **Institution inspection**. Inspect the candidate/exam status and the
   historical candidate's released practice result with raw score and provenance.
4. Sign out, then sign in as Institution Admin. Inspect the same records and
   authorized queues. Candidate cannot open the institution route or its API.
   Reviewers/admins do not get candidate navigation merely from institution roles.
5. Writing/Speaking queues are empty for the valid synthetic objective dataset.
   Exam Studio is labelled a preview; its authoring UI is not advertised as
   connected. No nonexistent Control Centre link is exposed.

The shell provides loading/error/retry and explicit access-restricted states.
The `/status` endpoint is a basic liveness surface, not database readiness proof.
Phase 8 Step 2 release/certification is not part of this PR.

## Acceptance evidence

Frontend tests cover refresh retry, CSRF rotation on logout, account-generation
boundaries, role navigation and explicit development gating. Outbox regressions
cover separate account databases, logout deletion and in-flight account changes.
In the development-only `/docs` guide, the offline privacy diagnostic can also
write a synthetic check into the real IndexedDB queue. Queue one as Candidate,
sign out, sign in as Admin and inspect: the count is zero. After signing out and
returning as Candidate it is still zero. The diagnostic never sends an exam
response and is unavailable in production.
`test_demo_seed_postgresql_integration.py` runs the real seed/runtime services
with required PostgreSQL after Alembic and verifies persisted first/second save,
reload, submit and idempotent reseeding. Real browser verification complements
these tests; mocked fetch tests alone are not product acceptance.
