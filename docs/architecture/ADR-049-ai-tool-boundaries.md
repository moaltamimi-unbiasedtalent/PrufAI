# ADR-049: AI tool boundaries

Status: Accepted for MVP baseline

Context: Models must not enforce tenant, provider or rules policy themselves.

Decision: Expose only typed retrieval, exact Standard Pack reads, deterministic Rules evaluation and citation resolution. No shell, SQL, arbitrary HTTP or filesystem tool exists.

Consequences: Provider isolation and rules precedence are enforceable outside the model; some exploratory workflows require explicit future tools.

Alternatives considered: Generic function-calling was rejected.
