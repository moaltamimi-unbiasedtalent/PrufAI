# ADR-030: Provider Isolation

Status: Accepted for MVP baseline

## Context

Provider-specific standards must not silently contaminate one another.

## Decision

Provider and authority filters are explicit fields in `RetrievalScope`; no
low-result fallback widens the provider set. Shared standards require explicit
authority inclusion.

## Consequences

Insufficient scoped results remain insufficient, preventing unannounced
cross-provider evidence.

## Alternatives considered

Automatic provider expansion was rejected because it changes assessment scope.
