# ADR-048: LangGraph orchestration boundary

Status: Accepted for MVP baseline

Context: Multi-step proposals need bounded branching and pause/resume.

Decision: Use a symbolic, bounded LangGraph-compatible runtime for execution; PostgreSQL domain records own business state.

Consequences: Computation can resume safely without turning checkpoints into approval truth. Graph definitions require validation and explicit node mappings.

Alternatives considered: Synchronous request chains and database-stored callables were rejected for safety and durability reasons.
