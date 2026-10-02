# ADR-032: Retrieval Reranking

Status: Accepted for MVP baseline

## Context

Hybrid retrieval provides candidates but not necessarily the most useful order.

## Decision

Use a versioned reranker abstraction over the already scope-filtered candidate
set. Keep original channel and fusion signals and record rerank profile data.

## Consequences

Ordering can improve without changing authorization or provider boundaries.
Production failures are explicit; deterministic reranking is test/dev only.

## Alternatives considered

Corpus-wide reranker search and silent lexical fallback were rejected.
