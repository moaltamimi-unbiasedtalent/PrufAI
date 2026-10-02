# ADR-040: Assessment classification

Status: Accepted for MVP baseline

## Context

Practice, institutional use, placement, and official certification have
different governance implications.

## Decision

Support PRACTICE, MOCK, PLACEMENT_PROGRESS, INSTITUTIONAL, and
OFFICIAL_CERTIFICATION. Official certification is rejected unless a future
explicit authorization mechanism exists; no provider authorization is inferred.

## Consequences

Pack authors can declare safe uses while the platform remains fail-closed on
certification claims.

## Alternatives considered

Allowing all classifications by default was rejected.
