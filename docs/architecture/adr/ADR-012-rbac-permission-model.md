# ADR: RBAC and permission model

Status: Accepted for MVP baseline

+## Context

Future domains need capability checks rather than scattered role-name conditionals.

## Decision

Persist scoped Roles, Permissions, RolePermission mappings, MembershipRole assignments, and idempotent seed definitions.

## Consequences

Roles remain extensible and services can require stable resource.action capabilities.

## Alternatives considered

String role checks in routes; a single role column on User.
