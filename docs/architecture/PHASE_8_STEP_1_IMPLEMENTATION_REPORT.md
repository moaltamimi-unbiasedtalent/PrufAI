# Phase 8 / Step 1 Implementation Report

## Executive summary

PrüfAI now has an additive, versioned result-governance boundary for moderation and appeals. Existing result, scoring, writing, speaking, transcription, and object-storage records remain the evidence authorities.

## Schema and lifecycle

Revisions `0021_result_governance`, `0022_result_correction`, and
`0023_objective_correction` add the appeal/moderation tables, durable correction
linkage, and immutable objective replacement bindings. The appeal lifecycle is
explicit: submitted, under review, information required, adjudicated
(upheld/partially upheld/rejected), withdrawn, and closed. Stable
`AssessmentResult` and immutable `ResultVersion` identities are preserved.

## Evidence and adjudication

The deterministic evidence bundle resolves the exact `ResultVersion`, `ScoringRun`, response snapshot, item scores, components, exam version, and standard-pack version. Its checksum is reproducible. Human adjudicators must present the appeal and evidence checksums; stale versions are rejected. AI appeal assistance is not enabled, so there is no autonomous decision path.

The appeal row is locked during adjudication. The current version/checksum and evidence checksum are checked while holding the PostgreSQL row lock, which makes conflicting reviewer submissions deterministic. Repeated operation identities are idempotent; conflicting identities receive a domain conflict.

## Corrections, release, and certification

`ResultCorrection` is the durable correction boundary. Human authorization is required. Writing and speaking correction calls the existing `SubjectiveAssessmentService.correct` path and then `ScoringService.score`, producing new immutable component/scoring/result versions. Transcription correction delegates to an explicit factory over the existing transcription service and preserves the historical transcript and source audio. Objective correction requires a same-stable-item approved replacement binding and reuses the deterministic scorer; arbitrary score overrides fail closed. No historical score is edited and no corrected result is auto-released. Appeal outcome and result outcome remain separate, and the existing official-certification guard remains authoritative.

## UX, security, privacy, and CI

Candidate and institution routes are live-backend surfaces with semantic forms and inert text rendering. Capability checks and tenant context are enforced server-side. Audit metadata excludes grounds, notes, assessment content, audio, transcripts, prompts, raw AI output, and private URLs. CI runs Alembic head, governance schema, and PostgreSQL row-lock integration checks with required integration flags, while preserving the pinned MinIO regression.

## Known limitations and Step 2 recommendation

Notification delivery, formal assignment scheduling, optional nonbinding AI summaries, and fixture-level correction-chain execution on CI services remain limitations. Step 2 must not add live speaking or autonomous adjudication.
