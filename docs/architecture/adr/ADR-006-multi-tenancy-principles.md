# ADR: Multi-tenancy principles

Status: Accepted for MVP baseline

+## Context

Institutions and candidates require strict data isolation.

## Decision

Treat tenant context, least privilege, RBAC, future ABAC, object isolation, and no cross-tenant memory/RAG as mandatory future boundaries.

## Consequences

Makes isolation an architectural invariant before business data exists.

## Alternatives considered

Tenant-by-convention; global queries with post-filtering.
