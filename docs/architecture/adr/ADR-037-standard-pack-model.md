# ADR-037: Standard Pack model

Status: Accepted for MVP baseline

## Context

Knowledge evidence and examination configuration need separate ownership and
versioning boundaries.

## Decision

Persist a stable `StandardPack` identity and a separate `StandardPackVersion`
with source bindings and a normalized blueprint hierarchy.

## Consequences

Pack configuration can be governed, diffed, exported, and reproduced without
copying protected source content. More tables are preferred over an opaque blob.

## Alternatives considered

Provider-specific code branches and a single unvalidated JSON document were
rejected because they weaken taxonomy, governance, and auditability.
