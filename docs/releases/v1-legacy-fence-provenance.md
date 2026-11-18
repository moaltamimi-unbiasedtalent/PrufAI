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
  Unknown lineage and the edit barrier must survive reload; no implicit edits.
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
