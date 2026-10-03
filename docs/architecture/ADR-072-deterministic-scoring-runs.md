# ADR-072 — Deterministic scoring runs

Status: Accepted for MVP baseline

## Context
Submitted responses must be scored reproducibly without AI or mutable attempt state.

## Decision
Score only SUBMITTED attempts through versioned ScoringRun records with frozen response snapshots, Decimal arithmetic, explicit engine/configuration checksums and database idempotency.

## Consequences
Retries converge; corrections create new runs; operational workers remain optional.

## Alternatives considered
Scoring directly from live responses, float arithmetic, and LLM grading were rejected.
