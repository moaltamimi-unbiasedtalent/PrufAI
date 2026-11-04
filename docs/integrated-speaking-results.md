# Integrated MVP Step 2B.3: governed recorded Speaking results

This integration connects original private recorded audio, an explicitly
`SPEAKING / HUMAN_ONLY / AUDIO` rubric, independent human criterion approval,
the existing deterministic scorer, versioned results, human result review,
governed release and a candidate Statement of Results. The existing generic
assessment, scoring, correction and release tables are reused. No migration,
transcription execution, model inference, AI proposal or certification authority
is introduced.

## Scoring contract

HUMAN origin does not imply Writing. The scorer dispatches by the actual item
skill and published rubric policy. Writing retains its text-evidence validator;
Speaking uses the existing original-audio validator and a scorer-compatible
wrapper. AI-assisted workflows retain their own proposal/review requirements.

Both human skills must match the exact organisation, candidate, submitted
attempt, exam version, item binding/version, response revision/checksum, pack,
published executable rubric/checksum, approved criteria and independent human
decision. Speaking additionally verifies the attached READY asset, complete-input
validation, original-byte SHA, authoritative duration, recording policy and
integer millisecond spans. Transcript-dependent policies do not fall back to
audio-only human assessment.

The existing Decimal aggregation uses only published criterion bounds, levels
and aggregation policy. The maximum must match the actual scored item. No pass
threshold is invented: without a governed outcome rule, the result remains
`UNDETERMINED`.

Private ItemScore/component provenance and the linked response snapshot retain
the exact approved assessment, rubric, response, audio identity/SHA, validation
and aggregation references. The public result/statement component is an explicit
allowlist: Speaking skill, HUMAN origin, HUMAN_ONLY/AUDIO modes, approved version
references and score/max points. It excludes audio keys/URLs, rationale, evidence
comments/spans, transcript text and invented AI fields.

Existing Writing configuration keys and public dictionary shape are preserved
so unchanged historical Writing evidence keeps its scoring idempotency identity.

## Authoritative version selection

Selection is scoped to the actual submitted item and its assessment aggregate;
it does not search for an arbitrary latest approval.

| Current state | New current scoring behavior |
| --- | --- |
| No assessment | Deterministic `INCOMPLETE`; no complete releasable result |
| Initial CREATED/REVIEW_REQUIRED/IN_REVIEW or unapproved criteria | Deterministic `INCOMPLETE` |
| Unique current approved version with valid pins and lineage | Score that exact version |
| Older approval followed by a pending successor | `CONFLICT`; no fallback or new run/result |
| Older approval followed by a rejected/changes-requested successor | `CONFLICT`; no new approved score is inferred |
| Approved authoritative successor | Score only that exact successor |
| Multiple active approvals, conflicting roots or version numbers | `CONFLICT`; fail closed |
| Historical persisted run/result/statement | Keep its exact persisted references and payload |

The institution's read-only readiness projection uses the same selection and
validation as scoring and performs no scoring or audit write. It reports
`READY`, `INCOMPLETE` or `CONFLICT`, bounded reasons, exact component references
and server-authorized `can_score`. Consequential operations use consistent
attempt → result/version → subjective-version locking.

## Review, release and history

Institution result pages own scoring, human result review, release and statement
issuance. Speaking review hands off to the exact submitted attempt rather than
adding another scoring UI. Actual tenant permissions remain required:
`scoring.run`, `result.review`, `result.release` and read permissions as applicable.
Platform oversight alone does not authorize institution actions.

A first release and a first statement issuance must still match the current
authoritative Speaking evidence. A scored result cannot be newly released after
its assessment becomes unresolved or is replaced. Authorized existing release
replays and candidate history retain their existing historical contracts.

Creating a successor result does not create a successor release. For a result
with an actual governed ResultRelease, scoring preserves the old ResultVersion
status/content until an explicit valid successor release owns supersession.
Legacy results released without a ResultRelease retain their prior lifecycle.
The existing effective-status rule remains: a release can be shown as blocked
or historical when a newer ResultVersion exists. This derived validity does not
rewrite the original release or statement payload.

## Governed Speaking correction

The existing ResultCorrection lifecycle is reused:

1. A candidate challenges the exact released result and an authorized human
   upholds the appeal using the governed evidence bundle.
2. Correction selects the exact challenged ItemScore and approved Speaking
   component/checksum. It cannot choose the first/latest assessment or accept
   direct point overrides.
3. The actual human Speaking successor service creates REVIEW_REQUIRED
   evidence, pinning correction/appeal/result/run/item-score/source identities
   before its checksum and durable receipt are written.
4. Reviewer A enters new criteria and a distinct Reviewer B approves them.
5. The existing correction resume operation revalidates the upheld appeal,
   exact component and lineage, then invokes the actual scorer for S2/R2.
6. R2 is not automatically released. Its separate human result review and exact
   predecessor/correction release checks remain mandatory.

SP1's criterion evidence, S1/R1 checksums and the original release/statement
payload remain historical. A rejected appeal or successor cannot trigger a new
approved score, automatic rescore or release. Correction history is checked from
persisted immutable references; legitimate later lifecycle status changes do not
cause a historical evidence bundle to be silently reinterpreted.

## Original development walkthrough

The development-only seed adds **Human-only B1 German Speaking Results Practice
— SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL**, version
`speaking-results-b1-v1`. It uses an independent scenario namespace and preserves
the original twelve browser journeys. The new rubric starts DRAFT; no recording,
assessment, score, result or release is seeded.

Golden candidate: `speaking.results.candidate@demo.prufai.local`.
Pending negative candidate: `speaking.results.pending@demo.prufai.local`.
Accounts use the existing explicitly development-only demo credential policy.

1. Admin requests rubric review; a distinct authorized human approves it; Admin
   publishes the exact rubric and assigns the original practice exam.
2. Candidate records supported audio, finalizes, attaches and submits it.
3. Reviewer A listens and enters all bounded criteria/spans. Reviewer B
   independently approves the exact version.
4. Admin follows the exact-attempt handoff, checks readiness, scores and inspects
   the actual ScoringRun, ItemScore and ResultVersion.
5. Admin performs result review/approval, explicitly releases the exact result
   and issues its Statement of Results.
6. Candidate sees PRACTICE classification, Speaking/HUMAN origin, score/max,
   issuer, timestamps, exact result/run and statement checksum.
7. Platform Admin inspects safe audit identifiers and checksums, including audio
   identity, result/run, release/statement and correction successor references.
   No platform marking or release bypass is supplied.

## Required acceptance

Use fresh Alembic-migrated PostgreSQL/pgvector and actual private MinIO with the
merged complete-input audio runtime. Required tests must execute with both
integration flags set and zero skips; no `Base.metadata.create_all()` or memory
storage can substitute for required proof. Existing Redis integrations remain.

From `services/api`, with `POSTGRES_INTEGRATION_URL` and the private storage
configuration already set:

```sh
PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 \
PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION=1 \
python3 -m pytest tests/test_speaking_results_postgresql_minio_integration.py -v
```

The PostgreSQL user needs permission to create disposable databases: each case
applies `alembic upgrade head` to its own unique database and cleans up only that
database. No developer database is reset. The existing browser command remains:

```sh
DATABASE_URL=postgresql+psycopg://USER@HOST:PORT/FRESH_BROWSER_DATABASE \
PRUFAI_BROWSER_TOOL_DIR=/absolute/path/to/isolated/browser-tool \
bash scripts/run-integrated-browser.sh
```

The repository browser runner uses a production Next.js build and real API,
PostgreSQL and MinIO. It retains the previous twelve journeys and adds the full
Speaking result/statement journey and an unapproved/unauthorized negative
journey. Synthetic microphone capture and diagnostic human inputs prove the
workflow, not German proficiency or linguistic scoring accuracy. Successful
responses are not mocked.

Correction successor acceptance is a real PostgreSQL/MinIO service chain. A full
correction browser journey is deferred in this bounded integration; that
distinction must remain explicit in the closure report. Exact-head remote
Python, web, browser and required real-service gates are separate from local
test evidence. The PR remains draft and unmerged for human review.
