# ADR-075 — Result release governance

Status: Accepted for MVP baseline

## Context
Consequential results require controlled release.

## Decision
Incomplete or rule-blocked ResultVersions cannot release. HumanReviewRequest binds the exact result checksum; approval is required before release and official certification remains blocked.

## Consequences
Review is stale-safe and released history is immutable.

## Phase 8 Step 2 corrective contract

`POST /api/v1/organisations/{organisation_id}/result-versions/{version_id}/release`
has one registered handler, in the scoring API. It requires an operation ID and
atomically performs the approved result transition and records the authoritative
release. A legacy Step 1 result already marked RELEASED can be recorded without
rewriting its history. A successor additionally requires `supersedes_release_id`
and the exact current upheld appeal, completed correction, result version,
scoring run, candidate, attempt and Standard Pack version.

Release, statement and certificate actions lock the AssessmentResult aggregate.
Permissions and tenant/provenance checks precede every replay. One statement and
one certificate are allowed per release; the database also prevents two releases
from claiming the same predecessor. A replay cannot issue against a revoked,
superseded, stale or moderated release. Revocation requests pin an operation ID
and reason in the append-only audit record.

Statements and certificates retain their recorded payload, checksum, issuance
time and provenance. Read APIs return both recorded `status` and derived
`effective_status` / `currently_valid`. Revocation or supersession of a parent
invalidates its artifacts without rewriting or deleting those artifacts. A
certificate also requires its exact pinned authorization version to remain
active, in date and in scope. Authorized institution users and the owning
candidate can still inspect historical records.

Certificate issuance is disabled by default through
`CERTIFICATION_ISSUANCE_ENABLED=false`. Enabling it does not authorize official
certification: OFFICIAL_CERTIFICATION remains denied independently in release,
authorization and certificate validity. Synthetic positive tests use an eligible
PRACTICE assessment through runtime, scoring, human approval and release, followed
by explicitly labelled SYNTHETIC TEST AUTHORIZATION. They do not invent a real
provider entitlement. Authorization requires the platform-only
`certification.authorize` capability and explicit organisation context, exact
StandardPackVersion, classification, pack code as provider scope, evidence and
effective dates. Institution administration alone grants no such capability.

The frontend uses the authenticated client and real membership permissions
merged in PR #19. Institution release queue/detail/history and candidate
release/statement routes display backend-derived lifecycle state. No Control
Centre or official certificate template is introduced.

Acceptance includes Alembic-migrated PostgreSQL tests, real service calls and
overlapping independent connections. Concurrency tests observe
`pg_blocking_pids()` before releasing the winning transaction; a sequential
second call is retained as a separate stale-operation check.

## Alternatives considered
Automatic release from raw percentage and mutable reviewer score overrides were rejected.
