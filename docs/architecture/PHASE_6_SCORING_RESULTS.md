# Phase 6 / Step 2 — Deterministic scoring and governed results

## Purpose and boundaries

Step 2 scores only submitted attempts with deterministic Question Bank
metadata. It does not perform writing/speaking AI grading, subjective rubric
interpretation, certificates, appeals, psychometric calibration or provider
rule invention. Candidate responses remain evidence; the scoring engine is
not an LLM.

```text
Submitted Attempt
      │ exact response revisions + runtime checksum
      ▼
Frozen ResponseSnapshot ── SHA-256 ──► ScoringRun (objective engine v1.0)
      │                                  │
      │                                  ├─ ItemScore (Decimal)
      │                                  ├─ EXAM score component
      │                                  └─ exact Pack/Rule provenance
      ▼                                  ▼
 constructed item ─► PENDING_ASSESSMENT   ResultVersion
                                             │
                                      HumanReviewRequest
                                             │
                                      APPROVED → RELEASED
                                             │
                                   candidate-safe projection
```

## Snapshot, scoring and reproducibility

Only `SUBMITTED` attempts are scoreable. The response snapshot freezes the
attempt, exact ExamVersion, runtime checksum, submitted timestamp, binding and
QuestionBankItemVersion IDs, accepted response revision IDs and payload
checksums. Its canonical JSON produces a response-set SHA-256 checksum; answer
keys are not copied into the snapshot.

`ScoringRun` records `prufai_objective_scoring@1.0`, the exact
StandardPackVersion, configuration checksum, response checksum, lifecycle and
run checksum. A unique database idempotency key covers attempt, response set,
engine version and configuration. A changed input/configuration creates a new
run; completed historical runs are not overwritten.

`SELECT_ONE` uses exact option IDs and the immutable item key. `SELECT_MULTIPLE`
uses exact set matching unless a versioned `DECLARED_TABLE` partial-credit
policy is explicitly present in item metadata. No partial-credit formula is
inferred. All consequential points and aggregates use PostgreSQL `NUMERIC` and
Python `Decimal`.

Objective unanswered items are `UNANSWERED` only when the exact immutable item
metadata declares `unanswered_policy: {type: ZERO, points: "0"}`. The scoring
engine has no universal unanswered default: missing, malformed, negative, or
non-zero unanswered policy is a deterministic configuration failure. The item
scoring metadata, item checksum and exact Rule/Pack references are included in
the scoring configuration checksum, so changing the policy cannot replay as
the same run.
`DECLARED_TABLE` partial credit is a bounded Decimal lookup: malformed
Decimals, negative or over-maximum values, unknown options, impossible
responses, and missing mappings fail deterministically. It has no executable
expression or dynamic import path.
`RUBRIC_REQUIRED`, `MANUAL_REVIEW`, constructed text and unsupported modes are
`PENDING_ASSESSMENT`/`NOT_SCORABLE`, never silently zeroed. Mixed exams retain
objective raw components but create an `INCOMPLETE` result while required
constructed assessment is missing.

Raw awarded/max points remain separate from any future transformed score. No
DTZ, Goethe, telc or LiD weighting/pass threshold is hard-coded. LiD remains
`INTEGRATION_KNOWLEDGE`/`LEBEN_IN_DEUTSCHLAND` with no invented certification
outcome.

## Rules, results and review

Bound `SCORING`, `RESULT` and `CERTIFICATION` RuleVersions are evaluated through
the existing `RulesEngine` using a narrow score/result fact adapter. Missing
governed outcome rules produce `UNDETERMINED`; a rule failure or error blocks
release. `OFFICIAL_CERTIFICATION` remains blocked without future authorization.

One stable `AssessmentResult` exists per attempt. Each immutable
`ResultVersion` records the scoring run, response/configuration checksums,
raw Decimal score, outcome, completeness, rule provenance, checksum and
supersession link. Lifecycle is `DRAFT`, `INCOMPLETE`, `REVIEW_REQUIRED`,
`APPROVED`, `RELEASED`, `REJECTED`, `SUPERSEDED`, `VOIDED`.

Human review reuses `HumanReviewRequest` and binds the reviewed ResultVersion
checksum. Stale reviews are rejected. Humans approve/reject/release but cannot
edit deterministic ItemScores; corrections require a new scoring run and
ResultVersion. Incomplete or rule-blocked versions cannot release. Released
historical versions remain immutable and queryable; deterministic result diffs
compare score, outcome, completeness, rule provenance and scoring run.
Configuration failures transition a running `ScoringRun` to `FAILED` and emit
`scoring.run.failed` with only run/attempt identifiers, failure category and
engine version. Candidate response payloads, answer keys and tracebacks are
never audit metadata; a failed run creates no releasable result.

## API, security and privacy

Institution scoring/review/release routes require explicit capabilities:
`scoring.run`, `result.read`, `result.review`, `result.release`, and
`result.manage`. Candidate result routes are ownership- and tenant-scoped and
return only `RELEASED` safe fields. Admin projections include provenance and
checksums but no unnecessary answer keys. Candidate DTOs contain no response
payload, answer key, rule DSL, reviewer notes or private URLs. Lifecycle audit
events cover run creation/completion/failure and result creation/review/
approval/rejection/release/supersession; response text is not audit metadata.

Formal assessment-result retention remains a pre-pilot governance requirement.
No AI service or paid provider is involved.
