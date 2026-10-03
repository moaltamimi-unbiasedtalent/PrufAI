# ADR-050: Human review checkpoints

Status: Accepted for MVP baseline

Context: Consequential AI proposals require human governance.

Decision: HumanReviewRequest in the domain database owns pending, approval, rejection and change-request decisions. Execution checkpoints only store resume position.

Consequences: Decisions are auditable, checksum-bound and protected against stale or duplicate actions.

Alternatives considered: Treating LangGraph checkpoint state as approval state was rejected.
