# ADR-067: Candidate attempt and session model

**Status:** Accepted for MVP baseline

## Context

An assignment, business attempt and live runtime location have different
ownership and lifecycle semantics.

## Decision

Persist assignments, attempts, one-to-one sessions, responses and revisions in
PostgreSQL. Assignments pin exact ExamVersions; sessions never duplicate exam
content.

## Consequences

Recovery, ownership checks and audit boundaries are explicit. Redis cannot become
the completion truth.

## Alternatives considered

One mutable attempt record, browser-only state, and Redis-only sessions were
rejected because they weaken history, recovery and transactional guarantees.
