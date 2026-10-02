# ADR: Memory model

Status: Accepted for MVP baseline

+## Context

Future workflows need execution context and controlled longitudinal knowledge.

## Decision

Separate short-term workflow state from governed long-term memory with purpose, tenant, retention, and provenance boundaries.

## Consequences

Supports useful continuity without uncontrolled retention or cross-tenant leakage.

## Alternatives considered

One undifferentiated memory store; implicit model context as memory.
