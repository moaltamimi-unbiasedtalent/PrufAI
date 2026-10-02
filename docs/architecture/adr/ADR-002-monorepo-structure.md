# ADR: Monorepo structure

Status: Accepted for MVP baseline

+## Context

Frontend, API, AI, shared contracts, standards, infrastructure, and docs need clear ownership.

## Decision

Use apps/, services/, packages/, standard-packs/, infra/, tests/, and docs/architecture/ boundaries.

## Consequences

Supports local development and future service extraction without premature distribution.

## Alternatives considered

Multiple repositories; a flat repository.
