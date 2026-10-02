# ADR-043: Safe declarative rule DSL

Rules use a small JSON AST with allow-listed operators and fact roots. Complexity limits and rejection of executable constructs make evaluation deterministic and bounded.
