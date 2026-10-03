# ADR-080: Human final authority for subjective scoring

Status: Accepted for MVP baseline

## Context

Consequential subjective assessment requires a human decision bounded by the
approved rubric and existing release governance.

## Decision

All subjective writing proposals require a checksum-bound HumanReviewRequest.
Reviewers may correct criterion values only within rubric bounds; AI proposals,
human values, reviewer identity, and timestamps remain separately recorded.

## Consequences

AI cannot approve, release, or bypass Rules or certification guards. Stale
reviews fail and corrections create new immutable assessment versions.

## Alternatives considered

High-confidence auto-approval, unconstrained reviewer points, and AI-only
release were rejected.
