# ADR: Database

Status: Accepted for MVP baseline

+## Context

The platform will need durable audit, versioning, results, and tenant-scoped data.

## Decision

Use PostgreSQL with SQLAlchemy 2.x and Alembic; defer product schema tables until domain design.

## Consequences

Mature relational integrity, migration discipline, and future pgvector option.

## Alternatives considered

A document-only primary store; speculative tables in the baseline.
