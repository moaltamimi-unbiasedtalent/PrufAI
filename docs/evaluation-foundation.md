# Governed Evaluation Foundation, Phase 1

This foundation records versioned datasets, their independent human provenance
review, exact evaluation configurations, measured case results and a separate
human review of the results. It does not establish German-language assessment
validity, CEFR calibration, examiner equivalence, fairness or production readiness.
It cannot approve candidate assessments, change scores, release results, issue
certificates or activate models, prompts, rubrics or empirical deployment gates.

## Existing code and evidence boundaries

- `services/api/evaluation/golden_v1.json` contains 12 legacy synthetic retrieval
  placeholders. Their expected-ID lists are empty; the file supplies no expert
  labels, governed approval history or meaningful linguistic-quality denominator.
- `app/evaluation/retrieval.py` supplies actual recall, precision, reciprocal-rank
  and nDCG arithmetic. These functions are reused. The legacy ranking runner's
  previously constant successful safety values are now unavailable (`null`), and
  its safety gate is `NOT_AVAILABLE`. Ranking arithmetic alone cannot establish
  citation validity, source authorization or historical-version accuracy.
- `app/evaluation/ragas.py` exports **RAGAS-compatible JSON only**. Neither that
  serializer nor this evaluation method executes RAGAS or a model-based judge.
- `app/writing_assessment/evaluation.py` remains a legacy synthetic engineering
  harness. Its aggregate-score comparison, list-shape evidence check, self-reported
  `policy_safe` flag and omission of missing predictions from some denominators
  do not make it an authoritative criterion-level or empirical evaluator. The new
  governed method does not use those shortcuts.
- Existing Writing and Speaking execution validators remain authoritative for
  their provider output and governed handoff. Pinned-execution replay reuses those
  boundaries. The evaluation schema-compliance metric separately checks the
  normalized observation contract and states that limitation.

## Original technical fixtures

[`foundation_v1.json`](../services/api/evaluation/foundation_v1.json) is an array
of four **DatasetVersionInput payloads**, one for transcription, Writing, Speaking
and retrieval. Every payload contains an original positive observation and a
deliberately invalid observation. All examples are:

**SYNTHETIC TEST DATA — NOT OFFICIAL EXAM MATERIAL.**

They declare `ORIGINAL_SYNTHETIC`, `OWNED_ORIGINAL`, `SYNTHETIC_EXPECTATION`,
`NOT_EXPERT_REVIEWED` and `SYNTHETIC_TEST`. Their `LANGUAGE / de / B1` metadata
defines the intended fixture scope, not a validated B1 judgment. No expert label,
human consensus, provider response or existing candidate execution is fabricated.

The transcription and Speaking entries are **identity/arithmetic fixtures, not
recorded German speech**. The all-zero audio SHA and the declared 1,000ms timeline
are conspicuous placeholder identities for structural checks. There is no audio
file, CandidateMediaAsset, measured recording duration or verified media hash
behind them. `TECHNICAL_TONE` explicitly prevents treating these fixtures as
linguistic or acoustic evidence. The all-one SHA is a deliberately mismatched
negative observation. Do not upload these identity placeholders as genuine
audio evidence, infer recognition accuracy from their text, or describe their
span arithmetic as pronunciation/fluency validation.

The retrieval example is an original synthetic in-file source catalog. Its
checksum is the actual SHA-256 of its UTF-8 source text. Its source/version labels
identify this fixture only; they do not claim a KnowledgeDocumentVersion,
retrieval-provider execution, official citation or external source authority.

The complete two-case payloads are expected to produce measured hard-fail runs,
because each includes a negative case. A successful measurement process and a
failed technical quality gate are compatible. Do not remove failed results after
execution. To demonstrate a positive-only run, create and independently approve
a separate version containing the intended positive case **before** execution.

Nothing automatically imports, seeds, submits, approves or publishes these files.

## Dataset identity, lifecycle and human authority

The platform API requires authenticated platform context without an institution
header. Permissions are reloaded from current database assignments; a claimed
permission in a client body is not authority.

| Operation | Required platform permissions |
| --- | --- |
| Create/version/submit/publish/run | `standards.evaluation.run`, `ai.manage` |
| Read safe metadata/results/comparisons | `standards.evaluation.read` |
| Approve a dataset or review a run | `standards.evaluation.read`, `ai.manage`, `ai.review` |
| Read eligible original-synthetic cases | `standards.evaluation.read`, `ai.review`, plus applicable institution authority |

Dataset versions follow `DRAFT → REVIEW_REQUIRED → APPROVED → PUBLISHED`.
Approval requires an actual authorized human distinct from the version creator,
the exact expected checksum and a genuine provenance-review reference. A
reference string is not proof that legal review occurred. The independent human
must inspect origin, ownership, permitted purpose, label provenance and retention.
An AI worker does not receive approval authority through evaluation execution.

Dataset identity, version content and completed evaluation history are immutable.
Create a new version to change cases, labels or scope; do not rewrite published
history. SQL guards and ORM checks protect new evaluation tables, including nested
JSON content and append-after-completion attempts. Audit events contain safe
identities/checksums/counts, not raw responses, audio or private review references.

The initial API accepts original synthetic fixtures only. Typed single-expert and
adjudicated reference contracts exist, but **expert/private-candidate dataset
import is not configured**. A caller cannot promote the dataset's evidence class
or add expert judgments by changing a label. Real candidate data requires a
separately approved data-use, rights, retention and access workflow.
`INTEGRATION_KNOWLEDGE`, including LiD, cannot carry CEFR metadata.

## Exact run configuration and bounded execution

A request pins a published dataset version/checksum, an exact Standard Pack
version/checksum, the registered split, evaluation method/version and the complete
metric configuration/checksum. Applicable rubric and question/item versions need
their exact checksums and compatible scope. Supplied rubric criteria/bounds must
match the reference contract. Claimed source identities must agree with these
pins. Technical-only arithmetic may explicitly mark model, rubric or item
references not applicable; this is not an assessment against an authoritative
rubric. Pinned execution requires the actual applicable registry/source pins.

`TRAIN`, `CALIBRATION` or `TEST` is selected before case execution. Reuse of the
same normalized evidence text, audio SHA or execution ID across splits is rejected.
This is exact-identity leakage detection, not semantic duplicate detection or
proof of population independence.

The runner accepts at most 100 bounded cases, a bounded JSON payload and a bounded
transcription edit-distance work budget. It is one synchronous deterministic
runner, not a distributed AI execution framework. An operation UUID identifies
the exact request: retrying that request is idempotent; changing its body or actor
under the same UUID is a conflict. Completed per-case and aggregate measurements
have immutable checksums and failed-case references.

Two execution adapters are supported:

- `SYNTHETIC_OBSERVATION_REPLAY_V1` measures the explicitly supplied synthetic
  observations. It executes **no model**, creates no AIRun and must remain
  visibly `TEST_ONLY` with technical/synthetic evidence classification.
- `PINNED_EXECUTION_REPLAY_V1` reads an exact already-completed execution job.
  It revalidates the original source, configuration, validated output and
  persisted handoff. It does not invoke a provider or create an assessment.
  Current institution membership and the existing evidence permission are
  required in addition to platform evaluation authority. Dataset organisation,
  source identities and exact registry bundle must match the job.

Pinned Writing/Speaking may record `REAL_PROVIDER` execution only when the
existing validated job/receipt proves it. That does not promote synthetic
reference labels to expert or pilot evidence. Current transcription history has
no adequate persisted external request receipt for this classification; genuine
ASR replay is rejected as unavailable rather than invented. Technical ASR history
can be replayed as `TEST_ONLY`.

Raw execution-backed cases cannot be exported through the synthetic-cases route.
Institution-private synthetic cases additionally require current institution
evidence authority. Control Centre endpoints return summaries only.

## Measured values and limitations

Metrics carry a value or explicit unavailable state, numerator, denominator,
sample size, missing count, evidence class and limitations. Unknown reference
labels never become zero error or perfect agreement. Invalid numeric booleans,
NaN, infinity and out-of-bound points cannot become valid criterion scores.

- WER and CER use pinned NFC/whitespace normalization, optionally case-folding,
  and exact Levenshtein edits divided by reference words/code points. Rates may
  exceed one. Empty references have no valid error-rate denominator. Empty
  observed output and unsupported-language behavior are measured separately.
- Writing/Speaking comparisons report signed criterion differences and MAE for
  available bounded labels. Missing criteria/references remain visible. Human
  judgment ranges are descriptive and never manufacture examiner consensus.
- Writing quotes must match exact text offsets. Speaking spans must match the
  exact audio/asset identity and timeline; transcript-only acoustic proposals
  fail modality checks. Acoustic validity remains unestablished.
- Retrieval reuses real ranking arithmetic. Source existence/version/checksum,
  authorization, tenant scope, citation identity and quote containment are checked
  against the pinned catalog. Exact quote containment is not semantic entailment;
  support-label checks retain the declared provenance. Missing support labels
  remain unavailable. No RAGAS execution is claimed.
- Prompt-injection checks measure observable structured tool/authority actions
  and output integrity in deliberate adversarial cases. They do not claim a
  comprehensive semantic jailbreak assessment or trust `policy_safe` self-report.

Deterministic hard failures block invalid output, missing required criteria,
wrong identities, unauthorized sources, unsupported modalities and broken
provenance. Empirical accuracy/agreement values have descriptive limitations and
missing-data counts; no confidence interval or empirical threshold is invented.
The current threshold setting is `NONE`. Different versions/configurations can be
compared descriptively with cohort, sample-size and changed-pin limitations;
changing a rubric may also change its score scale. No causal improvement claim
follows from a delta.

## Manual API workflow

Use two actual authorized human actors through the repository's existing
authentication flow. Their user identities must be distinct; actual human
independence remains a governance responsibility. Do not create dummy approvals,
seed approval events, paste credentials into registry JSON or commit tokens.
Obtain actual compatible
published Standard Pack pins from the existing registry.

The workflow below describes individual human actions; it is not an automatic
approval script. API prefix: `/api/v1/platform/evaluation`.

1. Choose one complete object from `foundation_v1.json` and POST it to
   `/datasets` as the author. The response contains the real `dataset_id`,
   dataset-version `id` and content `checksum`; status is `DRAFT`.
2. An eligible reviewer can GET `/dataset-versions/{version_id}/cases` to inspect
   the original synthetic case content and provenance. Safe metadata is available
   at GET `/dataset-versions/{version_id}`. Do not substitute invented identities
   or approval references for the returned records.
3. The author POSTs `/dataset-versions/{version_id}/submit` with
   `{"expected_checksum":"<actual returned checksum>"}`.
4. After independent human provenance review, the reviewer POSTs
   `/dataset-versions/{version_id}/approve` with the exact `expected_checksum`
   and their actual `provenance_review_reference`. A dataset author approving
   their own version is rejected.
5. The authorized publisher POSTs `/dataset-versions/{version_id}/publish` with
   the exact expected checksum. No version becomes published from a metric result.
6. POST `/runs` as the authorized author/execution actor from step 1, with a new operation UUID,
   the real published dataset-version ID/checksum and exact configuration:

   ```json
   {
     "operation_id": "<new operation UUID>",
     "dataset_version_id": "<published dataset-version UUID>",
     "dataset_checksum": "<actual dataset checksum>",
     "configuration": {
       "dataset_split": "TEST",
       "standard_pack_version_id": "<compatible published Standard Pack UUID>",
       "standard_pack_checksum": "<actual Standard Pack checksum>",
       "execution_adapter": "SYNTHETIC_OBSERVATION_REPLAY_V1",
       "evaluation_method": "DETERMINISTIC_REFERENCE_COMPARISON",
       "evaluation_method_version": "1",
       "metric_configuration": {
         "schema_version": "1",
         "normalization": "NFC_WHITESPACE",
         "retrieval_k": 5
       }
     }
   }
   ```

   The angle-bracket values require actual returned records; they are not valid
   credentials, UUIDs or checksums. Optional rubric/item references are absent
   here because these fixtures measure technical arithmetic only. Supplying an
   authoritative rubric/item requires compatible real pins and case bindings.
7. Inspect GET `/runs/{run_id}`. The full fixture has an expected `FAIL` technical
   gate from its deliberately invalid case. Review failures and missing-data
   limitations; do not call this a failed model run or a quality certification.
8. The independent authorized reviewer from step 4 POSTs `/runs/{run_id}/reviews` with the
   actual `expected_run_checksum`, an explicit decision
   (`ACCEPTED`, `REJECTED` or `CHANGES_REQUIRED`) and a real `review_reference`.
   The run creator and dataset-version creator cannot review their own work.
   `ACCEPTED` is rejected when a technical hard gate failed. The full negative
   fixture can legitimately receive `CHANGES_REQUIRED` or `REJECTED`.
9. Create a new DRAFT version with POST `/datasets/{dataset_id}/versions` for a
   deliberate corrected or positive-only population, then repeat independent
   governance. Compare actual completed run IDs with GET
   `/runs/compare?left_id={left}&right_id={right}`. Preserve failed history and
   state whether populations/configurations changed.

## Control Centre and review decisions

The read-only Evaluation section at `/control-center/evaluation` shows dataset
versions/status, run status, pinned references, measured values, sample sizes,
missing data, failure categories and review status. Its summary APIs are
`GET /api/v1/platform/control-center/evaluation?kind=datasets|runs` and
`GET /api/v1/platform/control-center/evaluation/runs/{run_id}`. Empty data produces
an honest empty state; it does not seed synthetic approvals or decorate unknown
metrics as successful percentages.

The UI does not display raw candidate-like text/audio, source excerpts, private
expert notes, credentials or private review references. Reviewers inspect safe
summaries and failed-case references. Human review records are append-only and
remain separate from evaluation execution and any future release decision.
An accepted evaluation review acknowledges the reviewed technical evidence; it
does not activate a model, authorize real candidate processing or certify an exam.

**PILOT ASSESSMENT VALIDITY: NOT ESTABLISHED.**
