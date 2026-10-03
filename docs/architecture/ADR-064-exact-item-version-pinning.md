# ADR-064: Exact ItemVersion pinning

Status: Accepted for MVP baseline

## Context

Eligible content changes over time and stable QuestionBankItem identity is not
enough to reproduce an assessment.

## Decision

Every item binding stores an exact QuestionBankItemVersion ID and is selected
through the existing exact-pack eligible-bank service.

## Consequences

Assignments are reproducible and server-side eligibility prevents stale or
cross-tenant selections.

## Alternatives considered

Stable-item-only or latest-item lookup was rejected.
