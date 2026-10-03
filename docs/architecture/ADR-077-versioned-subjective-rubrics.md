# ADR-077: Versioned subjective rubrics

Status: Accepted for MVP baseline

## Context

Subjective writing assessment must use an exact, reviewable rubric without
copying provider-owned rubric text or resolving a moving “current” version.

## Decision

Introduce stable `AssessmentRubric` identities and immutable `RubricVersion`
records containing normalized criteria, levels, aggregation, evidence policy,
AI policy, Standard Pack compatibility, and a substantive checksum.

## Consequences

Historical assessments remain pinned to the exact rubric and source evidence.
Corrections create a new version; executable rubric content is rejected.

## Alternatives considered

Generic provider lookup, mutable rubric rows, and free-form scoring expressions
were rejected.
