# ADR-078: Governed writing assessment

Status: Accepted for MVP baseline

## Context

Writing responses are untrusted constructed evidence and must not become an AI
or browser-controlled final score.

## Decision

Create stable `SubjectiveAssessment` identities and immutable
`SubjectiveAssessmentVersion` records pinned to the submitted response
revision, Question Bank item, Exam Version, Standard Pack, and Rubric Version.
Every AI proposal enters mandatory human review.

## Consequences

AI proposals remain distinguishable from approved values. Approved versions can
feed a new deterministic ScoringRun while the original incomplete run remains
immutable.

## Alternatives considered

Direct LLM result release, mutable response scoring, and browser-side scoring
were rejected.
