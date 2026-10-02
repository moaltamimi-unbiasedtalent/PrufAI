# ADR-041: Structural Standard Pack validation

Status: Accepted for MVP baseline

## Context

Configuration needs deterministic safety checks before human approval, while
executable standards rules belong to the next step.

## Decision

Return typed PASS/FAIL/WARN results with stable issue codes. Validate taxonomy,
bindings, ordering, timing, task ranges, classifications, and checksum inputs.
Do not evaluate arbitrary expressions.

## Consequences

Validation is reproducible and machine-readable, but it does not calculate
scores or enforce future Rules-as-Code.

## Alternatives considered

LLM review and embedded executable expressions were rejected for this step.
