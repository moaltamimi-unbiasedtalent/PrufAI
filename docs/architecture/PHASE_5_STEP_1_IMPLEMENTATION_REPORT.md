# Phase 5 / Step 1 implementation report

## Scope delivered

The governed Question Bank is implemented without Exam Studio, Exam/ExamVersion,
candidate runtime, candidate responses, provider ingestion, psychometric
calibration, adaptive testing, or production item-generation workflows.

Schema tables are `question_bank_items`, `question_bank_item_versions`,
`question_bank_item_cefr_levels`, `question_bank_item_evidence_bindings`,
`question_bank_standard_pack_bindings`, and `question_bank_media_assets`.
`QuestionBankItem` is stable identity; all substantive content, answer keys,
scoring, rights and compatibility state belongs to `QuestionBankItemVersion`.

Lifecycle is `DRAFT`, `VALIDATION_REQUIRED`, `HUMAN_REVIEW`, `APPROVED`,
`PUBLISHED`, `ACTIVE`, `RETIRED`, `REJECTED`, and `SUPERSEDED`. Platform/shared
and organisation/private scope are enforced in service and database constraints.

Root families remain `LANGUAGE` and `INTEGRATION_KNOWLEDGE`. CEFR is normalized
as A1–C2 associations; LiD uses `INTEGRATION_KNOWLEDGE` plus
`LEBEN_IN_DEUTSCHLAND` and rejects CEFR. Typed content models cover
`MULTIPLE_CHOICE`, `EXTENDED_WRITING`, and `ORAL_RESPONSE`. Objective options
use stable IDs and answer keys reference IDs only.

Rights statuses are `PRUFAI_ORIGINAL`, `LICENSED`, `PUBLIC_DOMAIN`,
`THIRD_PARTY_RESTRICTED`, and `UNKNOWN`. Unknown/restricted rights cannot be
published; licensed content requires a license reference. Standard Pack
bindings store exact `standard_pack_version_id` and optional exact
`task_spec_id`; provider alignment never changes ownership or origin.

Validation is deterministic and separates `STRUCTURAL`, `RIGHTS`,
`PACK_COMPATIBILITY`, and `RULE` issues. Blocking rules, failed pack
compatibility, invalid answer keys, and rights failures block approval or
publication. Checksums are SHA-256 over canonical content, taxonomy, scoring,
rights, CEFR, evidence and pack references, excluding timestamps/audit fields.
Diff and normalized exact-duplicate fingerprint functions are non-LLM and do
not claim semantic plagiarism detection.

The administrative export is distinct from the delivery projection. The
delivery projection contains prompt/options but removes correct answer IDs,
scoring keys, reviewer notes and governance metadata. Export is permissioned,
bounded and contains no private storage URLs or linked Knowledge bodies.

## Verification

Local API validation: 63 passed, 8 expected infrastructure skips. The skips
are `test_ai_orchestration_postgresql_integration.py`,
`test_memory_postgresql_integration.py`, `test_memory_redis_integration.py`,
`test_postgresql_integration.py`, `test_postgresql_retrieval_integration.py`,
`test_question_bank_postgresql_integration.py`, `test_redis_integration.py`,
and `test_rules_postgresql_integration.py`; all are expected local service
availability skips.
AI validation: 7 passed. Frontend lint, typecheck, tests and build passed.
Ruff, mypy and Alembic offline/head validation passed; migration `0012_question_bank`
is linear from `0011_governed_memory` and is 19 characters.

The PostgreSQL integration test is explicitly invoked in CI after
`alembic upgrade head` and checks the migration head and Question Bank tables.
It is skipped locally because PostgreSQL/pgvector is unavailable. Remote CI,
live database execution, and npm audit remain unverified from this environment.

All fixtures are original synthetic material marked `TEST FIXTURE — NOT
OFFICIAL MATERIAL`. No Exam Studio or Phase 5 / Step 2 work is included.
