# ADR-069: Idempotent response synchronization

**Status:** Accepted for MVP baseline

## Context

Autosave and reconnect can replay operations, while multiple devices can race.

## Decision

Every mutation carries a client operation UUID and expected revision. The
database uniquely scopes operation IDs; same-payload replay returns the existing
revision, different-payload reuse conflicts, and stale revisions are rejected.

## Consequences

Response history is append-oriented and devices must reconcile explicit
conflicts instead of silently overwriting.

## Alternatives considered

Last-write-wins and client-only deduplication were rejected because they lose
candidate work under retries and concurrency.
