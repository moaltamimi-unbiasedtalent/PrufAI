# ADR-066: Exam delivery manifest security

Status: Accepted for MVP baseline

## Context

Administration needs protected review data while a future candidate runtime
must not receive answer keys or private storage details.

## Decision

Expose separate administrative and delivery-safe manifests. The safe manifest
reuses Question Bank delivery projection and contains opaque media references
only; no public candidate endpoint is added in this phase.

## Consequences

Answer-key security is enforced by DTO construction rather than presentation
hiding, and future runtime authorization can resolve media securely.

## Alternatives considered

One universal manifest or CSS-hidden answer fields were rejected.
