# ADR: Standard Pack concept

Status: Accepted for MVP baseline

+## Context

German assessments span CEFR, LiD, providers, and custom standards that evolve independently.

## Decision

Represent provider/exam variation as independently versioned Standard Packs with source bindings; do not copy protected content without rights.

## Consequences

Avoids level/provider hard-coding and preserves provenance.

## Alternatives considered

A single hard-coded exam configuration; copied provider material.
