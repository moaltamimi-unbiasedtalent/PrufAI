# Phase 7 / Step 1 implementation report — Governed writing assessment

## Executive Summary

Added governed German writing assessment foundations without changing historical
Phase 6 objective scoring. Rubric versions are exact, evidence-bound and
immutable. Subjective assessment versions pin submitted response revisions and
AI provenance, produce structured criterion proposals, require human review,
and can feed only a new deterministic ScoringRun after approval.

## Schema

Migration `0016_writing_assessment` adds `assessment_rubrics`, `rubric_versions`,
`rubric_evidence_bindings`, `subjective_assessments`,
`subjective_assessment_versions`, and `subjective_criterion_assessments`.
All consequential points use PostgreSQL `NUMERIC` and Python `Decimal`.

## Rubric model and validation

Rubric identity is separate from immutable version content. Criteria, levels,
descriptors, weights, aggregation, evidence, AI policy and human policy are
checksummed. Structural validation rejects duplicate codes, missing levels,
invalid bounds, unsupported schema/taxonomy, missing required evidence and
executable configuration. Aggregation is declarative (`SUM`, `WEIGHTED_SUM`,
`DECLARED_MAPPING`). Exact evidence references bind approved knowledge document
versions and optional page/chunk locations.

## Subjective assessment

Each assessment version pins attempt, ExamVersion item binding, QuestionBank
item version, CandidateResponseRevision and payload checksum, RubricVersion,
StandardPackVersion, and AI workflow/profile/prompt/agent/run references.
Writing is limited to `TEXT`/`LONG_TEXT` and `LANGUAGE`/`WRITING`; integration
knowledge/LiD and oral media are rejected. Candidate text is untrusted input.
Structured proposal validation requires complete criterion coverage, explicit
levels, bounded Decimal points, evidence references, confidence, and
uncertainty. Disagreement is preserved rather than averaged away.

## Workflow and governance

The implementation reuses existing AI orchestration identifiers and its
allow-listed tool vocabulary, adding `RUBRIC_READ` to the writing assessment
provenance. Bounded stages are `prepare_input`, `evidence_analysis`,
`criterion_evaluation`, `consistency_review`, `deterministic_validation`,
`review_routing`, and `human_review_request`. Independent proposals retain
their spread and are never majority-voted. The MVP proposal path requires
`HumanReviewRequest`; AI cannot approve or release. Human corrections create a
new assessment version, preserving AI proposals and storing human values
separately. Review checksums block stale approvals. Audit records lifecycle
transitions without candidate response text.

## Scoring/result integration

Phase 6 `PENDING_ASSESSMENT` records are untouched. Approved subjective
assessment versions are included in a subsequent ScoringRun configuration and
provide approved Decimal points for matching constructed items. This changes
the configuration checksum, creates a new run/result version, and preserves the
historical incomplete run/result. Existing RulesEngine, review, release, and
official-certification guards remain authoritative.

## Evaluation, CI and limitations

Synthetic engineering fixtures report schema validity, criterion coverage,
evidence validity, exact and within-one-level agreement, review-trigger
coverage, adversarial-policy pass rate, mean absolute score distance, and an
explicit non-equivalence claim.
Adversarial candidate prompt text is treated as untrusted content. Tests cover
rubric validation, executable-policy rejection, Decimal aggregation, checksum
sensitivity, and bounded structure. PostgreSQL CI forces
`PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1`, runs the writing schema integration,
and compares the database revision with the repository Alembic head. No
candidate writing, provider rubric dump, credential, or paid model API is
included. Institution reviewer routes are `/institution/writing-reviews` and
`/institution/writing-reviews/[assessmentId]`; the queue omits full writing,
the workspace distinguishes `AI PROPOSAL` from `HUMAN APPROVED VALUE`, and
candidate content is rendered as inert text. Speaking/audio assessment is
reserved for Phase 7 / Step 2.

The local safety suite exercises bounded structured-output retries, invalid
output rejection, failure-category provenance helpers, and an organisation-
scoped reviewer queue API. Candidate-owned internal assessment access remains
separate from institution reviewer capability. The remote PostgreSQL gate is
still pending.

The invalid-output proof has a named 24-case completeness inventory spanning
schema, criterion, numeric, evidence, consistency, authority, and aggregate
failures. Case 16 is explicitly not applicable because evidence objects do not
carry response-version identity. Expected-valid and expected-rejected fixtures
are evaluated separately; malformed outputs cannot lower the valid-schema
gate. As-of resolution remains intentionally deferred for Step 1.
