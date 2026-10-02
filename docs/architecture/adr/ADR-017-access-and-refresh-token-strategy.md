# ADR-017: Access and Refresh Token Strategy

Status: Accepted for MVP baseline

## Context

The browser needs short-lived API access while sessions must be revocable and
refresh credentials must not be exposed to JavaScript.

## Decision

Use signed short-lived JWT access tokens containing identity/session claims
only, plus rotating opaque refresh values stored hashed in `AuthSession` and
delivered in an HttpOnly cookie.

## Consequences

Authorization remains current server-side. Refresh replay can revoke a session;
cookie use requires CSRF protection.

## Alternatives considered

Long-lived JWT refresh tokens and localStorage persistence were rejected because
they weaken revocation and browser credential containment.
