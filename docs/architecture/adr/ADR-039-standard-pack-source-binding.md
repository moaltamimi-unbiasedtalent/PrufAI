# ADR-039: Standard Pack source binding

Status: Accepted for MVP baseline

## Context

Pack behavior must remain explainable against approved evidence.

## Decision

Bindings reuse KnowledgeSource and pin exact KnowledgeDocumentVersion for
normative evidence. Only approved or historically approved versions qualify.

## Consequences

Newer documents cannot silently rewrite old packs. Legal and provenance review
remains visible.

## Alternatives considered

Binding only to a provider or latest source was rejected as non-reproducible.
