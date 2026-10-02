# ADR-031: Typed Retrieval Scope

Status: Accepted for MVP baseline

## Context

Retrieval filters affect authorization, assessment-family safety, effective
dates, and citation correctness.

## Decision

Use a typed scope covering provider/authority/source, assessment family,
exam/language/CEFR/skill/document type, shared-authority inclusion, and optional
historical date. LiD cannot carry a CEFR filter.

## Consequences

Scope validation is centralized and platform APIs cannot pass arbitrary filter
dictionaries. Current and historical semantics remain explicit.

## Alternatives considered

Unvalidated dictionaries and fuzzy CEFR adjacency were rejected.
