# ADR-063: Blueprint-first exam assembly

Status: Accepted for MVP baseline

## Context

Standard Packs already define the authoritative module, section, and task
hierarchy.

## Decision

Materialise that exact hierarchy into ExamVersion child rows referencing the
pack entities. Materialisation is idempotent and validates ownership.

## Consequences

Exam Studio has concrete slots while the Standard Pack remains authoritative.
No second taxonomy or provider-specific part layer is introduced.

## Alternatives considered

Copying mutable pack text or inventing a parallel blueprint was rejected.
