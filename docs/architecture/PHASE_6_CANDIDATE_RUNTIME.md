# Phase 6 / Step 1 — Candidate Exam Runtime Foundation

## Purpose and boundary

Candidate Runtime lets an authenticated organisation member take one exact,
published ExamVersion. It does not score, grade, certify, proctor, record audio,
run adaptive testing, or issue results. PostgreSQL is the business truth for
assignments, attempts, sessions, timing, responses and submission.

## Domain model

`CandidateExamAssignment` authorises one candidate and one exact
`exam_version_id`. `CandidateExamAttempt` is the immutable attempt identity and
history. `CandidateExamSession` is the live location/heartbeat companion.
`CandidateResponse` stores current work and `CandidateResponseRevision` stores
append-oriented response history. No runtime record resolves an Exam or item to
"latest".

```text
Immutable ExamVersion -> delivery-safe RuntimePlan -> Assignment -> Attempt
                                      |                  |
                                      |                  +-> Session (clock/deadline/navigation)
                                      +-> safe items     +-> Responses -> Revisions
Browser -> IndexedDB bounded outbox -- idempotent sync -> PostgreSQL truth
Client clock, browser close, and Redis are not authority.
```

## Runtime plan and delivery security

The plan is built from the exact ExamVersion hierarchy and the existing
Question Bank `delivery_projection`. Its deterministic checksum is pinned on
the attempt. Administrative manifests are never used. Answer keys, solutions,
scoring keys, reviewer notes, AI critique, validation, legal notes and private
object URLs are absent. Media remains an opaque reference; protected media
resolution is a later boundary.

Supported response modes are `SELECT_ONE`, `SELECT_MULTIPLE`, `TEXT` and
`LONG_TEXT`. `AUDIO`, `LIVE_ORAL`, `MATCH` and `ORDER` fail safely with
`RUNTIME.UNSUPPORTED_RESPONSE_MODE` until implemented.

## Assignment, ownership and lifecycle

Institution-authorised users create or cancel assignments only within their
active organisation. Candidates can list and read only their own assignments
and attempts. Published/active, non-certification ExamVersions with valid
delivery items are required. Availability windows use server time.

Attempt statuses are `CREATED`, `READY`, `IN_PROGRESS`, `SUBMITTED`, `EXPIRED`,
`CANCELLED` and `INVALIDATED`. Attempt numbers are unique per assignment,
maximum attempts are checked under a PostgreSQL row lock, and a partial unique
index permits only one `READY`/`IN_PROGRESS` attempt per assignment.

## Timing and navigation

Start is server-authoritative. The server records `started_at` and calculates
`deadline_at` from the pinned runtime plan timing snapshot; client timestamps
are informational. Browser close and network loss do not pause the clock, and
resume does not reset it. Every consequential operation calls `expire_if_due`;
overdue attempts become `EXPIRED` and writes are rejected. A sweep operation is
available for background expiry.

Navigation is persisted in the session and validated against the plan. Linear
and no-backtrack policies reject backward movement; arbitrary or unknown binding
IDs are rejected.

## Responses, autosave and recovery

Pydantic request payloads are typed by response mode and validated against the
safe option projection without evaluating correctness. Autosave requires a
UUID client operation ID and `expected_revision`. A replay with the same
payload returns the existing response; reuse with a different payload returns
`RUNTIME.IDEMPOTENCY_KEY_REUSED`. A stale expected revision is a conflict, so
multi-device writes never silently overwrite newer work. Submitted and expired
attempts are immutable. Submission is server-timestamped and idempotent.

The Candidate App exposes assignment/runtime status, server-derived timing,
autosave state and accessible controls. A bounded IndexedDB outbox queues only
response operations during a temporary interruption; it stores no auth tokens
and is reconciled idempotently on reconnect. Conflicts, expiry and submission
are surfaced rather than hidden.

### Local accessibility and runtime UX closure

The runtime screen uses native headings, labels, `fieldset`/`legend`, radio and
checkbox controls, inputs and textareas. Each response control is associated
with the question prompt and contextual help. Validation and conflict/expiry
messages use explicit descriptions and alert semantics without exposing raw
backend errors. Autosave distinguishes `Saving`, `Saved`, `Offline — queued`,
`Conflict`, `Expired` and `Submitted`; routine saves are not live-announced.

The timer is a stable labelled `time` presentation. It does not announce every
tick or move focus. If the server supplies a deadline, only meaningful
milestones (10, 5 and 1 minute) are announced politely. The server remains the
authority for start, deadline, expiry and resume; the local timer never changes
those semantics. Offline, conflict and connectivity state are rendered as
text, not colour or icon alone. Expired and submitted states disable editing.

Core controls are native and keyboard reachable. Autosave, heartbeat and timer
updates never reset focus; only a future consequential transition may move
focus deliberately. This is MVP runtime accessibility closure, not formal WCAG
certification. Focused Vitest coverage checks timer labelling, non-flooding
announcements, response labelling, textual states, conflict and expiry copy,
and the rendered candidate flow. Full browser/API integration and external
accessibility audit remain future gates.

## Authorization, audit, telemetry and privacy

Candidate endpoints derive ownership from the authenticated principal. Tenant
scope is checked for every assignment and attempt. Institution assignment
actions use `exam_assignment.read/manage`; candidate self-access is ownership
based. Consequential lifecycle events are recorded through `AuditService`, but
autosave payloads and candidate text are not audit metadata. Operational
telemetry may record IDs, counts, latency, conflicts, queue size and time
remaining buckets without response text. No additional profile or health data
is introduced. Formal assessment-record retention/deletion policy remains a
future governance decision.

## Migration and validation

Migration `0014_candidate_runtime` adds five focused tables, foreign keys,
attempt/response/revision uniqueness, timing/window checks and the active-attempt
index. It is Alembic-backed; no `Base.metadata.create_all()` is used by the
PostgreSQL integration test. CI upgrades the head and explicitly forces the
Candidate Runtime PostgreSQL test with `POSTGRES_INTEGRATION_URL` and the
required-integration flag. Local runs skip that test only when the integration
service is unavailable.

## Known limitations and next step

There is no scoring, result calculation, audio capture, proctoring, adaptive
selection, official certification delivery, accommodations, media URL resolver,
or formal retention policy. Phase 6 / Step 2 may add governed scoring and result
calculation while preserving the immutable attempt and answer-key boundary.
