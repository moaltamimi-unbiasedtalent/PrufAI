# ADR: Identity model

Status: Accepted for MVP baseline

+## Context

The platform needs identities before authentication mechanics are selected.

## Decision

Persist a UUID-based User with normalized unique email, display name, lifecycle status, and timestamps; do not persist credentials in this step.

## Consequences

Keeps identity separate from authentication and minimizes PII.

## Alternatives considered

A user record with password fields; identity embedded in memberships.
