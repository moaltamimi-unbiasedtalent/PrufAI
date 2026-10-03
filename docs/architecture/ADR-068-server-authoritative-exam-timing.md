# ADR-068: Server-authoritative exam timing

**Status:** Accepted for MVP baseline

## Context

Client clocks, browser lifecycle and reconnects are not trustworthy timing
authorities.

## Decision

The server records start time, snapshots governed duration into the attempt,
calculates the deadline, and enforces expiry at request time and through a sweep
hook. There is no implicit grace period or candidate pause.

## Consequences

Refresh, close and offline display cannot extend an attempt. The client receives
server time only for presentation and reconciliation.

## Alternatives considered

Client timers and heartbeat-only enforcement were rejected because either can be
manipulated or fail during network loss.
