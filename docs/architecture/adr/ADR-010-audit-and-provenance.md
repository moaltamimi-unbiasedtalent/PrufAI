# ADR: Audit and provenance

Status: Accepted for MVP baseline

+## Context

Assessment and AI outputs must be explainable and reviewable.

## Decision

Use append-oriented audit/provenance events that reference actors, tenant context, versions, inputs, outputs, approvals, and timestamps.

## Consequences

Creates an evidence trail for governance and later compliance work.

## Alternatives considered

Mutable operational logs as the audit record; unversioned AI output.
