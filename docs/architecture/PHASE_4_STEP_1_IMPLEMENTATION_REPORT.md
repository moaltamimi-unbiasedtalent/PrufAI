# Phase 4 / Step 1 Implementation Report

## Executive summary

Added governed AI registries, exact version provenance, bounded provider-neutral orchestration, constrained tools, structured outputs, synthetic deterministic execution and domain-owned human review records. No memory, question generation, exam runtime, scoring or certification decision was added.

## Schema and migration

Migrations `0009_ai_orchestration` and `0010_ai_checkpoints` add model profiles, prompt definitions/versions, agent definitions/versions, workflow definitions/versions, AI runs/steps, durable workflow checkpoints and HumanReviewRequest. Published artifacts are immutable by service guard; checksums cover substantive configuration. Revision lengths are 20 and 19 characters.

## Runtime and safety

LangChain Core `0.3.79` and LangGraph `0.6.7` are pinned for the AI worker boundary. The deterministic test model is network-free and explicit. Tools are allow-listed, retrieval scopes are enforced before model access, Standard Pack reads require exact versions, and Rules results retain precedence. Pydantic models reject malformed or extra output fields.

## Human governance

AI runs enter `WAITING_FOR_HUMAN` and create a domain HumanReviewRequest. Approval, rejection and change requests update business state; checkpoint state is only a computation resume cursor. Review checksums prevent stale approval and status guards prevent double decisions.

## Validation and limitations

Local AI tests, API tests, lint and targeted mypy are available. PostgreSQL and remote CI remain external gates. A full production provider call is intentionally not exercised in CI. Memory governance is deferred to Step 2; Question Bank and Exam Studio remain Phase 5.

## Final governance closure details

- Production checkpoint backend: PostgreSQL `ai_workflow_checkpoints`; test execution uses explicit in-memory state only.
- Checkpoint keys include run ID, workflow-version ID and optional organisation ID. Checkpoints cannot change HumanReviewRequest state, and terminal runs cannot resume.
- Retention is service-level: active waiting checkpoints are retained; cancelled/completed/non-active checkpoints are cleanup candidates; orphan handling is documented rather than scheduled in this MVP.
- Provider retry policy is bounded to three attempts per call. Structured output is bounded to two attempts. Tool limits are three calls per step, twelve per run and twenty retrieval results per call.
- Cancellation is terminal and idempotent. Review decisions and resume state are guarded against stale checksums and duplicate decisions.
- HITL audit hooks cover review requested, approved, rejected, changes requested and cancelled; run telemetry remains in AIRun/AIRunStep rather than duplicating payloads in audit metadata.
- API and AI currently communicate as separately executable boundaries; this step does not introduce an HTTP cross-service call. No caller-supplied tenant privilege is trusted by platform routes.

## Audit lifecycle closure

`AIRun`/`AIRunStep` remain technical execution provenance. `AuditService` records consequential lifecycle evidence using identifiers, statuses and checksums only. Actual lifecycle event names are:

- `ai.run.created`
- `ai.workflow.run.started`
- `ai.workflow.run.completed`
- `ai.workflow.run.failed`
- `ai.workflow.run.cancelled`
- `ai.workflow.resumed`
- `ai.review.requested`
- `ai.review.approved`
- `ai.review.rejected`
- `ai.review.changes_requested`
- `ai.review.cancelled`

Approval leaves a waiting run in a resumable domain state; only the authorized, checksum-validated resume transition emits `ai.workflow.resumed`, and terminal completion is emitted separately. Duplicate resume and review decisions are rejected. Audit metadata does not contain prompts, model outputs, source text, checkpoint blobs or secrets.
