# P1B governed privacy cases and discovery

The workbench at `/privacy` uses the authenticated organisation context. It lists only cases covered by the caller's current privacy grants. An ordinary institution or platform role carries no privacy capability. The platform Control Centre links to the workbench, which shows an access-denied or unconfigured state when a current grant is unavailable.

## Operating boundary

This implementation is enabled only for the owned disposable test database. `PRIVACY_TEST_DISCOVERY_ENABLED` defaults off. The server checks its configured and connected database identity using the existing disposable guard and requires a `TEST_ONLY` grant with a currently active holder membership, active candidate subject membership, exact responsible organisation and purpose, independent issuer receipt and expiry. Production cannot use the test path. The browser fixture and required integration runner create synthetic grants only. No application endpoint grants privacy authority.

The controller or processor relationship must be assessed for the individual purpose and institution. The case scope can record an assessed context and opaque reference. The workbench always reports operational legal authorization as unverified. Intake is an authorized staff action for an exact bound subject. An independent verifier records the test-only identity and scope receipt. An arbitrary identifier, email address or ordinary membership cannot prove scope.

## API and evidence

Routes are under `/api/v1/privacy/organisations/{organisation_id}/cases`. List and detail require `privacy.case.read`. Intake requires `privacy.case.intake`, independent verification requires `privacy.subject.verify`, policy drafts and hold reviews require `privacy.legal.review`, and preview requires `privacy.discovery.preview`. Each authorization is separately checked against the exact current grant. Case intake accepts erasure and restriction requests. All other rights stay with the designated manual procedure.

The received case and the verified scope are immutable versions. Policy drafts are immutable, versioned by organisation, purpose and category and never become approved. Hold reviews attach bounded windows and opaque decision/basis references to an exact case scope. A reported hold is a review input. Open attempts, unfinished assessment/review, unpublished results, appeals and corrections are separately classified operational dependencies. An expired, absent or unresolved hold cannot confer clearance.

The preview uses a repeatable-read PostgreSQL transaction. It joins assignments, attempts, typed objective answers and Writing revisions, recording sessions and assets, transcription versions and jobs, Writing/Speaking jobs and AI references, human assessment/review, scoring/result versions, releases/statements, and appeals/corrections. It exposes bounded counts and relationship classes only. Limits are 20 cases per keyset page, 100 records per scanned relation and four exact object HEAD probes. An exhausted bound, inconsistent ownership or missing evidence marks scope incomplete. Object identity is derived from matching staged/final records and exact owner/attempt identity. HEAD distinguishes known, missing, inaccessible, mismatched and unavailable metadata. It does not read the object body or assert a byte-level checksum verified by HEAD.

The initial method does not discover subject identity files, certificates, audit history, held-out evaluation or feedback records. Redis, browser data, temporary files, logs/traces, backups and external provider copies report the inventory or operator-verification boundary with no count. No unsupported or uninspected copy is labeled absent.

The assessment returns `NOT_DETERMINED` and legal review required. A preview writes only an immutable receipt and append-only audit evidence. It does not write assessment evidence, call a provider or change object storage. A receipt pins exact case/scope, method, requested categories, current draft policy checksums, hold state, source fingerprint and evaluation timestamp. Replay requires current authorization and the same observed source and policy evidence. A stale receipt returns a conflict. Request validation and audit events do not echo raw evidence, source keys, private reviewer text or receipt documents.

## Acceptance

The dedicated Python job step allocates a fresh guarded PostgreSQL database and private MinIO bucket, applies the dynamic Alembic head and requires zero skipped cases. It verifies real audio and transcription provenance, exact Writing and proposal references, review, scoring and release, and denial and stale paths. The integrated Playwright runner adds one authenticated journey on the private synthetic fixture and preserves the existing suite.

Open production decisions remain separate. The responsible organisation must approve its lawful bases, authority, policy and hold workflow, and confirm retention and processor obligations under the existing [P1A review contract](retention-readiness.md). For a real request, route the case to the controller's designated manual procedure. The discovery limitations do not decide that request.
