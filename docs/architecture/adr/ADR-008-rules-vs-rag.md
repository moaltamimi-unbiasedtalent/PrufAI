# ADR: Rules versus RAG

Status: Accepted for MVP baseline

+## Context

Knowledge retrieval and deterministic examination constraints have different correctness properties.

## Decision

Use RAG for grounded knowledge and Rules-as-Code for constraints, eligibility, timing, and deterministic decisions.

## Consequences

Keeps probabilistic retrieval from silently replacing enforceable policy.

## Alternatives considered

Using prompts as policy; using RAG for deterministic scoring.
