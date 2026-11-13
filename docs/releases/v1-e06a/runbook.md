# E06A synthetic activation, stop and rollback

## Configuration

| Requested E06A profile | API setting | Technical authorization |
| --- | --- | --- |
| `SYNTHETIC_TEST` | `V1_PILOT_PROFILE=BERLIN_LID_SYNTHETIC` | Disposable guarded rehearsal only. The API additionally requires the exact disposable PostgreSQL database and a valid synthetic Berlin form. |
| `STAGING_PROPOSED` | `V1_PILOT_PROFILE=BERLIN_LID_LOCKED` | NOT_AUTHORIZED. Preflight exits 3 before contacting infrastructure. |
| `REAL_PILOT` | `V1_PILOT_PROFILE=BERLIN_LID_LOCKED` | NOT_AUTHORIZED. Preflight exits 3 before contacting infrastructure. |

The harness explicitly uses Redis for authentication limits, guarded private
S3 for object storage, and the Docker decoder. `AUTH_ENROLLMENT_DELIVERY_MODE`
is `DISABLED`. Certification and all external provider configurations remain
disabled. Secrets are generated per run and kept out of the Web build,
manifest, public logs and uploaded evidence. Storage credentials are scoped
to the newly created synthetic service. Production TLS, IAM, storage
selection and encrypted backup are **NOT VERIFIED**. Alert delivery is
**NOT_CONFIGURED**. A process-health claim requires independent process
evidence. The worker check here exercises real entrypoints under the stop
profile and verifies unchanged persisted jobs.

## Reproduce and verify preflight

1. Verify the release source and CI as described in the [evidence package](README.md).
   Use a clean checkout with no application `.env` files and explicitly select
   an isolated disposable Docker context. Use Python 3.11 or later, the
   repository's API dev and AI dependencies, the pinned build tooling from
   the supply-chain workflow, Node 24, and `npm ci --prefix apps/web`.
2. Build the existing guarded MinIO and decoder recipes
   (`infra/minio-source.Dockerfile` and `infra/media-probe.Dockerfile`) in
   that context or use the scanned images identified by the workflow.
3. Run the following against that disposable context. Locally the rehearsal
   requires loopback PostgreSQL port 55438 and the isolated API port 58400
   to be free. The GitHub job uses port 5432 for its own PostgreSQL.

   ```sh
   PRUFAI_DISPOSABLE_OPERATIONS_TEST=1 PYTHONPATH="$PWD/services/api" \
     python scripts/storage-readiness/v1_release.py /tmp/prufai-e06a-evidence \
       --profile SYNTHETIC_TEST --local-image <guarded-image> \
       --local-decoder <decoder-image>
   ```

4. Inspect the generated preflight report for exact source, content hashes,
   configuration profile, authenticated PostgreSQL and Redis, one applied
   Alembic head, guarded storage with a bounded verified private canary,
   private bucket policy, local credential scope, backup tools, API
   `/health` and `/ready`, Web sign-in and sanitized public probes. The
   preflight reads existing state. Its rehearsal provisions the fixture and
   canary before preflight. `--profile REAL_PILOT` and
   `--profile STAGING_PROPOSED` can be checked with an unused output path and
   return the static authorization report and exit 3.

## Stop and recover

1. A readiness failure, cross-tenant access, score or statement disclosure,
   conflicting release receipt, migration mismatch or object integrity
   mismatch stops the cutover. In the rehearsal the runner owns all processes
   and injects a PostgreSQL outage only after validating the container label.
   `/health` stays 200, `/ready` and a protected request return 503, and
   restarting the same database recovers readiness.
2. Drain requests, send SIGTERM to owned workers, then stop and restart the
   owned API with `V1_PILOT_PROFILE=BERLIN_LID_LOCKED`. The supported API
   command is `python -m uvicorn app.main:app --app-dir services/api --host
   127.0.0.1 --port 58400 --no-access-log` with the existing isolated
   environment supplied by the harness. The synthetic stop diagnostic invokes
   `python -m app.<speech_transcription|writing_execution|speaking_execution>.worker
   --once` one worker at a time with the locked settings. Check assignment
   creation, attempt creation and start, result release and candidate result
   access are denied. Compare attempts, revisions, results and persisted job
   snapshots. Leave the database and storage available to the isolated operator.
3. Stop the owned API before copying authoritative state. Use O2A's
   coordinated `pg_dump -Fc --no-owner --no-privileges` and object manifest.
   Validate archive and object hashes before
   `pg_restore --exit-on-error --no-owner --no-privileges` to an empty,
   distinct database and distinct private MinIO volume. The rehearsal uses
   separate database names on the same disposable PostgreSQL server.
   Reconciliation requires zero missing, orphaned, corrupt and
   metadata-mismatched objects and equal authoritative row digests. Check
   privacy immutability, Standards storage and audit linkage. The snapshot
   preserves the queued TEST_ONLY transcription and the revocation recorded before stop.
4. Redeploy the pinned prior API source with its exact Alembic history and
   preserved synthetic JWT signing configuration using the restored target.
   Check readiness, tenant isolation, withheld result/statement, and replay
   scoring and revocation through the API to verify the same operation and
   rows. The Web build remains in place during this API rollback. This is an
   application/config rollback with tested restore, with no schema downgrade.
   New migration compatibility or a full Web binary rollback requires its own
   rehearsal.
5. If reconciliation differs, maintain the lock and preserve the original
   snapshot, manifests and sanitized logs. Refer to the organization's
   authorized platform, security, privacy and assessment owners when
   appointed. The synthetic archives use owner-only temporary plaintext.
   Retain only the public counts and checksums. Historical object versions,
   independent server loss, encryption, a live cutover, and recovery time or
   point objectives need separate verification.

## Candidate release operator checklist

- [ ] Match the exact source, artifact digests and CI evidence. Confirm one applied
  Alembic head, all authenticated browser journeys and zero required skips.
- [ ] Inspect the stop and restore reports and complete a fresh isolated
  rehearsal if an input or target changes.
- [ ] Obtain independent review for the Draft PR before a manually authorized merge.
- [ ] Before any real launch, close the applicable H01, approved Berlin form,
  source rights, independent review, privacy and retention, approved delivery,
  manual human UAT, operational owner, hosting and storage, security and
  incident-response gates in the [V1 register](../v1-readiness-register.md).
  E03A and historical review exceptions stay in their existing registers.

[O2A recovery commands](../../operations/deployment-reliability-o2a.md) ·
[O2B.1 readiness and signals](../../operations/dependency-readiness-o2b1.md)
