# ADR-070: Candidate delivery security boundary

**Status:** Accepted for MVP baseline

## Context

Exam Studio administrative representations contain governance and answer-bearing
data that candidates must never receive.

## Decision

Candidate runtime builds a deterministic plan only from the existing delivery
projection. It strips answer keys and internal governance fields, rejects
unsupported response modes, and carries only opaque media references.

## Consequences

Candidate APIs cannot use admin manifests or enumerate Question Bank records.
Protected media resolution and scoring remain separate future capabilities.

## Alternatives considered

Filtering admin DTOs in the browser and hiding fields in React were rejected as
non-security boundaries.
