# ADR-065: Exam validation and publication

Status: Accepted for MVP baseline

## Context

Structural, rights, classification, pack, and deterministic rule constraints
must remain independent of human or AI intent.

## Decision

Recompute authoritative validation before review and each protected lifecycle
transition. Required rule errors and blocking failures, incomplete slots,
rights failures, duplicates, and unauthorized certification block progression.
Human review is persisted through HumanReviewRequest and checksum-bound.

## Consequences

A stale review cannot approve changed composition and published versions are
immutable.

## Alternatives considered

Trusting a frontend snapshot or an LLM validation result was rejected.
