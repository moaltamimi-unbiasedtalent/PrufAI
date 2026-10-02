# ADR: AI service boundary

Status: Accepted for MVP baseline

+## Context

AI workloads will eventually need distinct scaling, evaluation, and governance.

## Decision

Keep a minimal independently runnable Python AI service boundary; add LangChain/LangGraph only when a later phase requires them.

## Consequences

Separates AI concerns without adding a dependency tree or LLM coupling now.

## Alternatives considered

Direct LLM calls from UI/API; a premature agent microservice mesh.
