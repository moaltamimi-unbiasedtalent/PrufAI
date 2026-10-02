# ADR: Tenant context and isolation

Status: Accepted for MVP baseline

+## Context

Users may belong to multiple organisations and tenant-owned data must never rely on caller-supplied IDs alone.

## Decision

Construct an immutable TenantContext and require centralized authorization plus explicit organisation predicates in tenant services.

## Consequences

Makes tenant scope a reusable service boundary while authentication remains deferred.

## Alternatives considered

Implicit global session state; filtering after unrestricted queries.
