# ADR-076 — Scoring provenance and reproducibility

Status: Accepted for MVP baseline

## Context
A result must identify every governed input used to produce it.

## Decision
Persist exact attempt, response revision, ExamVersion, StandardPackVersion, QuestionBankItemVersion, RuleVersion, engine/configuration and output checksums. Canonical JSON excludes administrative timestamps from substantive checksums.

## Consequences
Historical reruns and deterministic diffs are possible without exposing answer keys to candidates.

## Alternatives considered
Latest-version lookup, opaque provider scores and audit-only provenance were rejected.
