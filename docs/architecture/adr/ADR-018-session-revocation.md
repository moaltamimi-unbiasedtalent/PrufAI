# ADR-018: Session Revocation

Status: Accepted for MVP baseline

## Context

Suspension, logout, password reset, and suspected refresh theft must invalidate
access promptly.

## Decision

Every access token references an active `AuthSession`. Requests check session
and user state. Users may list and revoke only their own sessions; reset and
password changes revoke all existing sessions.

## Consequences

Revocation is immediate at the API boundary and does not wait for JWT expiry.
The session table is part of every authenticated request path.

## Alternatives considered

Stateless JWT-only validation was rejected because it cannot provide immediate
account or session revocation.
