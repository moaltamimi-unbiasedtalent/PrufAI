# ADR: Platform versus tenant authority

Status: Accepted for MVP baseline

+## Context

PrüfAI platform administrators need cross-tenant capabilities without fake tenant memberships.

## Decision

Persist explicit PlatformRoleAssignment records and platform-prefixed permissions; platform authority is independent of organisation IDs.

## Consequences

Cross-tenant operation is deliberate, inspectable, and separately testable.

## Alternatives considered

A special organisation used to simulate platform authority; trusting any admin role.
