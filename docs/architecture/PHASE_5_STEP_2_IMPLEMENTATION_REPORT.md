# Phase 5 / Step 2 implementation report — Exam Studio

## Executive Summary

Exam Studio now creates governed, versioned assessments from one exact
StandardPackVersion and exact QuestionBankItemVersion bindings. It materialises
the existing module → section → task blueprint, validates completeness,
ownership, eligibility, rights, classification, duplicates and exact bound
RulesEngine results, then uses the existing HumanReviewRequest checksum
workflow before publication. Candidate runtime, sessions, responses, scoring,
grading, proctoring and automatic question generation remain out of scope.

## Schema and identity

Migration `0013_exam_studio` adds `exams`, `exam_versions`,
`exam_version_modules`, `exam_version_sections`, `exam_version_tasks`, and
`exam_version_item_bindings`. `Exam` is stable identity. `ExamVersion` owns
substantive composition and pins the exact `standard_pack_version_id`,
classification, lifecycle, effective dates, supersession, overrides, AI
provenance and checksum. All item bindings store exact ItemVersion IDs.

## Blueprint and assignment

Materialisation follows the existing Standard Pack hierarchy and is idempotent.
Child references are validated against the pinned version. Task cardinality is
derived from `min_items`/`max_items`; unresolved required slots block validation.
Assignment calls the existing Question Bank `eligible_bank` service with the
exact pack and task specification, then rechecks on the server. Cross-tenant,
cross-pack, inactive, rights-blocked and duplicate bindings are rejected.

## Governance and validation

Scopes are `PLATFORM_SHARED` and `ORGANISATION_PRIVATE` with database checks and
capability checks. Structural, blueprint, Question Bank, pack-compatibility,
rights, classification and Rules issues retain separate origins. Timing and
navigation overrides are currently unauthorized and block validation; they
cannot be smuggled into delivery through a version override. The exact
Standard Pack rule bindings are evaluated by the existing RulesEngine using a
narrow exam fact adapter. Blocking failures and errors prevent protected
transitions. Official certification is blocked because no explicit
authorization domain exists.

Review requests reuse HumanReviewRequest with `entity_type=EXAM_VERSION` and
the exact ExamVersion checksum. Composition changes recalculate the checksum,
so stale review cannot approve. Published versions are immutable; a corrected
composition is a new version and may explicitly supersede its predecessor.
Current resolution uses ACTIVE plus effective dates; as-of resolution preserves
historical versions.

## Scoring, timing and navigation

Raw Decimal item metadata remains available as a future scoring foundation;
provider weighting and transformations remain authoritative. Timing, response
mode, navigation and sequence are projected from the exact pack and no
candidate scoring or runtime timer is implemented.

## Manifests and security

The administrative manifest is permissioned and may include protected item
content for review. The delivery-safe manifest is separate and uses the
Question Bank delivery projection, excluding correct answers, scoring keys,
reviewer notes, AI critique, internal validation, legal notes and private object
URLs. No public candidate endpoint exists. Opaque media IDs may be carried for a
future authorized runtime resolver.

## AI proposals

AI proposals are DRAFT-only, carry AIRun/AIRunStep provenance, and contain exact
IDs that are server-revalidated through eligible-bank. Unknown or ineligible
IDs are rejected. Deterministic assembly gaps report missing slots; no question
generation is performed and AI cannot approve, publish or activate.

## API, RBAC and audit

The API provides bounded exam listing, version creation, blueprint
materialisation, eligible-item lookup, assignment/removal, validation, review,
lifecycle transitions, diff, administrative manifest, delivery preview and AI
proposal endpoints. Capabilities are `exam.read`, `exam.manage`, `exam.review`,
`exam.publish`, `exam.activate` and `exam.export`. Consequential actions use
the existing immutable audit service without writing answer keys or full
protected content to audit metadata. The implemented event set is
`exam.created`, `exam.version.created`, `exam.version.superseded`,
`exam.blueprint.materialized`, `exam.item.assigned`, `exam.item.removed`,
`exam.validation.completed`, `exam.review_requested`, `exam.approved`,
`exam.rejected`, `exam.published`, `exam.activated`, `exam.retired`,
`exam.exported` and `exam.ai_proposal.created`.

## Frontend

`/exam-studio` is a focused accessible administration surface for the governed
workflow. It displays workflow stages and explicitly defers eligibility and
validation to the server API. It is not a candidate UI.

## Tests and integration

The API suite includes checksum, server-side eligibility revalidation,
certification/rights and override gates, bound-rule blocking, review checksum
staleness, published immutability, effective-date resolution, scope isolation,
AI proposal boundaries and delivery-safe manifest assertions. The dedicated
PostgreSQL integration checks the migration head, all six Exam Studio tables and
the core scope/slot constraints. CI runs Alembic through the repository head,
then explicitly runs the Exam Studio integration alongside Question Bank,
Rules, AI, memory and prior regression gates; the Exam Studio test is not
silently skipped in CI if its PostgreSQL URL is missing. Local PostgreSQL/Redis-
dependent tests remain infrastructure skips when those services are unavailable.

## Known limitations

Full lifecycle fixture coverage against a live PostgreSQL service is CI-owned;
local validation cannot certify it without PostgreSQL. Import, non-trivial
provider scoring transformations, Candidate Runtime, sessions, responses,
grading, appeals, adaptive testing and official delivery are later phases.

The final local API run completed with 74 passed and 9 expected PostgreSQL/Redis
infrastructure skips. The skips are the configured PostgreSQL AI, Exam Studio,
memory, core/retrieval, Question Bank and Rules integration tests plus the two
Redis integration tests; they are not reported as application failures. AI tests
completed with 7 passed. Web lint, typecheck,
tests and production build passed. Ruff, mypy, Alembic heads/history and
offline SQL generation passed. `npm audit --omit=dev` reported one high and one
moderate PostCSS/Next advisory with only a breaking `next@16` forced fix; no
dependency upgrade was applied. Python audit was unavailable because
`pip_audit` is not installed.
