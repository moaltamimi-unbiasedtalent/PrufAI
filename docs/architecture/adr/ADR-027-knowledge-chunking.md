# ADR-027: Deterministic Knowledge Chunking

Status: Accepted for MVP baseline

## Context

Retrieval needs stable, auditable units without rewriting standards content.

## Decision

Use page-local deterministic paragraph chunking with persisted chunker name,
version, configuration, hashes, offsets, and page provenance. No LLM is used.

## Consequences

Citations are precise and reindexing is reproducible. Cross-page semantic
windows are deferred until a later evaluated change.

## Alternatives considered

LLM summarization and cross-page chunks were rejected because they weaken
regulatory traceability and source fidelity.
