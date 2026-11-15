# V1 Berlin LiD incident rehearsal

Alert delivery is `NOT_CONFIGURED`. Operator ownership is `NOT_APPROVED`.
Worker process health is `NOT_VERIFIED`. The authenticated Control Centre
System page reports live measurements, bounded process-local failure counts
and the V1 execution exclusions. Read the isolated rehearsal report for
test-only delivery receipts and acknowledgements.

## Run the isolated exercise

Use the clean checkout, disposable Docker context and exact-image command in
the [synthetic preflight runbook](../releases/v1-e06a/runbook.md). The runner
creates and checks ownership of the test services before injecting faults.
The output `v1-e06a-rehearsal.json` records the source revision, timestamps and
correlations in `operational_incidents`. Match the creation, delivery
attempts, receiver confirmation, explicit `TEST_ONLY` acknowledgement and
recovery for each correlation. If delivery, acknowledgement or resolution
fails, the rehearsal fails and retains the sanitized incident in
`v1-o04a-alert-failure.json`. The receiver and its credential are
discarded at the end of the run.

Run the commands below only with the isolated API URL and Docker context the
runner supplies. Where a command names a container, use the runner-owned
container and confirm its rehearsal label using `docker inspect` first.
A failed readiness dependency can prevent authentication to Control Centre.
System shows endpoint reachability, the guard's read-only challenge and private
canary results separately. A healthy canary alone leaves the guard unverified.

Running the API with a Berlin LiD V1 profile reserves its PostgreSQL database
for the pilot and persists the worker exclusion before serving requests.
A restart with a different process profile preserves the exclusion. Restore
the policy with the database. Keep earlier worker binaries away from this
database because they predate the persisted policy.

| Signal | Severity and immediate response | Isolated diagnostic and recovery |
| --- | --- | --- |
| `postgresql.readiness` | High. Pause writes and result release because the service cannot verify authoritative state. | Run `curl -i "$API/health"` and `curl -i "$API/ready"`. Check `docker exec "$PG" pg_isready`. Restart only the owned database with `docker start "$PG"`. Recheck readiness and a protected request. |
| `redis.readiness` | High. Keep authentication fail-closed while rate-limit state is unavailable. | Check `/ready` and `docker exec "$REDIS" redis-cli ping` using the disposable service settings. Start the owned container with `docker start "$REDIS"`. Verify readiness and recovery of rate-limited login. |
| `storage.guard`, `storage.private_operation`, `storage.integrity` | High. Pause private object operations until the guard and byte integrity are verified. | Compare guard and private canary states in System. Check `docker exec "$GUARD" cat /proc/net/tcp /proc/net/tcp6` and the guard bindings. Start the owned guard container with `docker start "$GUARD"`. Reverify direct-backend denial and the private canary. Keep the guarded route in use. |
| `backup.integrity`, `restore.reconciliation` | High. Stop restoration or cutover if authentication, schema or reconciliation fails. | Use the encrypted workflow in the [restore runbook](../releases/v1-e06a/runbook.md). Check the rejection and unchanged-target evidence. Restore a verified archive to a distinct, empty target and compare rows and private objects before considering a restart. |
| `worker.activation`, `provider.execution` | High. Treat any unexpected activation as a V1 scope incident. | Compare denied requests and persisted queues with the rehearsal report. Use its real `python -m app.<worker>.worker --once` checks only under the documented disposable V1 settings. Stop any affected owned worker and preserve claim history for review. |
| `result_release.operation` or unexpected candidate visibility | High. Stop release on an unverified transition. Treat any cross-tenant or revoked-result disclosure as a security incident. | Verify the governed release state and repeat the candidate pre-release, post-release and post-revocation checks in the rehearsal. Preserve the authorization trail. Resume only after the responsible assessment and security owners clear the incident. |

Keep private records, credentials, object locations, candidate material and
raw service diagnostics out of public incident evidence. Before rehearsing
on a hosted target, obtain approval for the target and designate responsible
platform, security, privacy and assessment owners. Production alert routing,
acknowledgement, escalation, RTO and RPO require separate approval and
evidence. The two MinIO findings, CVE-2026-40344 and CVE-2026-41145, are
`PRESENT_CONTAINED` within the verified guarded topology. The exercise
provides no production storage assurance.
