# ADR-029: Hybrid Retrieval and Rank Fusion

Status: Accepted for MVP baseline

## Context

Standards retrieval benefits from exact lexical matches and semantic similarity.

## Decision

Run PostgreSQL full-text and pgvector retrieval over the same validated scope,
deduplicate chunks, and combine channels with deterministic Reciprocal Rank
Fusion. Preserve each channel's rank and score.

## Consequences

Results are explainable and deterministic. No LLM reranking or answer synthesis
is introduced.

## Alternatives considered

Raw score addition and opaque LLM reranking were rejected for incompatibility
and auditability risk.
