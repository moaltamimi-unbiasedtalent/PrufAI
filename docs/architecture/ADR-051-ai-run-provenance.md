# ADR-051: AI run provenance

Status: Accepted for MVP baseline

Context: Later evaluation and audit need exact model, prompt, agent, workflow, evidence, pack and rule references.

Decision: AIRun and AIRunStep persist version IDs, checksums, retrieval/rule references, token usage, latency and failure metadata without duplicating source documents.

Consequences: Historical analysis is possible while minimizing copyrighted or sensitive raw content storage.

Alternatives considered: Storing only final prose was rejected as non-reproducible.
