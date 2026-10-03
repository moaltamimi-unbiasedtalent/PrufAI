# ADR-062: Exam versioning

Status: Accepted for MVP baseline

## Context

An assessment identity must survive multiple governed compositions without
mutating historical delivery.

## Decision

Keep stable `Exam` identity separate from immutable, lifecycle-controlled
`ExamVersion` records. Each version pins an exact Standard Pack version and
uses effective dates and explicit supersession.

## Consequences

Historical versions remain reproducible and current resolution is deterministic.
Corrections require a new version.

## Alternatives considered

Mutable exam rows and implicit “latest” resolution were rejected because they
break auditability and historical reproduction.
