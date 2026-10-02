# ADR-028: pgvector and Embedding Profiles

Status: Accepted for MVP baseline

## Context

Vector retrieval needs compatible, reproducible representations and safe model
evolution.

## Decision

Enable PostgreSQL `vector`, persist fixed 384-dimensional cosine vectors, and
record provider/model/dimension/chunker in an index profile. Production uses a
configured embedding adapter; deterministic embeddings are test/dev only.

## Consequences

Profiles can coexist for reindex evaluation. Exact cosine search is used before
introducing an ANN index.

## Alternatives considered

Untracked model metadata and silent test fallback were rejected as unsafe.
