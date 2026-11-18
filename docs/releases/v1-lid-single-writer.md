# LiD durable single-writer safeguard — review handoff

This draft addresses the P1 cross-partition submission finding after PR #74.
It does not grant independent acceptance, close H10 or authorize a pilot.
PR #60 P2 and E02A-S1 remain OPEN; E03A remains BLOCKED. Production readiness
is NO and pilot launch is NOT AUTHORIZED.

## Policy and boundary

Browser-local locks cannot detect unsent answers on another device. An eligible
LiD practice attempt therefore has one server-authorized writer at a time.
Each account/organisation/attempt has a random browser-storage marker. The
server persists only its SHA-256 digest, a monotonically increasing generation
and the claim time. Another authenticated partition can read its own attempt,
but cannot start, save, navigate or submit without owning that generation.

All four mutation paths and writer claims acquire the same PostgreSQL attempt
row lock before checking authority. A claim or transfer and an in-flight save
or submission are ordered by the database transaction, not notification timing.
An older process session refreshes cached ORM authority under that lock. A
missing marker, wrong marker or stale generation fails closed. Candidate and
tenant authorization still precede writer policy; a writer marker is not a
substitute for authentication. Generic non-LiD runtime behavior is unchanged.

Initial READY claims can be automatic. Taking an existing writer, or recovering
an unclaimed IN_PROGRESS attempt after upgrade, requires explicit confirmation
and an exact expected-generation comparison. The German confirmation explains
that unsent answers elsewhere are unavailable here, directs the candidate to
check them first, and makes clear that the earlier browser becomes read-only.
Cancel receives initial dialog focus. Concurrent takeovers against one generation
cannot both win. Repeating the same successful claim after a lost reply does not
advance the generation or create another audit receipt.

There is no inactivity lease and no automatic timeout takeover. The persisted
writer survives an API restart. Loss of browser storage needs the same explicit
recovery, not a silent new claim. Expired or submitted attempts cannot transfer
or resume editing. Candidate-owned read and already-submitted idempotent status
responses retain their existing contract; they do not expose private outcomes.

## Queues, uncertain submission and upgrade

The account-scoped IndexedDB schema advances from version 3 to 4, adding only a
writer-identity store. Existing queued answer operations and submission fences
remain intact. Version 2/3 clients cannot open the upgraded database. Upgrading
a browser without a writer marker does not auto-replay an IN_PROGRESS attempt.

Original answer operation IDs, payloads, binding IDs and expected revisions are
preserved. After an explicit takeover, local queued work replays under the new
writer generation and still meets the normal immutable revision/conflict checks.
No unresolved operation is silently removed to enable submission. Same-partition
Web Locks and the durable outbox/submission barrier continue to apply.

New submission fences include their writer generation. After an authenticated
IN_PROGRESS response at a confirmed higher owned generation, the browser may
remove an older known-generation fence under its attempt lock and a shared-store
transaction, only when no queued operations remain. Every old in-flight request
is then server-fenced. Same-generation, unknown legacy, newer and uncertain
fences remain conservative. PR #74 definite-rejection matching-fence recovery
continues independently of this higher-generation recovery.

## Deployment and recovery limits

Migration `0045_lid_writer_fencing` is additive after `0044_v1_worker_exclusion`.
No earlier migration is rewritten. CI obtains and applies the current single
Alembic head dynamically. Only disposable databases are migrated in acceptance.
The existing encrypted restore rehearsal compares persisted database state; the
additive diagnostic rollback schema allowlist includes this migration without
weakening legacy worker denial. That pinned diagnostic API is not authorized
as a writable LiD runtime.

All writer-capable API instances must run this version before enabling the new
client. Mixed old/new writable API deployments and rollback to an older writable
runtime are unsupported: stop candidate writes before rollback. Restoring an old
backup requires invalidating active candidate sessions and verifying the restored
writer state before reopening; this draft does not claim a generation can never
be reused across an arbitrary historical database restore. No production restore
or rolling-upgrade safety is inferred from disposable tests.

The marker identifies a cooperating browser storage partition, not a physical
device or hardware key. Deliberately copying both authenticated access and the
private marker reproduces that writer. This policy prevents accidental stale
partition finalization; it does not attest physical devices or reconcile unsent
remote edits automatically. A candidate who explicitly takes over acknowledges
those limits. Human comprehension and screen-reader testing remain NOT RUN.

## Required automated and independent acceptance

The PostgreSQL suite uses Alembic, separate transactions, an observed row-lock
wait, simultaneous takeovers, cached-session rejection, ownership denial,
immutable answer revisions, sanitized audit receipts and expiry. Its dedicated
required JUnit report must contain executed tests with zero skips.

Browser coverage uses independent contexts with copied synthetic authentication
and separate IndexedDB stores. It verifies stale partition submission denial,
held/failed writes, explicit takeover in both directions, original-ID retry to
revision 2, API restart, stale held submission rejection, higher-generation fence
recovery and version-3 queue preservation. Existing same-partition cross-tab,
definite-rejection, German copy, credential-log and redirect regressions remain
required. Full Python, Web, Browser and Supply-chain results must be verified on
the final PR SHA; local output is separate from hosted evidence.

An authorized independent reviewer must examine this policy, migration and exact
code/CI revision and record findings and disposition before P1 closure. H10
sessions must additionally test the takeover warning, cancel/focus behavior,
keyboard-only use, actual NVDA/VoiceOver, physical devices and German comprehension.
No automated run is recorded as a human session or security-owner approval.
