# PrüfAI local product walkthrough

This explicit development mode demonstrates one original German reading question,
a candidate assignment, a real save/resume/submit flow, and institution inspection
of persisted assignments and a human-reviewed synthetic practice result.
No official provider material, invented audio objects, transcripts or AI judgments
are included. The Control Centre MVP provides a separate, read-only platform
governance workspace using actual stored metadata and platform permissions.

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
   The platform operator is a separate deterministic demo identity with the
   existing `PRUFAI_SUPER_ADMIN` platform role and no organisation membership.
   If its email is already owned by a different identity, or the demo platform
   user has been linked to a tenant, the seed stops instead of taking over that
   identity or deleting memberships. Use a dedicated demo database to avoid
   collisions with unrelated developer accounts.

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

All six accounts use the same local-only password: `Demo-PrufAI-2026!`.

| Account | Role | Walkthrough |
| --- | --- | --- |
| `candidate@demo.prufai.local` | Candidate | assigned original question; save twice, resume, submit |
| `reviewer@demo.prufai.local` | Existing EXAM_MANAGER role | institution candidate/exam/result inspection |
| `admin@demo.prufai.local` | Existing INSTITUTION_ADMIN role | institution inspection and authorized governance queues |
| `history@demo.prufai.local` | Candidate | existing genuine submitted objective attempt and practice result |
| `golden.candidate@demo.prufai.local` | Candidate | integrated B1 journey; starts without an assignment, attempt or result |
| `platform.admin@demo.prufai.local` | Existing PRUFAI_SUPER_ADMIN platform role | read-only Control Centre; no tenant membership or candidate impersonation |

These credentials are synthetic and must never be reused outside local development.
Operators can set `PRUFAI_DEMO_PASSWORD` when seeding. Passwords are not printed by
the seed, persisted in browser token storage, or filled into the login form.
The default guide describes the default local password; with a custom seed
password, use the operator-provided value instead.

### Integrated B1 seed fixture

`Golden B1 Practice Candidate` is separate from the legacy `candidate@` runtime
fixture and the `history@` inspection result. Its exam is **Golden B1 German
Practice — SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL**, version
`golden-b1-v1`. It uses the existing LANGUAGE/de/B1/PRACTICE models, one active
original SELECT_ONE reading item, and the deterministic one-point answer key.
The visible Tuesday course question offers `Um 9 Uhr.` (one point) and
`Um 10 Uhr.` (zero points). No pass/fail threshold or official qualification is
implied; the current scorer reports raw points and an undetermined outcome when
no outcome rule is configured.

The seed creates the candidate roster entry and deliverable exam, but **does not
assign, start, submit, score, approve or release the golden candidate's journey**.
An authenticated Institution Admin must select that candidate and exam in the
product workflow. Subsequent scoring and human approval/release use the actual
services. Reseeding preserves any existing assignment, saved revisions, submitted
attempt and result history; it cannot reset a completed golden journey. Use a
fresh isolated demo database for a new end-to-end walkthrough.

The new Standard Pack pins a small original internal practice-policy document
version. Its full original content is stored in the development seed and durably
in document notes with its exact SHA-256 checksum. The explicitly invoked seed
records this prerequisite as **seeded human-approved internal policy metadata**.
It is not provider material, an uploaded object or proof of source ingestion:
there is no storage key, uploaded file, extracted page or ingestion job.
`KnowledgeRegistryService.approve` is not claimed to have run for this metadata
fixture. Standard Pack validation and lifecycle transitions, and Exam Studio's
checksum, review, approval, publication and activation, execute their existing
services with the seeded authorized platform operator.

The CLI returns `golden_candidate_user_id`, `golden_exam_version_id`,
`golden_pack_version_id` and `golden_item_version_id` for acceptance tooling.
The product selects actual API roster/exam records; it does not hard-code these
identifiers. A conflicting unrelated identity using the golden email is refused
without changing its credentials or granting membership.

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
5. Sign out, then sign in as Platform Admin. The normal authenticated session
   opens `/control-center`. Inspect **Overview**, **Organisations**, **Standards**,
   **Source Registry**, **Question Bank**, **Exam Governance**, **Audit**,
   **AI Operations**, **System Health**, and **Certification Oversight**. These
   views show metadata from the real database. Empty source/AI/certification
   tables have explicit empty states; the seed does not fabricate operational
   history, AI execution, provider entitlements or certificate records.
   Question Bank and Exam Governance initially show platform-shared metadata.
   Open the demo academy in **Organisations** and follow its authorized Question
   Bank or Exam metadata link to inspect the seeded private practice material.
   The explicit organisation filter selects metadata; it does not switch to a
   candidate or institution identity.
6. Sign out and return as Institution Admin or Candidate. Opening
   `/control-center` must show access denied. The server independently rejects
   their calls to `/api/v1/platform/control-center/*`; choosing an organisation
   or sending a platform-looking header cannot grant platform permissions.
7. Writing/Speaking queues are empty for the valid synthetic objective dataset.
   Exam Studio is labelled a preview; its authoring UI is not advertised as
   connected.

The shell provides loading/error/retry and explicit access-restricted states.
The `/status` endpoint is a basic liveness surface, not database readiness proof.
Phase 8 Step 2 release/certification is present in the merged baseline, but this
seed creates only its existing human-reviewed practice result. Control Centre
certification oversight is read-only, and official certification remains denied.
The platform role never creates a provider authorization merely from an exam
name, Standard Pack alignment or CEFR level.

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
`test_control_centre_postgresql_integration.py` exercises normal password/JWT
authentication against Alembic-migrated PostgreSQL, verifies the platform-only
demo identity, and checks server-side Control Centre permission and safe metadata
boundaries. Platform audit views expose event/actor/entity references and safe
correlation fields, never the raw audit metadata, candidate answers, prompts,
passwords, tokens or reviewer-private notes.
