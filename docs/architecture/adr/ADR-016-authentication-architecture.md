# ADR-016: Authentication Architecture

Status: Accepted for MVP baseline

## Context

PrüfAI needs local authentication without coupling identity records to one
credential type, while retaining a path to enterprise identity providers.

## Decision

Keep `User` identity-only. Model local passwords as
`UserPasswordCredential` and validate authenticated requests through a
server-tracked `AuthSession`.

## Consequences

Credential providers can be added independently, and suspension/revocation
takes effect immediately. Authentication code does not duplicate RBAC.

## Alternatives considered

Putting password hashes on `User` was rejected because it couples identity to a
single provider. An external IdP was deferred until OIDC/SSO requirements are
approved.
