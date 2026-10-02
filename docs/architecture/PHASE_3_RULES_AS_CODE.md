# PrüfAI Phase 3 — Rules-as-Code

Phase 3 Step 2 introduces a deterministic, declarative Rules-as-Code foundation. A rule is a versioned platform artifact; normative meaning remains in the Knowledge layer and assessment structure remains in Standard Packs.

```text
Knowledge document version (evidence + checksum)
                 │
                 ▼
Rule Definition ── Rule Version ── Standard Pack Rule Binding
                        │                    │
                        ▼                    ▼
                 bounded DSL engine     pack validation report
```

## Boundaries

The generic engine evaluates structure, timing, task/count, classification, scoring and result predicates. It does not contain provider conditionals, question authoring, agents, official certification logic, or a scoring UI. `OFFICIAL_CERTIFICATION` is a platform authorization guard and always fails closed.

Rules use JSON expression schema `1`. Operators are `ALL`, `ANY`, `NOT`, `EQ`, `NE`, `LT`, `LTE`, `GT`, `GTE`, `IN`, `NOT_IN`, `EXISTS`, `NOT_EXISTS`, `COUNT`, `SUM`, and `BETWEEN`. Fact paths are allow-listed dotted paths rooted at `pack`, `module`, `section`, `task`, `exam`, `score`, or `result`.

Missing required facts and type mismatches produce `ERROR`; existence operators are the explicit safe way to branch on optional facts. Expressions are bounded to 20 levels, 100 nodes, 100 list items, and 1,000-character strings. No `eval`, `exec`, imports, callbacks, templates, or arbitrary code are accepted.

Every result records rule identity, version, checksum, category, severity, status (`PASS`, `FAIL`, `ERROR`, `SKIPPED`), failure metadata, evidence references and a bounded trace. Numeric aggregation uses `Decimal` conversion. Evidence bindings require an exact Knowledge Document Version and preserve its checksum; normative evidence must be approved or historically approved.

Rule versions move through draft, validation, review, approval, publication and activation. Published/active/retired/superseded versions are immutable. Checksums cover expression, failure configuration, dates, tests and evidence. Pack bindings are explicit, ordered, required/optional, and cannot mutate a published pack. Historical resolution is date-aware and retains the pinned rule version.

Standard Pack validation preserves structural issues with `STRUCTURAL` origin and appends rule results with `RULE` origin. Required blocking/error rule failures fail validation; optional failures warn. RBAC permissions and audit events are platform-scoped.

Synthetic fixtures are marked `TEST FIXTURE — NOT OFFICIAL PROVIDER RULE` and are not production standards.
