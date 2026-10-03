# Phase 8 / Step 1 Implementation Report

## Executive summary

PrüfAI now has an additive, versioned result-governance boundary for moderation and appeals. Existing result, scoring, writing, speaking, transcription, and object-storage records remain the evidence authorities.

## Schema and lifecycle

Revision `0021_result_governance` adds `result_appeals`, `result_appeal_versions`, `appeal_information_entries`, and `result_moderation_cases`. The appeal lifecycle is explicit: submitted, under review, information required, adjudicated (upheld/partially upheld/rejected), withdrawn, and closed. Stable `AssessmentResult` and immutable `ResultVersion` identities are preserved.

## Evidence and adjudication

The deterministic evidence bundle resolves the exact `ResultVersion`, `ScoringRun`, response snapshot, item scores, components, exam version, and standard-pack version. Its checksum is reproducible. Human adjudicators must present the appeal and evidence checksums; stale versions are rejected. AI appeal assistance is not enabled, so there is no autonomous decision path.

## Corrections, release, and certification

This step records the correction boundary and does not mutate scores or silently release a corrected result. Existing governed scoring must create new component/scoring/result versions. Appeal outcome and result outcome remain separate, and the existing official-certification guard remains authoritative.

## UX, security, privacy, and CI

Candidate and institution routes are live-backend surfaces with semantic forms and inert text rendering. Capability checks and tenant context are enforced server-side. Audit metadata excludes grounds, notes, assessment content, audio, transcripts, prompts, raw AI output, and private URLs. CI should run Alembic head and the Phase 8 migration-backed PostgreSQL suite with required integration flags, while preserving the pinned MinIO regression.

## Known limitations and Step 2 recommendation

Notification delivery, formal assignment scheduling, optional nonbinding AI summaries, and full governed correction orchestration remain intentionally out of scope. Step 2 may add correction execution only after this lifecycle is remotely validated; it must not add live speaking or autonomous adjudication.
