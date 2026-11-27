# O2B.1 operational readiness

`GET /health` is process liveness and makes no dependency calls. `GET /ready`
returns only `{"status":"ready"}` with 200 or `{"status":"unavailable"}` with 503,
with caching disabled. PostgreSQL and the configured Redis authentication
limiter are critical. The explicit local/test memory limiter requires no Redis.
Storage and provider execution have separate capability states.

The PostgreSQL probe uses a dedicated nonpooled read-only connection with a
two-second connect limit, a 750 ms statement limit and one authenticated
`SELECT 1`. Redis checks authenticated Lua execution and the login key namespace
without modifying a key. It does not assert permission for every limiter
operation. The normal limiter still enforces its policy and fails closed on
Redis errors. Matching concurrent probes coalesce, with at most one active
probe per dependency per process and a three-second caller wait per dependency.
SQL programming errors are distinct from connectivity failures. Login
connectivity failures return HTTP 503 with `AUTH_DATABASE_UNAVAILABLE`.
The browser retains entered values for an explicit retry.

System status requires platform authority. The detailed read-only view shows
measured dependencies, guarded storage and persisted execution counts.
`LEASE_EXPIRED` is determined from persisted deadlines and the measurement
time. No job or lease establishes current worker-process health. The separate
queue query uses a statement timeout and omits individual rows and payloads.
Observations are process-local and reset on restart. Repeated snapshots can
observe the same lease or terminal failure.

Storage probes are opt-in. Point the existing S3 endpoint at the verified
guarded ingress, then set `OPERATIONS_STORAGE_GUARD_PROBE_ENABLED=true`.
The MinIO readiness response verifies that the configured endpoint serves
readiness. It does not independently verify topology or object integrity.
For a separately provisioned private synthetic object, set
`OPERATIONS_STORAGE_CANARY_ENABLED=true`. The fixed private key and content
are defined by `CANARY_KEY` and `CANARY_BYTES` in the readiness module. The
probe performs one bounded authenticated read through the same endpoint and
checks the exact length and SHA-256. The probe never provisions or lists
objects. Without a canary, object capability remains unverified. Production
provider approval is a separate governance decision.

## Operator signals

Routing and paging thresholds require approval. **ALERT_DELIVERY =
NOT_CONFIGURED.** Logs carry fixed operation and failure categories and counts.
Persisted governance audit remains a separate source of evidence.

| Signal | Evidence and urgency | Proposed responder |
| --- | --- | --- |
| PostgreSQL or Redis unavailable | Readiness 503 and dependency failure category. Authentication and protected operations may be unavailable. | Platform operations |
| Guard failure or unguarded exposure | Guard readiness/canary or a separate authorized containment probe. Treat exposure as a security incident. | Storage operations and security |
| Storage integrity mismatch | Canary digest/length or bounded original-evidence validation. Suspend affected evidence operations pending verification. | Storage operations and assessment owner |
| Stale lease or retry exhaustion | Authorized persisted queue snapshot. Inspect the worker and preserve lease fencing and job history. | AI operations |
| Result-release failure or suspected confidentiality incident | Fixed release failure signal and restricted audit. Verify access with a separately authorized identity. Escalate suspected disclosure immediately under the approved incident process. | Assessment, security and privacy owners |

See [fault recovery runbooks](deployment-reliability-o2a.md) and the
[deferred pre-production review checklist](pre-production-review-checklist.md).

### Sign-in support handoff (D-02A / U13)

Candidate sign-in messages distinguish incorrect credentials (HTTP 401) from
temporary service unavailability (HTTP 503, other 5xx, or a failed network
request). Both `/anmelden` and `/login` advise an explicit later retry and,
for a persistent outage, contact with the candidate's institution. This is a
handoff instruction, not confirmation that a support channel is approved,
staffed or able to respond. No automatic authentication retry is introduced.

Before investigating a report, verify the exact environment and the operator's
assigned authority. A candidate report alone authorizes no infrastructure access
or restart. Keep the report private: retain only necessary time/timezone, route,
fixed failure category and a safely redacted correlation reference, if available.
Do not request passwords, cookies, tokens, response content, candidate records
or unredacted screenshots/logs. Do not infer an account exists from a denial.

| Report or observation                          | Next step for an authorized operator                                                                                                                                             | Boundary / escalation                                                                                                                                                      |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Incorrect-credentials guidance                 | Ask the user to check their own entries; use the existing invitation/recovery flow where appropriate.                                                                            | Never disclose account existence, request credentials, or bypass recovery controls.                                                                                        |
| Temporary-unavailability guidance              | Verify the approved target and compare `/health` process liveness with `/ready` dependency readiness. A liveness 200 alone does not establish recovery.                          | A generic user message does not identify which dependency failed.                                                                                                          |
| Readiness 503 or verified auth/database outage | Follow the existing **Database or auth dependency outage** runbook in [O2A](deployment-reliability-o2a.md#runbooks); inspect only dependencies within the assigned scope.        | Escalate through the proposed platform/identity operations role only after its actual appointment and within approved authority. No candidate-visible production commands. |
| Healthy probes but persistent sign-in failure  | Preserve a minimal redacted report and use the same authorized platform/identity handoff. Confirm recovery through an approved synthetic sign-in, not a candidate's credentials. | Do not retry on the user's behalf, claim a session was created, or treat probes as user acceptance.                                                                        |

Actual paging/routing, responder appointments, production operational acceptance
and H10 human comprehension/accessibility evidence remain **OPEN**. The existing
`ALERT_DELIVERY = NOT_CONFIGURED` baseline is not changed by this copy correction.
E03A remains **BLOCKED**; production readiness **NO**; pilot launch
**NOT AUTHORIZED**. This is a synthetic technical correction awaiting independent
acceptance, not a human support or UAT disposition.

## Disposable acceptance

The hosted browser job supplies pinned pgvector, Redis and guarded MinIO.
The PostgreSQL outage journey uses the full ephemeral container ID from GitHub
Actions and checks its image, configured database and published port before
stopping it. Local use of the integrated runner requires the existing disposable
database guard, `REDIS_URL`, and the full container ID in
`PRUFAI_E2E_POSTGRES_CONTAINER_ID`. Its dedicated PostgreSQL container must be
labelled `org.prufai.browser-disposable=<database name>`. Use the same pinned
image as CI and an isolated Docker environment. The journey restarts the database
in its cleanup path. The supply-chain rehearsal independently tests disposable
PostgreSQL, Redis and guarded MinIO failure and recovery.
