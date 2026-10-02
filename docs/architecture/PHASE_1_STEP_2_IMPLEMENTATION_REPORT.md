# Phase 1 / Step 2 Implementation Report

## Executive Summary

This step adds a local-password authentication and server-session foundation
without changing the approved identity, tenancy, RBAC, or audit architecture.
It provides secure login, rotating refresh sessions, revocation, invitations,
password reset, CSRF protection, authentication throttling, and authenticated
principal resolution.

## Schema

Migration `0003_authentication_sessions` adds:

- `user_password_credentials` — one-to-one Argon2id credential record.
- `auth_sessions` — status, expiry, revocation, rotation generation, and only a refresh-token hash.
- `organisation_invitations` — tenant-scoped hashed one-time invitation records and approved role keys.
- `password_reset_tokens` — hashed, expiring, one-time reset records.

All records use UUIDs, foreign keys, timezone-aware timestamps, uniqueness
constraints, and lookup indexes.

## Authentication Architecture

`User` remains identity-only. `AuthenticationService` owns credential, session,
login, refresh, and revocation operations. `get_principal` verifies JWT claims,
session state, and current user state before constructing an optional tenant
context through the existing `build_tenant_context` function.

## Password Security

Passwords use Argon2id via `argon2-cffi`, with a central 12–128 character
policy. Plaintext passwords are neither persisted nor included in responses or
audit metadata.

## Session Architecture

Sessions are persistent and statusful (`ACTIVE`, `REVOKED`, `EXPIRED`). Access
tokens reference the session with `sid`; requests check the session and user on
every authenticated call. Users can list and revoke only their own sessions.

## Access Tokens

Signed HS256 JWTs contain only `sub`, `sid`, `iat`, `exp`, `jti`, issuer, and
audience. The default access lifetime is 15 minutes and is configurable.

## Refresh Tokens

Refresh values are opaque high-entropy strings with a visible session UUID and
unpredictable secret. Only a SHA-256 hash is stored. Successful refresh rotates
the value; stale-token reuse revokes the session and emits an audit event.

## Revocation

Logout, user-requested session deletion, refresh reuse, suspension enforcement,
and password reset all prevent continued authenticated use. Password reset
revokes all existing sessions and requires a new login.

## Tenant Context Integration

`X-PrufAI-Organisation-ID` is treated as an untrusted selector. The server
requires an active membership and loads current roles and permissions through
the existing centralized authorization service. Cross-tenant selectors and
suspended memberships are denied.

## Invitations

Users with `membership.manage` can create tenant invitations. Invitation role
keys are validated against tenant roles; the invitee cannot choose additional
roles. Tokens are hashed, expire after seven days by default, and cannot be
replayed. Delivery uses a notification abstraction with a safe fake sink for
tests.

## Password Reset

Reset requests always return a generic message. Reset tokens are hashed and
one-time. Confirmation validates policy, updates the credential, revokes all
sessions, marks the token used, and requires normal login.

## Rate Limiting

Login, reset requests, and refresh have configurable temporary limits. Production
uses the atomic Redis-backed limiter; development/tests explicitly use the
in-memory backend. Redis outages produce controlled `503` responses rather
than a silent downgrade. Keys hash source and account dimensions.

## Audit Events

Recorded events include `auth.login.succeeded`, `auth.login.failed`,
`auth.session.created`, `auth.session.refreshed`, `auth.session.revoked`,
`auth.refresh_reuse_detected`, `auth.invitation.created`,
`auth.invitation.accepted`, `auth.password_reset.requested`, and
`auth.password_reset.completed`. Sensitive-shaped metadata keys are rejected.

## Tests

The API suite contains 18 passing tests, including credential hashing and
policy, generic login behavior, session rotation/reuse, suspended users,
invitation replay, password-reset replay, and token claim minimization. A
PostgreSQL integration test is skipped locally without a service and runs in CI
after migration.

## CI/PostgreSQL Integration

The Python GitHub Actions job now provisions PostgreSQL 16, runs `alembic
upgrade head`, and checks the migrated schema through
`test_postgresql_integration.py`. Existing frontend, AI, lint, typecheck, and
unit-test jobs remain in place.

## Security Findings and Dependency Audit

- No real secrets, raw passwords, or raw authentication tokens were added.
- Production requires a strong `AUTH_JWT_SECRET`; development/test defaults are explicit.
- Refresh and logout use double-submit CSRF protection.
- The known inherited Next.js/PostCSS advisory remains documented; remediation requires a deferred breaking Next.js major upgrade.

## Final Security Closure

- Redis rate limiter implemented with atomic Lua `INCR`/`EXPIRE` windows.
- Two-instance shared-state, threshold, expiry, privacy-key, and Redis-failure tests pass.
- PostgreSQL and Redis services, migration execution, and integration-test commands are configured in GitHub Actions.
- GitHub Actions execution remains an operator verification requirement; this environment cannot inspect remote checks.
- The inherited Next.js/PostCSS advisory remains the only npm production advisory and still requires a breaking major upgrade.

## Known Limitations

1. Production email delivery and a shared Redis limiter are not configured.
2. MFA, OIDC/SSO, SAML, passkeys, and candidate authentication are deferred.
3. Live PostgreSQL execution depends on Docker/CI availability; local validation uses SQLite plus Alembic offline SQL.

## Recommendations

Review the Next.js major-version upgrade separately, configure a production
secret manager and Redis deployment, then add provider-backed email and MFA
only in their controlled phases.
