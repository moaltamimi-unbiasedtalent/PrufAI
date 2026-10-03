# Phase 8 / Step 1 — Result Governance

## Scope

This step adds result moderation, version-pinned candidate appeals, deterministic evidence bundles, and human-only adjudication after a result exists. It does not add live speaking, autonomous appeal decisions, or official certification issuance.

## Lifecycle and evidence

`AssessmentResult` remains the stable root. `ResultVersion` and `ScoringRun` are never rewritten. An appeal stores the exact challenged result version, scoring run, exam version, standard-pack version, and checksums. `ResultAppealVersion` is append-only. `ResultEvidenceBundle` resolves those historical rows directly and hashes canonical metadata, so a later current result cannot silently replace the challenged evidence.

Appeal eligibility is policy-driven. The MVP default permits released results only and has no invented provider deadline. A configured window is validated when supplied. Appeal outcomes are separate from result outcomes.

Adjudication locks the appeal row with PostgreSQL `SELECT ... FOR UPDATE` and verifies the expected appeal-version and evidence checksums inside the transaction. A second reviewer using stale state receives a conflict. Idempotency is checked after the row lock.

## Authority and security

Moderation and appeal actions use capabilities (`result.moderate`, `appeal.read`, `appeal.adjudicate`, and `appeal.correct`) and explicit tenant context. Candidate projections are ownership-scoped and omit reviewer notes, raw evidence, prompts, and AI output. Candidate and reviewer text is rendered as inert text. Audit records contain identifiers, checksums, states, and decision categories only.

AI assistance is intentionally not implemented in Step 1. Human adjudication is mandatory and cannot enter an arbitrary score. Upheld appeals create a durable `ResultCorrection` linked to the exact appeal version and source checksum. Writing and speaking reassessment uses the existing versioned subjective correction path and existing scoring for a new `ScoringRun` and `ResultVersion`. Historical artifacts remain retained and corrected results are not automatically released. The certification guard remains unchanged.

## Concurrency and idempotency

Consequential creation and decisions require operation IDs. Appeal and evidence checksums are supplied on adjudication, so a stale reviewer is rejected. Appeal history, information entries, release history, and superseded result versions remain retained.

## UI and limitations

The candidate appeal surface is `/candidate/results/appeal`. Institution queues are `/institution/result-moderation` (API-backed moderation queue) and `/institution/appeals`, with `/institution/appeals/[appealId]` for the exact evidence workspace. The MVP does not build notification delivery, workforce scheduling, or formal accessibility/certification claims.

## Validation

Phase 8 integration is migration-backed by Alembic revisions `0021_result_governance` and `0022_result_correction`; no `Base.metadata.create_all()` is used by production migration paths. Existing object-storage and speaking evidence integrations remain unchanged.
