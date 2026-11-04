# Governed human-only recorded Speaking foundation

Integrated MVP Step 2B.2 ends with an independently approved, immutable
`SubjectiveAssessmentVersion` for genuine submitted private audio. It reuses the
generic rubric, assessment, criterion, review and durable operation tables.
There is no new migration. Resolve the migration head dynamically with Alembic.

The policy must explicitly declare `SPEAKING`, `HUMAN_ONLY` and `AUDIO`. A task
configured for AI assistance or transcript evidence does not fall back to this
workflow. No transcript, model inference, AI proposal, AI confidence or AI worker
is supplied by this feature.

## Authority and evidence

Rubric publication follows `DRAFT → HUMAN_REVIEW → APPROVED → PUBLISHED`, with
different requesting and approving humans. Its version pins the Standard Pack,
Speaking task, language, level, criterion bounds, allowed performance levels and
audio evidence policy. Published content and its checksum are immutable.

Institution assessment actions require actual organisation membership and the
existing `subjective.read`, `subjective.assess` or `subjective.review` permission.
A candidate, a foreign organisation, a supplied identifier, or platform authority
alone cannot grant institution assessment access. The generic AI review endpoint
cannot decide a domain-owned human Speaking review.

Initialization resolves audio on the server from the exact submitted response
revision and exam-item binding. It checks candidate, organisation, attempt,
question version, response checksum, attached READY asset, original-byte SHA256,
complete-input validation, verified duration, recording-policy checksum, and the
published rubric pinned by that question. Browser-selected arbitrary audio asset
identifiers are not an initialization authority.

Reviewer A supplies every criterion once: bounded finite points, an allowed
performance level, private rationale, and exact audio evidence references with
millisecond spans. The server checks `0 <= start < end <= verified duration` and
the pinned asset/SHA. The spans describe the verified decoded media timeline;
they do not manufacture transcript quotes. Reviewer B independently decides the
checksum-bound proposal. The criterion author cannot approve their own work.

Durable operation receipts bind actor, organisation, action, target and input
checksum. Identical authorized replays are idempotent; changed-input replays and
stale checksums conflict. PostgreSQL row locks protect competing operations.
Approved criteria, nested rationale/evidence, pins and human provenance remain
immutable. Reassessment creates a successor requiring fresh criterion submission
and independent approval; it never rewrites the approved historical evidence.

## Product and privacy

Use the existing rubric governance pages at `/institution/rubrics` and the
Speaking queue/detail at `/institution/speaking-reviews` and
`/institution/speaking-reviews/{assessmentId}`. The authorized private player uses
the application API and a revocable Blob URL. No public object-storage URL is
needed. Existing full-stream and bounded backend HTTP Range behavior remains
covered by the recorded-audio acceptance suites.

The Control Centre Audit view at `/control-center/audit` remains read-only.
Its repository selects explicit metadata columns and an operation UUID; it does
not serialize domain payloads, private rationale, criterion evidence, speech,
object keys, tokens or transcript text. Platform inspection does not authorize
playback or marking.

All institution API paths have prefix
`/api/v1/organisations/{organisation_id}`:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/speaking-review-options` | Exact eligible submitted revisions and published rubric choices |
| POST | `/subjective-assessments/human-speaking` | Initialize the exact HUMAN/AUDIO assessment |
| GET | `/speaking-reviews/{assessment_id}` | Current assessment with pinned audio/rubric/review metadata |
| POST | `/speaking-reviews/{version_id}/human-proposal` | Submit complete human criteria and audio spans |
| POST | `/speaking-reviews/{version_id}/human-approve` | Independent approval of the exact proposal |
| POST | `/speaking-reviews/{version_id}/human-decision` | Reject or request changes with a reason |
| POST | `/speaking-reviews/{version_id}/human-successor` | Initialize a reasoned, checksum-bound reassessment |

The existing rubric publication API remains shared with Writing. Historical
assessment versions are inspected by exact version identity; history does not
silently resolve a newer response or rubric.

## Original development walkthrough

The existing development-only demo seed adds **Human-only B1 German Speaking
Practice — SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL**, version
`human-speaking-b1-v1`, and `speaking.candidate@demo.prufai.local`. The original
LANGUAGE/de/B1/PRACTICE task asks for an invented personal introduction, a hobby
and a reason. Its `TASK` and `LANGUAGE` criteria each permit 0–3 raw practice
points and original `NOT_YET`, `DEVELOPING` and `CLEAR` descriptors. These are
internal practice descriptors, not official provider thresholds.

The fixture starts with a DRAFT rubric. It creates no recording, transcript,
assessment, AI run or approved outcome. It uses a separate scenario namespace
and preserves the existing published recorded-audio fixtures. The seed is
non-destructive; use a fresh dedicated database for a repeat acceptance run.

1. **Institution Admin:** inspect the Speaking rubric and request review.
2. **Independent reviewer:** approve and publish the exact rubric version.
3. **Institution Admin:** assign the original Speaking practice exam to the
   Speaking practice candidate.
4. **Candidate:** record real audio in the runtime, finalize, attach and submit
   it. The server performs complete-input original media validation.
5. **Reviewer A:** open Speaking reviews, initialize the exact submitted option,
   play its private audio and enter all criterion judgments with valid spans.
   Submit the proposal for independent review. Self-approval is unavailable and
   also denied on the server.
6. **Reviewer B:** inspect the same recording, rubric, values and spans, then
   approve, reject or request changes. An approved version remains in history.
7. **Platform Admin:** inspect safe rubric/assessment audit references in the
   Control Centre. Candidate speech and private rationale remain private.

## Foundation boundary and result integration

The generic schema can represent HUMAN Speaking: exact response/rubric pins,
asset and SHA, approved criterion values, aggregate score and actor provenance.
This step validates that approved evidence contract independently of results.

Step 2B.2 stopped at that approved assessment. At that boundary the generic
scorer dispatched HUMAN evidence through Writing-specific validation, so it
could not consume Speaking. It also lacked explicit authoritative-successor
selection and the governed human Speaking correction adapter.

[Integrated Speaking results, Step 2B.3](integrated-speaking-results.md) connects
the existing scorer and result/release services using skill-specific validation,
explicit pending/rejected/approved successor rules and exact correction lineage.
The generic Writing correction entry point remains unavailable for HUMAN
Speaking: a correction must obtain a new independent Speaking decision through
the actual successor workflow. The foundation itself does not infer AI
execution, issue official certification or automatically release a result.

## Verification scope

Required acceptance uses fresh Alembic-migrated PostgreSQL and real private
MinIO/S3-compatible storage. Stored audio must pass the merged complete-input
decoder contract before binding/submission. Review playback retrieves that real
object through authenticated application routes. Required integration flags must
turn unavailable service configuration into failures; required job reports must
contain executed tests and zero skipped tests. Concurrency evidence must use
independent connections and actual domain services.

With the private object-storage endpoint, bucket and development credentials in
the environment, run from `services/api`:

```sh
PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 \
PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION=1 \
python3 -m pytest tests/test_human_speaking_postgresql_minio_integration.py -v
```

`POSTGRES_INTEGRATION_URL` must identify an isolated PostgreSQL/pgvector test
service whose user can create disposable test databases. Each test creates and
Alembic-migrates its own unique database and removes only that database during
cleanup. It does not reset the supplied database or any developer database.
Media validation requires the repository's pinned complete-input media runtime.

Browser acceptance uses the repository's production-build runner,
`scripts/run-integrated-browser.sh`, against a fresh dedicated PostgreSQL database
and a real private storage endpoint. The original objective, Writing and recorded
audio journeys remain part of the same suite. Successful assessment or review
responses must not be mocked. Local results are separate from exact-head remote
CI evidence; neither constitutes automatic merge authorization.

The isolated browser API child has a bounded refresh allowance of 120 requests
per existing window because the combined journeys share one loopback client IP.
The normal product default remains 30, and login/reset limits, authentication
checks and API security-test settings are unchanged. This avoids treating the
combined browser suite's repeated legitimate account switches as an attack.
