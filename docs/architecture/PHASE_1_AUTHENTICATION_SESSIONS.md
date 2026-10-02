# PrüfAI Authentication and Sessions

Status: accepted MVP foundation for Phase 1 / Step 2

## Architecture

`User` remains identity-only. Local password authentication is a separate
`UserPasswordCredential`; future OIDC, SAML, passkeys, or MFA providers can add
their own credential records without changing tenant authorization.

```text
Browser/API client
      |  access token (memory) + refresh/CSRF cookies
      v
Authentication API -> JWT verifier -> AuthSession + User status
      |                                      |
      +--> TenantContext --------------------+--> current memberships/RBAC
      |
      +--> append-only AuditEvent
```

## Passwords and sessions

Passwords are validated centrally at 12–128 characters and hashed with
Argon2id through `argon2-cffi`. Raw passwords and token values are never
persisted. A successful login creates an `AuthSession` with a 15-minute
configurable access-token lifetime and a longer-lived session lifetime.

Access tokens are signed JWTs containing only `sub`, `sid`, `iat`, `exp`, `jti`,
issuer, and audience. Each authenticated request verifies the signature,
issuer, audience, expiry, session status, session expiry, and current user
status. Roles and permissions remain server-side.

Refresh credentials are opaque, high-entropy values formatted with a visible
session UUID plus an unpredictable secret. Only a SHA-256 token hash is stored.
Successful refresh replaces the hash and increments the generation. A token
whose session UUID is valid but whose hash is stale is treated as reuse and
revokes the session.

The refresh value is an application-specific HttpOnly cookie. Secure,
SameSite, path, and lifetime are configurable, with secure cookies required in
production. Access tokens are returned to the client and are not persisted in
browser localStorage.

## CSRF and HTTP boundary

Refresh and logout require a double-submit CSRF value: the readable
`prufai_csrf` cookie must match `X-PrufAI-CSRF`. CORS allows only configured
origins when configured and never combines credentials with `*`. The API adds
baseline content-type, framing, and referrer security headers.

## Tenant and platform context

An authenticated request may provide `X-PrufAI-Organisation-ID`. The server
resolves the current user, verifies an active membership, loads current roles
and permissions, and constructs `TenantContext`. A forged or suspended
organisation context is denied. Platform authority is obtained only from
explicit `PlatformRoleAssignment` records; it does not automatically imply
tenant impersonation.

## Sessions, invitations, and reset

Users can list active sessions and revoke only their own sessions. Logout,
password reset, and future password changes can revoke sessions; reset uses
the safe MVP behavior of revoking all existing sessions and requiring a fresh
login.

Organisation invitations and password-reset tokens are one-time opaque values
stored only as hashes. Invitations are authorized by `membership.manage`, are
tenant-scoped, and carry server-approved role keys. Acceptance creates or
reuses the normalized identity and never overwrites an existing password.
Reset requests return the same generic response for known and unknown emails.

## Rate limiting and audit

Login, reset requests, and refresh use configurable temporary throttling. The
production backend is a shared Redis limiter using an atomic Lua
`INCR`/`EXPIRE` operation, so concurrent API instances share a fixed window.
Development and tests may explicitly select the in-memory backend. Production
configuration rejects that backend and never silently downgrades to it.

Rate-limit keys hash source and normalized account identifiers; plaintext
emails, passwords, access tokens, refresh tokens, reset tokens, and invitation
tokens are not used as keys. If configured Redis is unavailable, affected
authentication operations return controlled service-unavailable responses
rather than disabling protection. CI provisions Redis alongside PostgreSQL and
runs the shared-state integration test.

Security events include successful/failed login, session creation/refresh/
revocation, refresh reuse, invitation creation/acceptance, and reset request/
completion. Audit metadata rejects password, token, secret, and API-key-shaped
keys.

## Future extension and limitations

MFA and external identity providers are extension points, not implemented in
this step. Email delivery is represented by a fake notification sink for tests;
no production provider is configured. Local Docker access and live PostgreSQL
execution remain environment-dependent, while CI now provisions PostgreSQL,
runs Alembic, and checks the migrated schema.
