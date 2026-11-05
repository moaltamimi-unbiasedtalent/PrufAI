# Governed calibration comparison method

`services/api/app/evaluation/calibration.py` implements the pure
`governed-calibration-v1` descriptive comparison. The existing Evaluation
Foundation registry remains authoritative. Its governance service must verify
approved source provenance, exact published rubric and pack versions, immutable
submitted judgments and execution receipts before calling this calculator.
The calculator has no database, provider, publication or activation side effect.

## Pinned population and comparison inputs

`compare_criterion_scores` takes an explicit case population, criterion codes and
maximum points, two immutable score sides, exact dataset/rubric version pins,
and the governance-derived reference evidence class. Side identity and checksum
refer to the exact stored version or checksum-bound immutable side collection.
Missing cells and explicit null scores are both missing observations. Duplicate
cells, unknown cases/criteria, non-finite numbers, Boolean scores and out-of-bound
scores are rejected rather than silently included or discarded. The bounded
population is at most 100 cases by 50 criteria.

The required pins are `dataset_version_id`, `dataset_checksum`, `dataset_split`,
`rubric_version_id` and `rubric_checksum`. Pack and metric-configuration pins are
supported. A version A/B comparison additionally requires both exact model and
prompt version identities. A checksum must be a full lowercase SHA-256 value;
the caller verifies it against the stored object. This function cannot verify
the authenticity of caller-supplied identities or expertise.

| Comparison | Left side | Right side | Interpretation |
| --- | --- | --- | --- |
| Human A versus Human B | Human judgment | Human judgment | Descriptive agreement; weighted kappa only for a verified fixed independent pair |
| Adjudicated versus human | Adjudication | Human judgment | Descriptive differences; adjudication derives from the original labels and is dependent |
| AI proposal versus reference | Exact AI proposal execution | Human judgment or adjudication | Descriptive proposal/reference differences; no scoring authority |
| Version A versus version B | Exact AI proposal A | Exact AI proposal B | Paired version differences over the same declared population; no improvement or causal claim |

Compare an adjudication separately against each original examiner; do not average
their labels or reinterpret two judgments as consensus. Select the same exact
cases and rubric scale before comparing. This module does not combine unmatched
cohorts or silently cross rubric versions.

## Criterion measurements

For each criterion, the result preserves the expected case count, complete-pair
sample size, left/right/both missing counts, missing-either count and complete
pair coverage. `missing_left` and `missing_right` each include `missing_both`.
`paired_coverage` is the number of complete pairs divided by the expected case
count. Separate `left_coverage` and `right_coverage` report the corresponding
side's nonmissing label count divided by that same expected count. The service
identifies which side supplies the governed reference in each comparison.
Agreement is the number of exact equal score pairs divided by the complete-pair
sample size. Signed difference is **right minus left**; mean absolute error is
the mean absolute paired score difference. The disagreement distribution reports
each exact signed difference, its absolute magnitude, count and proportion.
Per-case signed and absolute differences permit inspection by an authorized
service. Missing labels are never zero-filled, imputed or counted as agreement.

Calculations use decimal points. Numeric summaries are serialized as finite
numbers, with exact differences retained as decimal strings. Metrics with no
complete pairs are null, with sample size zero and explicit missing counts.
Different criterion scales are never pooled into a misleading overall MAE.

Phase 1 `evaluate_case` / `aggregate_case_results` continue to calculate
transcription WER/CER using the pinned normalization configuration, as well as
proposal bounds, evidence validity and existing descriptive criterion error.
There is no second transcription normalizer or edit-distance implementation.

## Quadratic weighted Cohen kappa

The method follows the chance-corrected weighted agreement introduced by
[Cohen (1968)](https://pubmed.ncbi.nlm.nih.gov/19673146/). The formula and
degenerate-denominator handling were checked against the
[official scikit-learn implementation](https://github.com/scikit-learn/scikit-learn/blob/afe5bcd80ce17fd8ca88c33beb17534bd36423bc/sklearn/metrics/_classification.py).
No scikit-learn dependency is installed or invoked.

For a declared ordered category index, let `O[i,j]` count paired assignments,
`L[i]` and `R[j]` be the two examiner marginals, and `N` the complete-pair count.
Use disagreement weights `w[i,j] = (i-j)^2` and expected counts
`E[i,j] = L[i] * R[j] / N`. Then:

`kappa = 1 - sum(w * O) / sum(w * E)`.

Normalizing every weight by `(K-1)^2` cancels in this ratio. The returned observed
and expected disagreements are the unnormalized weighted counts.

Kappa is available only when the service establishes the **same fixed independent
human examiner pair across cases**, the comparison is human versus human, an
explicit ordered rubric score scale is supplied, at least two complete paired
observations exist, and expected weighted disagreement is positive. The scale
must contain distinct strictly increasing finite score options within the
criterion bounds. Every observed score must exactly equal a declared option.
Continuous scores are never binned. Existing rubric descriptor insertion order
does not establish ordinal categories; the approved source protocol must provide
the scale. The weights assume equal spacing of category indices, irrespective
of numerical point gaps. This is a declared measurement assumption and does not
establish that a rubric or CEFR level has interval properties.

When a prerequisite is absent, kappa is null with an explicit unavailable reason.
Identical constant labels have zero chance disagreement and return null, rather
than an invented value of 1. Adjudication and AI/reference comparisons do not
claim independent human inter-rater reliability. Two observations are only a
computational minimum; they are no assertion of adequate study size.

## Evidence classes, uncertainty and release limits

All automated arithmetic examples and actors remain `SYNTHETIC_TEST`. A
`TEST_ONLY` proposal cannot be compared under an `EXPERT_LABELLED` claim. The
operational caller alone may authorize expert evidence after genuine provenance
and examiner governance checks; choosing a class string in this calculator is
not an expert onboarding mechanism. No computation promotes data to
`PILOT_CALIBRATED` or establishes expert calibration from synthetic labels.

Every result states `quality_claim=NOT_ESTABLISHED`,
`empirical_thresholds=UNCONFIGURED`, `production_activation=false`, uncertainty
`NOT_ESTIMATED`, no confidence interval, and sample sufficiency
`NOT_ESTABLISHED`. Measurements describe the supplied finite cohort only.
Representative sampling, independence/known lineage, examiner competence,
uncertainty estimation, benchmark thresholds, validity and fairness require a
separate approved study. No acceptable CEFR tolerance, pass threshold or release
decision is invented by this foundation.

## Receipt-backed execution acceptance and remaining limits

`test_receipt_backed_ai_reference_and_fixed_policy_ab_fail_closed` in
`services/api/tests/test_expert_references_postgresql_integration.py` exercises
the actual private S3 audio path, a separate Speaking worker using the explicit
`TEST_ONLY` provider, two synthetic examiner submissions, independent
adjudication/publication, and replay against that exact reference dataset. It
checks actual persisted worker receipts and measured proposal/reference MAE.
Both an exact evaluation-run ID and an exact case-result ID are exercised. The
replay and comparison create no additional `AIRun` or human review request.
Missing media/revision bindings, changed prompt pins, incorrect checksums and an
`AIRun` passed as an evaluation result are rejected. This is technical evidence
using generated tones and `SYNTHETIC_TEST` actors, not real provider acceptance,
expert calibration or recognition/scoring quality evidence.

The existing Speaking execution policy pins one exact workflow/profile version
inside the published rubric. Changing the model or prompt changes that profile
version and requires a new governed rubric version under the current execution
workflow. The calibration service preserves its exact cohort/rubric requirement.
Consequently, replaying the same authorized profile twice is rejected as a
version A/B experiment. Pure version-comparison arithmetic and exact version-pin
validation are implemented and tested, but there is no legitimate positive
different-profile execution experiment on the available unchanged-rubric cohort.
A separate evaluation-only execution lane would be future work; published
policies or historical receipts must not be modified to manufacture this proof.
Genuine model-quality comparison remains **NOT VERIFIED**.

Current governed publication produces one source-bound case per dataset version.
The pure calculator supports a larger declared population, but the persisted
service rejects multi-source datasets until they provide exact per-case
source/judgment/adjudication bindings. An operational one-case comparison does
not meet the computational sample requirement for kappa; a synthetic multi-case
arithmetic test demonstrates the formula without implying an expert study.
