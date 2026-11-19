# V1: sticky submission-fence provenance

Base: PR #76 merge `514ef712f4c50e6d5ac5ed639317237ec37959bc`.
This is a focused browser persistence correction. It does not close P1 acceptance,
H10, PR #60 P2 or E02A-S1. E03A remains BLOCKED; production readiness NO;
pilot launch NOT AUTHORIZED. The separate legacy-fence P2 remains OPEN pending
independent acceptance of this correction.

## Failure and representation

The old retry writer replaced a legacy boolean fence with a generation-bearing
object. Higher-generation recovery could then treat unknown lineage as known.
The new record keeps submissionId and recoverable, and adds one of:

- `provenance: KNOWN`, `generationCeiling: positive safe integer`.
- `provenance: LEGACY_UNKNOWN`, with no trusted generation bound.

A known record must also have a valid submission UUID and boolean recoverable.
Legacy booleans, untagged older objects (even with writerGeneration), malformed
tags and incomplete records are unknown. A current generation never establishes
an existing unknown fence's origin. Rewrites of unknown records remain unknown.
A proven absent key plus valid writer generation may create a new known fence.
Key existence is checked independently of its value: an existing undefined value
is an uncertain fence, not absence.

Known retries retain the maximum of the previous ceiling and the current request
generation. A later takeover must exceed every possibly outstanding known
request, not merely the first request's generation. Invalid/missing generation
on a rewrite conservatively loses known recovery and stays unknown thereafter.
The recoverable flag is not provenance: matching-submission definite-rejection
recovery remains independent, and a known retry with recoverable=false can still
recover at a strictly higher verified generation.

## Recovery and boundaries

The existing page caller retains the exclusive account/attempt Web Lock, obtains
an authenticated writer claim, verifies matching runtime generation and
IN_PROGRESS state, and checks the active account/context before guarded release.
READY, terminal, failed-verification and lost-context paths do not release.
The outbox helper requires a valid higher safe-integer generation, a complete
known record and an empty queue for this attempt in the same IndexedDB read/write
transaction that removes the fence. Other attempts' work remains untouched.
Account changes or unavailable storage fail closed. Unknown lineage never uses
this higher-generation release path.

This remains coordination for cooperating clients. PostgreSQL writer fencing
continues to enforce server authority across partitions; no backend permission,
writer-generation, response revision or scoring behavior changes here.

## A9: explicit authenticated retry

On 19 November 2026 the operator instructed this change to adopt the A9
clarification preserving explicit authenticated retries. This is the engineering
acceptance contract; it is not independent human acceptance, P2 closure or
release authority. PR #77 reviewed head
`80a4fee64dddbf8a6bb7685235c4caee619cca3a` merged as
`4b4570e31e652b8718e97aee4d29cc8144af3026`. Its separate merge-review exception
remains open in the [pre-production checklist](../operations/pre-production-review-checklist.md).

An unresolved legacy or ambiguous fence stays `LEGACY_UNKNOWN` through retries
and rewrites. A newly stamped generation never proves its original lineage.
Higher-generation ownership alone cannot automatically clear it. Answer editing
and question navigation remain blocked while the fence is unresolved.

A deliberate retry of the same attempt's submission is permitted through the
existing authenticated, writer-authorized path. It retains the exclusive
account/attempt lock, shared-outbox checks, successful server-state verification
and no unresolved queued answers. Cancelling the dialog sends no submission and
does not clear the fence. Failed verification sends no submission or fence
rewrite. A rejected or uncertain retry retains unknown provenance and keeps
editing/navigation blocked. The separate matching-submission definite-rejection
recovery keeps its existing identity and recoverability restrictions; rejecting
a new retry does not prove the original unknown submission was rejected.

A confirmed `SUBMITTED` or `EXPIRED` state is read-only. Submission does not
release a score or statement. An idempotent repeat may return HTTP 200 for the
existing submitted attempt; it must not duplicate finalization or its submission
audit event. This corrects the earlier blanket "submission remains blocked"
wording: an explicit checked retry is distinct from automatic fence release or
resuming edits. No runtime behavior is changed by this clarification.

The browser regression follows legacy true, a failed retry, takeovers to
generations 2 and 3, reload, cancellation, failed verification, another definite
rejection and explicit successful retry. It preserves the saved response revision
and payload. A read-only [disposable evidence probe](../../tests/browser/inspect-lid-submission.py)
checks the exact synthetic attempt's status, submission timestamp and audit IDs
before and after an idempotent repeat; it does not read candidate response text
or change database state. Existing queued-answer, account, ownership, lock,
storage, expiry, known-generation and confidentiality regressions remain required.

## IndexedDB version 5

The account/organisation database name and stores are retained. The version bump
creates missing stores only; it does not clear or rewrite existing operations,
fences or writer identities. Old values remain intact and are interpreted as
unknown by provenance-aware code. Existing connections close on versionchange;
a blocked upgrade fails closed. Version-4 clients receive VersionError after the
upgrade, preventing their retry code from stripping the new marker. The positive
API browser fixture uses version 5; explicit old-reader probes remain version 4. No Alembic
migration or dependency update is part of this correction.

## Required evidence

- Unit matrix: legacy values and ambiguous objects; repeated rewrites; invalid
  generations; maximum known bound; independent definite-rejection recovery;
  account changes, storage failures, queued and unrelated attempt data.
- Real browser legacy chain: true fence, failed retry, explicit generation-2
  takeover, generation-3 takeover, authenticated IN_PROGRESS and empty queue.
  Unknown lineage and the edit/navigation barrier survive reload. Cancellation
  and verification failure send no submission. An explicit authenticated retry
  may submit; an idempotent repeat keeps one persisted submission audit event.
  Rejected retries retain unknown lineage, and terminal reload remains read-only
  with the result withheld pending institution review and release.
- Known-generation browser recovery remains exercised after API restart and a
  denied old in-flight submission, followed by exactly one final submission.
- Version-3 and version-4 browser fixtures preserve the original queued operation,
  payload, expected revision and timestamp, boolean/ambiguous fences and existing
  writer identity. Version-4 reopening fails; original-ID replay yields revision 2.
- Full Web and Browser regressions on disposable PostgreSQL, Redis and guarded
  MinIO; credential-log unsafe control, redirect guards, real required suites,
  zero required skips, one dynamic Alembic head and checksum-linked CI evidence
  on the final source SHA. General API sweep skips remain separately reported.

All evidence is automated synthetic technical execution. It is not human UAT,
independent human approval, production qualification or content authorization.
