# Phase 6 / Step 1 implementation report — Candidate Exam Runtime

## Executive Summary

Implemented the authenticated Candidate Exam Runtime foundation. Assignments,
attempts, sessions, responses and revisions are PostgreSQL-backed and pinned to
one exact published ExamVersion. Runtime content is derived only from the safe
delivery projection; no correctness or scoring logic is present.

## Schema, assignments, attempts and sessions

Migration `0014_candidate_runtime` adds `candidate_exam_assignments`,
`candidate_exam_attempts`, `candidate_exam_sessions`, `candidate_responses` and
`candidate_response_revisions`. Assignment ownership, organisation scope,
availability and attempt limits are constrained. Attempt numbers and response
bindings are unique; response operation IDs are unique per attempt; a partial
PostgreSQL index protects one active attempt.

Assignment -> exact ExamVersion -> runtime plan checksum -> attempt is the only
launch path. New versions require new assignments. Statuses are controlled and
expiry is derived from server time, not status alone.

## Runtime plan, delivery and timing

`CandidateRuntimeService._safe_plan` materialises safe module/section/task/item
structure from the pinned ExamVersion, checks active rights and supported
response modes, and stores a deterministic checksum on the attempt. The admin
manifest is not called by candidate runtime. Overall/module/section timing is
snapshotted from existing Standard Pack metadata where available. Start,
deadline, expiry, heartbeat and resume are server-authoritative.

## Responses and lifecycle

Typed `SELECT_ONE`, `SELECT_MULTIPLE`, `TEXT` and `LONG_TEXT` payloads are
validated against safe option data with bounded text. Unsupported audio/oral,
matching and ordering modes block launch. Autosave is idempotent by client
operation ID and uses optimistic revision checks. Conflicting reuse and stale
devices are rejected. Submission freezes responses; duplicate submission returns
the existing state. A sweep hook supports background expiry, while every write
also enforces request-time expiry.

## Ownership, RBAC and isolation

Institution users with `exam_assignment.manage/read` create, list and cancel
assignments only inside their organisation. Candidate endpoints derive the
authenticated user and organisation context and never trust a candidate ID from
the request. Cross-candidate and cross-tenant access is rejected. Candidate
enumeration of Exam Studio or Question Bank records is not exposed.

## Offline, media, audit and telemetry

The Candidate App includes accessible status/timer controls and a bounded
IndexedDB response outbox. It never stores auth tokens in localStorage. Opaque
media references remain safe; private object URLs are not introduced. Audit
events cover assignment/attempt lifecycle only; response payloads and answer
text are not written to immutable audit. Operational telemetry boundaries are
documented without adding sensitive candidate profile data.

## Tests and integration

Unit tests cover typed validation, safe binding lookup, expiry, ownership,
controlled statuses and runtime table registration. Existing Exam Studio tests
remain green. A migration-backed PostgreSQL integration checks all five runtime
tables, uniqueness constraints and the dynamic Alembic head. CI explicitly
runs it with PostgreSQL/pgvector and fails if the required integration URL is
missing; general test runs classify absent infrastructure as a skip.

## Known limitations and recommendation

Scoring, result calculation, certificates, proctoring, audio/video, adaptive
testing, accommodations, protected media resolution and formal retention are
out of scope. Phase 6 / Step 2 should add governed scoring/result services
without changing attempt immutability, timing authority or safe delivery.
