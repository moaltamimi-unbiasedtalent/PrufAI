# ADR-020: Authentication Rate Limiting

Status: Accepted for MVP baseline

## Context

Authentication endpoints need temporary abuse controls without permanent
account lockouts that attackers can weaponize.

## Decision

Apply configurable temporary limits to login, password-reset requests, and
refresh. Production uses a shared Redis-backed limiter with an atomic
Lua `INCR`/`EXPIRE` operation. Development and tests may explicitly use the
in-memory implementation.

## Consequences

Normal usage remains recoverable while repeated abuse is throttled. Production
Redis outages fail affected authentication operations with a controlled
service-unavailable response; there is no silent per-process fallback.

## Alternatives considered

Permanent account lockout was rejected because it enables denial of service.
Unbounded in-process counters were rejected because they do not scale across
instances. A non-atomic Redis GET/SET counter was rejected because concurrent
requests could bypass thresholds.
