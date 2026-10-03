# Phase 6 / Step 2 implementation report — Deterministic scoring and results

## Executive Summary

Implemented the deterministic objective scoring and governed result foundation.
Scoring is restricted to submitted attempts, freezes exact response revisions,
uses Decimal/Numeric points, preserves immutable run/result history, leaves
constructed responses pending, and exposes only released candidate-safe results.
No subjective AI assessment, certificates or provider-specific transformations
were added.

## Schema

Migration `0015_scoring_results` adds `scoring_runs`,
`scoring_response_snapshots`, `item_scores`, `scoring_components`,
`assessment_results` and `result_versions`. Idempotency, attempt/result,
snapshot/run, item-score/run-binding and result-version uniqueness are database
constraints. Revision is 20 characters and follows `0014_candidate_runtime`.

## Scoring and provenance

`ScoringService.score` accepts only `SUBMITTED` attempts. The frozen snapshot
contains exact attempt/ExamVersion/runtime checksum, binding IDs, QuestionBank
item-version IDs, accepted response revision IDs, payload checksums and
submitted timestamp. Canonical JSON yields a response-set SHA-256. The engine
identity is `prufai_objective_scoring@1.0`; configuration and scoring-run
checksums include exact pack/rule/item references.

`SELECT_ONE` is exact option-ID matching. `SELECT_MULTIPLE` is exact set
matching unless an explicit versioned `DECLARED_TABLE` policy exists. No
partial-credit formula is inferred. Decimal arithmetic is used for item,
component and exam raw points. Raw and transformed concepts remain separate;
no provider transformation is implemented.

## Item and result lifecycle

Objective unanswered items are `UNANSWERED` with governed zero points.
Constructed/RUBRIC_REQUIRED/MANUAL_REVIEW items are `PENDING_ASSESSMENT` or
`NOT_SCORABLE`, never fabricated zero scores. Mixed results are `INCOMPLETE`.
Objective-only runs create `REVIEW_REQUIRED` ResultVersions.

One stable `AssessmentResult` exists per attempt. Immutable ResultVersions carry
raw Decimal totals, outcome, incomplete reason, exact rule provenance, checksums
and supersession links. Current and historical versions remain queryable;
deterministic diffs compare score, outcome, completeness, rules and run.

The existing RulesEngine evaluates bound SCORING/RESULT/CERTIFICATION rules
through narrow score/result facts. No governed outcome rule means
`UNDETERMINED`; rule failure/error blocks release. Official certification
remains blocked. HumanReviewRequest binds the ResultVersion checksum; review
approval is required for release, stale reviews fail, and humans cannot mutate
ItemScore values. Corrections create new runs/versions.

## API and security

Institution routes cover score, run/result reads, version history, review,
approval, rejection, release and deterministic diff. Candidate routes return
only released results owned by the authenticated candidate in the active
organisation. Capabilities are `scoring.run`, `result.read`, `result.review`,
`result.release` and `result.manage`. Candidate-safe projections contain no
response payloads, answer keys, rule DSL, reviewer notes or private URLs.
Audit covers consequential scoring/result lifecycle events; candidate response
text is not audit metadata. Retention duration remains intentionally undefined.

## Tests, CI and known limitations

Local API validation covers objective exact scoring, declared partial credit,
Decimal arithmetic, unanswered/constructed handling, checksums, mutable-attempt
blocking and table registration. PostgreSQL CI explicitly forces the new
schema integration and dynamically checks the Alembic head without
`Base.metadata.create_all`.

The frontend adds a released-results candidate surface; no scoring logic is
implemented in the browser. AI/subjective assessment, formal certification,
result retention policy, worker scheduling and remote CI remain future gates.
