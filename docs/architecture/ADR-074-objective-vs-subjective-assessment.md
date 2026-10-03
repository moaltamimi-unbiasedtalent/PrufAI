# ADR-074 — Objective versus subjective assessment

Status: Accepted for MVP baseline

## Context
Question Bank contains both deterministic and constructed-response items.

## Decision
Only OBJECTIVE_KEYED items are scored automatically. RUBRIC_REQUIRED and MANUAL_REVIEW items become PENDING_ASSESSMENT and make mixed results incomplete.

## Consequences
Missing human assessment is never represented as a fabricated zero or score.

## Alternatives considered
Zero-filling, AI grading and inferred rubric formulas were rejected.
