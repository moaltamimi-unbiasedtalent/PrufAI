# ADR-036: Retrieval Quality Gates

Status: Accepted for MVP baseline

## Context

Small synthetic baselines must fail closed on safety regressions.

## Decision

CI gates require zero provider and assessment contamination plus 100% scope
compliance, citation validity, and historical synthetic-case accuracy. Relevance
metrics are reported as baselines rather than production claims.

## Consequences

Unsafe retrieval changes fail early without requiring paid external model calls.

## Alternatives considered

Soft warnings for contamination and external-only evaluation were rejected.
