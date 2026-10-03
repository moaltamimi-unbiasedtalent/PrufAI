# Phase 7 / Step 1 — Governed writing assessment

## Boundary

This phase assesses only German `LANGUAGE` / `WRITING` constructed responses.
It excludes audio, speaking, proctoring, certificates, appeals, psychometrics,
adaptive testing, and autonomous release.

```text
Submitted ResponseRevision + exact Item/Exam/Pack
                         │
                         ▼
                  RubricVersion
                         │ exact evidence
                         ▼
        prepare_input → evidence_analysis → criterion_evaluation
          → consistency_review → deterministic_validation
          → review_routing → human_review_request
                         │ structured AI provenance
                         ▼
                deterministic Decimal aggregation
                         │
                         ▼
                 checksum-bound human review
                         │
                         ▼
               approved assessment version
                         │
                         ▼
       new Phase 6 ScoringRun / ResultVersion only
```

## Rubrics

`AssessmentRubric` is a stable identity. `RubricVersion` stores normalized
criteria, explicit performance levels, Decimal-compatible points and weights,
declarative aggregation, evidence policy, AI policy, human-review policy,
exact Standard Pack compatibility, and a checksum over substantive content.
Approved/published/active versions are not mutated. Exact approved knowledge
document bindings provide source provenance without copying provider rubric
text.

Validation rejects duplicate criteria, missing descriptors, invalid points or
weights, unsupported aggregation, missing evidence requirements, incompatible
taxonomy, and executable configuration. Allowed aggregation is `SUM`,
`WEIGHTED_SUM`, or explicit `DECLARED_MAPPING`.

## Assessment and AI boundary

`SubjectiveAssessment` is one logical assessment per submitted attempt item.
Each immutable `SubjectiveAssessmentVersion` pins the exact accepted response
revision checksum, item, exam, pack, rubric, AI workflow provenance, proposed
criteria, confidence, disagreement, and checksum. Candidate text is untrusted
content. AI outputs are structured proposals only; the allow-list excludes web,
database shell, and code execution. Approved memory can assist workflow but
cannot override rubric, rules, or Standard Pack policy.

Every proposal requires `HumanReviewRequest`. Human corrections are retained
alongside AI proposals and constrained by criterion levels, points, aggregation,
Rules, and certification guards. No private chain-of-thought is requested or
stored. Independent criterion proposals retain their spread and are never
majority-voted. A model profile must carry the explicit
`WRITING_ASSESSMENT` purpose. Corrections create successor assessment
versions; current, historical, and deterministic diff endpoints are available
to authorised institution reviewers.

## Scoring integration

Phase 6 incomplete scoring remains immutable. After all required writing
assessments are approved, a new ScoringRun includes exact approved assessment
version IDs and Decimal approved points. A new ResultVersion may supersede the
incomplete result, while historical runs and results remain queryable.

## Evaluation and privacy

Synthetic fixtures exercise schema validity, criterion coverage, evidence
validity, disagreement, score bounds, and prompt-injection resistance. Metrics
are engineering signals, not examiner-equivalence claims. Candidate text is
never written to AuditService and candidate APIs expose no internal assessment.

Institution reviewer routes are `/institution/writing-reviews` and
`/institution/writing-reviews/[assessmentId]`. The queue omits full writing;
the workspace distinguishes `AI PROPOSAL` from `HUMAN APPROVED VALUE` and
renders candidate content as inert text.
