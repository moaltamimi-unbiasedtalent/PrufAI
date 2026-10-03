# Phase 6 / Step 1 final governance and integration closure

## Scope and source control

This closure covers the Candidate Exam Runtime only. Scoring, correctness,
results, certificates, proctoring, adaptive delivery and Phase 6 / Step 2 are
not implemented.

- Repository: `https://github.com/moaltamimi-unbiasedtalent/PrufAI.git`
- Branch: `phase/6-candidate-runtime`
- Starting commit: `65ae34e`
- Migration: `0014_candidate_runtime` (revision length 22), after `0013_exam_studio`
- Manual PR URL: `https://github.com/moaltamimi-unbiasedtalent/PrufAI/pull/new/phase/6-candidate-runtime`

## Database guarantees

Migration `0014_candidate_runtime` creates the five runtime tables and enforces
the following in PostgreSQL: assignment foreign keys to organisation,
candidate user and the exact `exam_version_id`; unique attempt number per
assignment; a partial unique index allowing only one `READY`/`IN_PROGRESS`
attempt per assignment; unique response per attempt and item binding; unique
revision number per response; unique client operation ID per attempt; a unique
attempt foreign key for the one-to-one session; candidate and organisation
foreign keys on attempts and responses; and indexes for attempt status and
deadline. The service locks the assignment row while counting attempts and
creating a new attempt. This row lock plus `maximum_attempts` check and the
database partial unique index are the concurrency defence. A live race test
requires PostgreSQL and remains a remote-CI obligation.

## Runtime governance

Availability is evaluated from controlled assignment status,
`available_from`, `available_until`, and server time. The local unit gate covers
before, during and after the window; client timestamps are not trusted. Only
`PUBLISHED`/`ACTIVE` versions with publishable rights and active items can be
launched. DRAFT, HUMAN_REVIEW, REJECTED, SUPERSEDED and RETIRED versions are
not assignable through the runtime path. `OFFICIAL_CERTIFICATION` is explicitly
blocked because no authorization domain exists.

The call path is `CandidateRuntimeService._safe_plan` ->
`question_bank.service.delivery_projection`. It reads the pinned ExamVersion
hierarchy and the delivery-safe Question Bank projection; it does not consume
the admin manifest and strip answer fields afterward. The plan is serialized,
checksummed and pinned to the attempt. The safe projection excludes correct
answers, answer keys, solutions, scoring keys, reviewer/AI/internal validation,
rights notes and private object URLs. The synthetic JSON serialization test
passes for these fields. Supported modes are `SELECT_ONE`, `SELECT_MULTIPLE`,
`TEXT` and `LONG_TEXT`; unsupported modes are blocked with
`RUNTIME.UNSUPPORTED_RESPONSE_MODE`.

Server timestamps govern start, deadline, expiry, heartbeat and resume. The
request-time expiry guard and `expire_overdue()` sweep hook enforce the
post-deadline block; deployment of a periodic worker is still operational
work. Submission is final, duplicate submission is idempotent, and writes are
blocked after submission or expiry. Navigation rejects unknown bindings and
backwards moves for linear/no-backtrack plans.

## Ownership, responses and offline behaviour

Candidate routes derive candidate and organisation ownership from the
authenticated principal. Cross-candidate and cross-tenant access is rejected;
candidate routes do not enumerate Question Bank or Exam Studio records, and no
admin impersonation path exists. Assignment management is organisation-scoped
and protected by `exam_assignment.manage/read` permissions.

Typed response validation is bounded and binding-scoped. Client operation IDs
are unique per attempt, replay returns the existing result, conflicting reuse
is deterministic, and stale revisions are rejected by optimistic concurrency.
The IndexedDB outbox is bounded to 100 operations, 64 KiB per payload and
24-hour queued retention. It stores no authentication token. The offline timer
is not authoritative; the server deadline remains the source of truth.

Candidate surfaces are `/candidate` and `/candidate/exams/[attemptId]`; API
surfaces are under `/api/v1/candidate` plus organisation assignment routes.
The local runtime screen now has a stable accessible timer presentation,
milestone-only announcements, explicit labels for all four supported response
modes, prompt/error associations, textual connection and autosave states,
keyboard-reachable conflict recovery, and disabled controls after expiry or
submission. The timer remains presentation-only and waits for the
server-provided deadline; it does not become a client authority.

The completed local flow is dashboard → safe pre-start → explicit server
start → candidate-safe runtime → typed autosave → server-authoritative
navigation → offline outbox replay → submit/read-only state. Refresh/resume
fetches runtime state and persisted responses without starting a new attempt.
The frontend does not call Exam Studio or Question Bank admin endpoints.

Audit lifecycle events implemented are `exam_assignment.created`,
`exam_assignment.cancelled`, `exam_attempt.created`, `exam_attempt.started`,
`exam_attempt.expired` and `exam_attempt.submitted`. Response content and
answer text are not written to immutable audit; no retention policy or
proctoring telemetry has been invented.

## Validation evidence

- API local suite: **84 passed, 10 skipped, 0 failed**.
- Skip classification: **8 expected local PostgreSQL skips** and **2 expected
  local Redis skips**; no unexpected skip.
- Candidate Runtime PostgreSQL integration: configured in CI with
  `PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1` and `POSTGRES_INTEGRATION_URL`; it
  fails rather than skips when the required URL is absent, and does not use
  `Base.metadata.create_all`.
- Alembic heads: `0014_candidate_runtime`; offline SQL generation passes.
- API Ruff, format check and mypy: **PASS**.
- AI service Ruff, format check, mypy and tests: **PASS** (7 tests).
- Frontend lint, format check, typecheck, tests and production build: **PASS**.
- Frontend accessibility tests cover timer labelling/non-flooding announcements,
  response labelling, textual connection/sync states, conflict and expiry
  messaging, and the rendered candidate flow.
- Candidate API flow and runtime-contract tests cover assignment preparation,
  explicit start, safe runtime loading, operation-ID save, navigation,
  submission, normalized unsupported/ownership errors, conflict and expiry
  states.
- Existing Phase 5 gates: **PASS locally** where infrastructure is available.
- npm and Python dependency audits: **NOT AVAILABLE** in this environment
  (npm registry DNS failure; `pip_audit` unavailable).
- Remote GitHub CI, remote PostgreSQL execution and required-skip behaviour:
  **NOT VERIFIED** from this environment.

## Closure assessment

Implementation-level governance and local accessibility gates are satisfied,
but the overall technical DoD is **FAIL / not yet certifiable** until remote CI
observes the Candidate Runtime PostgreSQL job. The branch was pushed; merge and
deployment were not performed.

Blocking issues:

1. Remote GitHub CI and Candidate Runtime PostgreSQL integration have not been
   observed.
2. Local PostgreSQL/Redis are unavailable, leaving the ten expected skips.
3. The runtime screen remains an MVP shell pending live API wiring; remote CI
   and end-to-end browser verification remain separate gates.
