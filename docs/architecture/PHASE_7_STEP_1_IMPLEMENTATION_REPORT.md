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
provenance. The MVP proposal path requires `HumanReviewRequest`; AI cannot
approve or release. Human corrections preserve proposed values and remain
within rubric bounds. Review checksums block stale approvals. Audit records
assessment creation, proposal, review request, approval and future correction
events without candidate response text.

## Scoring/result integration

Phase 6 `PENDING_ASSESSMENT` records are untouched. Approved subjective
assessment versions are included in a subsequent ScoringRun configuration and
provide approved Decimal points for matching constructed items. This changes
the configuration checksum, creates a new run/result version, and preserves the
historical incomplete run/result. Existing RulesEngine, review, release, and
official-certification guards remain authoritative.

## Evaluation, CI and limitations

Synthetic golden fixtures report schema validity, criterion coverage, evidence
validity, mean absolute score distance, and an explicit non-equivalence claim.
Adversarial candidate prompt text is treated as untrusted content. Tests cover
rubric validation, executable-policy rejection, Decimal aggregation, checksum
sensitivity, and bounded structure. PostgreSQL CI forces
`PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1`, runs the writing schema integration,
and compares the database revision with the repository Alembic head. No
candidate writing, provider rubric dump, credential, or paid model API is
included. Speaking/audio assessment is reserved for Phase 7 / Step 2.
