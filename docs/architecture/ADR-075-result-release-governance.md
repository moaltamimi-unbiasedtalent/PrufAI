# ADR-075 — Result release governance

Status: Accepted for MVP baseline

## Context
Consequential results require controlled release.

## Decision
Incomplete or rule-blocked ResultVersions cannot release. HumanReviewRequest binds the exact result checksum; approval is required before release and official certification remains blocked.

## Consequences
Review is stale-safe and released history is immutable.

## Alternatives considered
Automatic release from raw percentage and mutable reviewer score overrides were rejected.
