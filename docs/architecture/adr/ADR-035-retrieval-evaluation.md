# ADR-035: Retrieval Evaluation

Status: Accepted for MVP baseline

## Context

Relevance metrics alone cannot detect provider leakage, invalid citations, or
historical-version errors.

## Decision

Evaluate Recall@K, Precision@K, MRR, nDCG, provider/assessment isolation,
scope compliance, citation validity, historical accuracy, and before/after
reranking signals. Export retrieval-only RAGAS-compatible records.

## Consequences

Domain safety invariants remain primary while future RAGAS metrics can extend
the run once generated answers exist.

## Alternatives considered

LLM judging in ordinary CI and fabricated answer metrics were rejected.
