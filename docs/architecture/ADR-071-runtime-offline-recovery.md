# ADR-071: Runtime offline recovery

**Status:** Accepted for MVP baseline

## Context

Short network interruptions should not discard a candidate response, but the
runtime is not a fully offline exam.

## Decision

Queue bounded response operations in IndexedDB, excluding auth tokens and answer
keys, then synchronize deterministically on reconnect. The server remains the
authority for time, navigation, ownership and acceptance.

## Consequences

Candidates see explicit queued, conflict, expired and submitted states. The
visual timer is only an estimate while disconnected.

## Alternatives considered

localStorage queues and fully offline authorization were rejected because they
increase token exposure and cannot enforce server timing or ownership.
