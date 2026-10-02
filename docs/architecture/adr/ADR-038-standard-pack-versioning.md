# ADR-038: Standard Pack versioning and immutability

Status: Accepted for MVP baseline

## Context

Exam reproducibility requires exact historical configuration.

## Decision

Versions carry lifecycle, effective dates, actor timestamps, supersession, and
canonical configuration checksums. Published/active/retired versions are
immutable; changes require a new version.

## Consequences

Historical examinations can pin an exact version and resolve it as-of a date.
Operational edits require deliberate version creation.

## Alternatives considered

Mutable current configuration and latest-provider resolution were rejected.
