# ADR-020: Authentication Rate Limiting

Status: Accepted for MVP baseline

## Context

Authentication endpoints need temporary abuse controls without permanent
account lockouts that attackers can weaponize.

## Decision

Apply configurable temporary limits to login, password-reset requests, and
refresh. Use an in-memory implementation for local/tests and retain a Redis
replacement seam for deployed environments.

## Consequences

Normal usage remains recoverable while repeated abuse is throttled. Production
must select a shared Redis-backed implementation for multi-instance behavior.

## Alternatives considered

Permanent account lockout was rejected because it enables denial of service.
Unbounded in-process counters were rejected because they do not scale across
instances.
