# ADR-033: Citation and Evidence Contract

Status: Accepted for MVP baseline

## Context

Downstream RAG needs stable, provenance-preserving evidence without private
storage leakage.

## Decision

Build citations from authority/source/version/page/chunk metadata with a stable
hash identifier. Preserve effective dates, checksum, legal status, and optional
section/URL without inventing missing values.

## Consequences

Evidence remains navigable and rights-aware. Citation metadata is distinct from
permission to redistribute source text.

## Alternatives considered

Mutable display strings and private object URLs were rejected.
