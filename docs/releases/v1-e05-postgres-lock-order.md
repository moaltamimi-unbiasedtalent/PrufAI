# V1 E05: attempt/source lock ordering and database diagnostics

This correction starts from PR #75 merge `3dbb5a9f60c5275a342c968449d5644cf1d9b292`.
It does not constitute independent acceptance, pilot authorization or production readiness.

## Observed failure and correction

The post-merge Browser run `44271102819` returned HTTP 409 at the request-review
HTTP 200 assertion. Its retained artifact did not include the response category.
The original hosted cause therefore remains unconfirmed. Independent disposable
execution reproduced a request-review deadlock (PostgreSQL SQLSTATE `40P01`), with
matching assignment/result-version/scoring-run provenance and a completed,
UNDETERMINED result awaiting review. A synchronized two-session probe confirmed
an attempt/source lock cycle.

Writer-status previously validated the form (locking source versions) before
locking the attempt. Review acquired the attempt first and then the sources.
The shared LiD ownership-validation path now verifies candidate/organisation
ownership, then locks and refreshes the attempt before environment, pilot and
form validation. Writer-status, navigation, runtime reads and heartbeats use this
path. The deterministic navigation regression also exposed the same original
inversion; navigation no longer takes its redundant later attempt lock.
Expiry updates occur under that same lock. A validation failure rolls back the
transaction. Source-version validation and its locks are retained.

Save, submit and takeover already acquire the attempt lock before
pilot/form validation. Review acquires the attempt before result/version and
source locks. No automatic transaction retries or weakened review assertions
are introduced.

## Error contract

Runtime and scoring HTTP boundaries classify SQLAlchemy/driver failures
without serializing the exception, SQL text, parameters or driver diagnostics:

| Failure | HTTP | Namespace suffix |
| --- | --- | --- |
| Deadlock, serialization conflict, lock unavailable | 503 | DATABASE_CONFLICT |
| Database query cancellation/timeout | 503 | DATABASE_TIMEOUT |
| Recognized connection/service outage | 503 | TEMPORARILY_UNAVAILABLE |
| Other database, schema or programming failure | 500 | INTERNAL_ERROR |

The detail uses `RUNTIME.` or `SCORING.`. Each database-error response has
`Cache-Control: no-store` and a server-generated `X-Request-ID` matching the
sanitized operational log. Logs contain only the fixed operation/category,
HTTP status and generated request ID. No caller-controlled correlation value,
SQL parameter, exception text or exception traceback is logged by this mapper.
The full database-error detail is intentionally unavailable in public logs;
operators correlate safe receipts without enabling SQL/parameter debug logging.

Business-rule conflicts retain their existing 409 detail and authorization
failures retain 403. Unrecognized scoring application exceptions return a
sanitized 500; they are not falsely classified as database outages.

## Acceptance evidence requirements

The required writer PostgreSQL suite uses explicit events and actual observed
PostgreSQL lock waits to overlap writer polling with review, save, navigation,
submit and takeover. It retains result provenance and the expected pre-approval
release denial. The browser confidentiality journey independently checks
COMPLETED scoring, UNDETERMINED outcome, canonical versions, assignment projection
and unchanged state after the pre-approval denial, before the original
request-review HTTP 200 assertion. Release, revocation and stale-response checks
remain intact.

HTTP negative tests inject synthetic database exceptions and check both response
and operational logs, matching correlation IDs, business conflicts and non-outage
application errors. Hosted acceptance must be verified on the final PR head:
Python, Web, Browser and Supply-chain, all current required integration reports
with zero required skips, one dynamic applied Alembic head, credential-log unsafe
control, redirect denial and artifact/manifest/rehearsal checksums. General API
sweep skips are separate from dedicated required integration execution.

## Separate OPEN findings and restrictions

- **Legacy-fence provenance P2: OPEN, not corrected here.** Retrying a legacy
  boolean fence can stamp a generation onto originally unknown lineage. The
  later correction must preserve sticky KNOWN / LEGACY_UNKNOWN provenance through
  retries, rewrites, IndexedDB upgrades and takeovers. Ambiguous older records
  remain unknown. Proven known-generation recovery additionally requires a
  confirmed higher owned generation, authenticated IN_PROGRESS state, the
  exclusive attempt lock and no queued operations. Older clients must not be
  able to remove unknown provenance. `recoverable` is not a provenance marker.
- P1 acceptance and H10 remain OPEN; no human UAT or human approval is supplied.
- PR #60 P2 and E02A-S1 remain OPEN; E03A remains BLOCKED pending the independent
  disposition and separate formal security-owner closure.
- Existing JWT/cookie defaults and historical review/archive exceptions remain
  separately tracked in the pre-production checklist.
- Production readiness NO; pilot launch NOT AUTHORIZED. No deployment, protected
  environment changes, live providers or content publication is authorized.
