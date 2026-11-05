# Evaluation Phase 2: human reference governance

This extends the existing `EvaluationDataset`, `EvaluationDatasetVersion`,
`EvaluationRun`, `EvaluationCaseResult` and `EvaluationReview` registry. It does
not create a second golden-dataset registry, invoke a provider, score candidates,
change approved assessments or activate model/prompt/rubric/pack versions.

## Source and examiner decisions

`EvaluationReference` pins source identity, legal and processing review
references, evaluation-only purpose, language/skill, exact published rubric and
pack, normalization, retention, content checksum and declared lineage. Source
records are immutable apart from their independent approval transition.
`ORIGINAL_SYNTHETIC` sources always remain `SYNTHETIC_TEST`. Approved genuine
source eligibility is `APPROVED_REFERENCE`, **not** an expert label.

Candidate sources need a current institutional evidence permission as well as
platform onboarding authority. A platform administrator without institution
membership cannot read private candidate evidence. Every source referencing an
actual response revision needs a processing approval reference, including
synthetic test attempts. Writing resolves byte-exact submitted text and its
revision/checksum. Audio resolves the original finalized private asset, immutable
primary-response binding, validated checksum, duration and complete media decode,
then reads bounded original bytes from durable S3 storage. A transcript is not a
substitute for original Speaking audio. A decoded tone is not language evidence.
Genuine audio additionally requires independently reviewed language-content
provenance; the software cannot certify that attestation is linguistically true.

Document references reuse `KnowledgeDocumentVersion` and
`KnowledgeLegalMetadata`, including exact checksum, approved legal review and
explicit permitted derivative/evaluation use. Unknown rights fail closed.
Tenant-private knowledge documents without a provable ownership relation are
rejected. External examination content and candidate evidence are not imported
merely because a caller supplies an opaque approval string: an independent
human source approval records responsibility for checking the referenced rights
and processing records. Those records must exist in the operator's approved
governance process; this application does not independently authenticate a legal
licence or an examiner's external credential.

`EvaluationExaminerQualification` captures an exact examiner, organisation,
kind/language, rubric/pack version, credential reference, expiry and independent
credential review. The creator and examiner cannot approve that qualification.
Technical actors/credentials cannot be promoted into genuine expert evidence.
Current user status, roles, institution access, expiry and exact qualification
scope are checked again before each new decision/comparison. The foundation has
no separate credential-revocation registry; disabling the identity or grant, or
expiry, blocks future use. An immutable revocation-event workflow is a future
bounded enhancement.

`EvaluationExpertJudgment` supplies the acting identity from authentication,
never from a submitted examiner ID. The protocol requires two distinct approved
examiners. The source author cannot judge its own reference. Each decision pins
source and qualification hashes, exact rubric, complete criterion scores,
performance levels, evidence, review operation identity and server timestamp.
Scores are finite and bounded. Quotes must match Unicode code-point offsets;
audio spans must match the original asset/revision/SHA and validated duration.

Reviewers cannot edit or delete submitted judgments. Each examiner sees only
its own decision. An independently qualified adjudicator may inspect both exact
originals after the two submissions are complete; neither examiner nor the
source author can adjudicate. Two matching labels are not automatically
consensus. `EvaluationAdjudication` stores exact original pins, private rationale
and immutable outcome. A successor names the exact previous adjudication;
originals remain intact. No model or agent performs adjudication.

Approved references are derived server-side into existing dataset versions,
with exact source/judgment/adjudication pins. They start DRAFT and reuse the
Phase 1 independent submit/approve/publish lifecycle. Final reference approval also excludes the source author, both examiners and the adjudicator, and rechecks current institutional access and qualification eligibility. Genuine labels require
genuine approved source and qualified human decisions; synthetic judgments
remain `SYNTHETIC_TEST` throughout. Optional replay bindings name an existing
exact execution job. A replay still validates the original receipt and source;
selecting a job ID grants no execution or assessment authority.

## Splits and privacy

Splits are `TRAIN` (development), `CALIBRATION`, `VALIDATION` and `HELD_OUT`.
Phase 1 `TEST` is treated as validation for cross-registry duplicate checks.
Content hashes detect exact audio and whitespace/NFC-normalized text duplicates;
declared lineage hashes detect known overlap. Database triggers serialize
reference split claims and immutable decisions. The normal synthetic import
cannot copy protected reference evidence into another split. Held-out dataset
case export is denied. A dataset/run split is chosen before execution.

This is exact/declared-lineage protection, not semantic near-duplicate detection
or a guarantee that an operator disclosed all unknown external lineage.
Management endpoints are controlled governance workflows, not a prompt-authoring
or training-data export. The separate private examiner workbench is required to
inspect authorized evidence. General Control Centre projections never return
text, transcript, audio bytes, private rationale, object key/URL or credentials.

## API and UI

Management prefix: `/api/v1/platform/evaluation`.

- `POST /references`: controlled immutable source onboarding.
- `POST /references/{id}/approve`: independent rights/processing source review.
- `POST /examiner-qualifications` and
  `POST /examiner-qualifications/{id}/approve`: independent credential governance.
- `GET /references/{id}/workbench`: current authorized source, actual rubric,
  eligible qualifications, blinded judgments and server-derived allowed actions.
- `POST /references/{id}/judgments`: immutable authenticated examiner decision.
- `POST /references/{id}/adjudications`: independent exact-pin outcome/successor.
- `POST /references/{id}/dataset-versions`: derive a version; no direct expert
  class import or automatic publication.
- `POST /calibrations`: exact stored human, adjudication or receipt-backed
  proposal comparison; callers cannot supply arbitrary scores.

The read-only summary is
`/api/v1/platform/control-center/evaluation/references`. Existing run metadata,
metrics and review routes show calibration results. Safe comparison details
include exact side versions/hashes, aggregate disagreement/coverage,
limitations, uncertainty and `UNCONFIGURED` thresholds; individual private
judgments and rationales remain in the separate authorized workbench.

UI: `/control-center/evaluation` and the private
`/control-center/evaluation/references` workbench. The minimal interactive
judgment form supports Writing. Speaking/transcription/retrieval evidence
validation and judgments are supported by the backend; this workbench does not
provide full audio playback or dedicated forms for those reference kinds.
Reference onboarding and credential approval are management APIs, rather than
a general platform upload or expert-accreditation wizard.

## Measurements and honest limits

See [the calibration method](evaluation-calibration-method.md). The existing
Phase 1 calculator supplies WER/CER, evidence/authority/bounds gates and missing
coverage. The calibration method supplies criterion agreement, signed error,
MAE, disagreement distributions and ordinal kappa only where its explicit scale,
fixed independent pair and sample prerequisites permit it. Unavailable metrics
are null with reasons. Empirical thresholds remain `UNCONFIGURED`; sample
adequacy, confidence intervals, CEFR/assessment validity and fairness are not
established by these technical fixtures.

Stored human comparisons bind the exact source decisions. AI comparisons accept
only completed receipt-backed executions revalidated through existing replay
services, not arbitrary scores or synthetic-observation replay. Test adapters
remain `TEST_ONLY` and all resulting reference evidence remains `SYNTHETIC_TEST`.

The current published assessment policy pins a single execution profile into a
rubric. It does not provide a separate evaluation-only execution lane for two
new model/prompt bundles on the same immutable candidate/rubric cohort. The
comparison contract supports exact A/B arithmetic and rejects incompatible
receipts. No positive real-provider A/B quality study is claimed. Adding such
an execution lane or a separately approved cross-rubric protocol is future work;
mutating published policy to manufacture an A/B fixture is prohibited.

This first publication workflow derives one fully bound source case per dataset
version. The pure calculator supports bounded larger populations, but persisted
multi-source publication fails closed until explicit per-case source/decision
bindings are available. A one-case example cannot establish inter-rater
reliability; appropriate kappa arithmetic is separately tested with clearly
synthetic multi-case examples.

**EXPERT REFERENCE CALIBRATION: NOT VERIFIED** until genuine approved human
references are actually onboarded and an approved study is performed. No genuine
examiner judgments are included in seeds, browser fixtures or automated tests.

## Disposable PostgreSQL and acceptance

Never point this acceptance workflow at a default development database. In
particular, leave the previously upgraded incident database untouched.

Every new acceptance path uses an explicit `DATABASE_URL`, an identical
`POSTGRES_INTEGRATION_URL` where applicable and exact
`PRUFAI_DISPOSABLE_DATABASE`. The guard prints only host, port and database name,
rejects unknown/default targets and query-string overrides, then checks
`current_database()` before migration. Local Phase 2 databases must be newly
created `prufai_eval2_<purpose>_<32-hex-UUID>` databases on the isolated
`127.0.0.1:55438` server. On Linux, the GitHub-hosted service uses its separately identified
ephemeral database. No default Settings URL is used as a test fallback.

Run `python scripts/migrate-evaluation-disposable.py` from the repository root
with those explicit variables. It requires a single dynamically discovered
Alembic head and verifies the resulting revision. It never resets, downgrades or
creates application tables through `Base.metadata.create_all()`.

The existing `scripts/run-integrated-browser.sh` applies the same guard before
migration/seed. `scripts/seed-evaluation-browser.py` creates only five named
synthetic technical actors and reviewed synthetic qualifications, publishes a
separate original synthetic rubric through the real governance service and
preloads no examiner judgments. The browser journey performs the independent
submissions and adjudication itself. Its manifest contains only IDs/checksums.
Use the documented pinned private MinIO and `prufai-media:ffmpeg-6.1.1` Docker
decoder; native decoder absence is not a reason to mock media validation.

The required CI PostgreSQL/MinIO step executes the new module explicitly and
rejects zero tests or any skipped required integration. The general API run may
skip optional integration modules when services are absent; that is not real
integration evidence. Those same modules must run in the dedicated service
steps. Real browser acceptance uses the production build and actual authenticated
requests, with no mocked success responses.

Python 3.11+ is required. A local Pydantic using `TypeForm` with mypy 1.20 requires
`--enable-incomplete-feature TypeForm`; that enables the annotation feature,
without suppressing errors or changing dependencies. CI uses its installed
supported toolchain and must pass its actual workflow checks.
