# ADR-034: Retrieval Golden Dataset

Status: Accepted for MVP baseline

## Context

Retrieval regressions require stable reviewed cases without copying protected
provider material.

## Decision

Version a synthetic golden dataset and keep future production defects in a
separate dynamic regression dataset. Cases declare scope, expected evidence,
forbidden authorities, date, tags, and review state.

## Consequences

CI remains deterministic and legally safe; expert-authored real-standard cases
can be added later under separate review.

## Alternatives considered

Auto-mutating golden data from production feedback was rejected.
