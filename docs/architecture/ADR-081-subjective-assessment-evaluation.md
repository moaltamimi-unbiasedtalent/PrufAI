# ADR-081: Subjective assessment evaluation

Status: Accepted for MVP baseline

## Context

Assessment quality must be measured without claiming examiner equivalence from
synthetic or limited evaluation data.

## Decision

The MVP evaluation contract uses synthetic golden writing fixtures and reports
schema validity, criterion coverage, evidence validity, score distance,
agreement/disagreement, and adversarial prompt-injection outcomes. Metrics are
engineering gates and not certification claims.

## Consequences

Evaluation artifacts remain safe, reproducible, and separate from candidate
data. Speaking and oral assessment remain Phase 7 / Step 2 scope.

## Alternatives considered

Unvalidated self-confidence, aggregate accuracy claims, and production
candidate text as a test corpus were rejected.
