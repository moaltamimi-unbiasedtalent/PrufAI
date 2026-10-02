# ADR-045: Rule evaluation semantics

Evaluation is fail-closed: missing required facts and type mismatches are `ERROR`; boolean predicates produce `PASS` or `FAIL`; optional existence checks are explicit. Decimal arithmetic is used for numeric aggregation and comparisons.
