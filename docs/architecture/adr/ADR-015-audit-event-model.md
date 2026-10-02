# ADR: Audit event model

Status: Accepted for MVP baseline

+## Context

Identity, tenancy, and future AI/product operations require durable provenance distinct from logs and memory.

## Decision

Persist append-oriented AuditEvent records and expose a validating AuditService with no update/delete API.

## Consequences

Creates a consistent evidence seam while avoiding sensitive metadata and mutable history.

## Alternatives considered

Unstructured logs; direct table writes from every domain; mutable audit records.
