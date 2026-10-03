# ADR-073 — Score and result versioning

Status: Accepted for MVP baseline

## Context
Objective scores and released outcomes require immutable history.

## Decision
Persist ItemScore and ScoringComponent evidence under ScoringRun, one stable AssessmentResult per attempt, and immutable ResultVersion rows with supersession and checksums.

## Consequences
Historical results remain auditable and corrections are additive.

## Alternatives considered
Mutating one result row or creating unrelated result identities per rerun were rejected.
