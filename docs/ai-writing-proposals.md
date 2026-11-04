# Step 2B.4B.1: governed Writing proposal execution

This is an operational foundation for non-final AI-assisted Writing proposals.
The existing HUMAN_ONLY Writing workflow remains independent. No worker approves
criterion marks, creates a ScoringRun, releases results or changes official
certification authority. Recorded Speaking proposals are outside this change.

## Sources of truth

The existing `AIModelProfile`, `PromptVersion`, `AgentVersion` and
`AIWorkflowVersion` records remain the registry. The model record has no separate
version table; its exact content-addressed `model_version` includes model identity,
configuration and creator. The execution profile is a content-addressed bundle
over the four existing records, keyed by the exact workflow-version UUID.
`app/writing_execution/registry.py` computes these identities using the existing
Writing canonicalization convention, while validating legacy registry checksums
with the original AI orchestration convention. Published registry content cannot
be edited or deleted through ORM services. Status revocation closes execution.

The published `RubricVersion.ai_assessment_policy` must explicitly contain
`enabled: true`, `mode: AI_ASSISTED`, `execution_profile_id` and the exact
`execution_profile_version`. The human-only policy continues to use
`enabled: false`, `mode: HUMAN_ONLY`. An unavailable executor never selects a
human-only fallback.

`WritingExecutionJob` is a durable execution receipt, not another assessment root.
It pins organisation, candidate, attempt, ExamVersion/item binding,
QuestionBankItemVersion, submitted response/revision/checksum, Standard Pack,
published rubric/checksum, exact model/prompt/agent/workflow snapshots, registry
approval identity, operation UUID and AIRun identity. Current eligibility and
current registry/deployment approval are rechecked before provider work and before
completion. A retry uses these pins; it never substitutes a newer version.

`AIRun`/`AIRunStep` remain the run and attempt evidence. Generic AI completion,
checkpoint and review APIs cannot bypass this domain path. The worker completes
the validated run and invokes the existing `SubjectiveAssessmentService.propose`
inside the same database transaction. That service independently requires a
completed, matching job/run/step receipt and revalidates the proposal before
creating `AI_PROPOSED` and a pending `HumanReviewRequest`. Duplicate delivery cannot
create another proposal or review. No run is seeded or labelled complete without
worker execution.

The actual workflow is INPUT → PROPOSAL → RULE_CHECK → HITL. It uses one provider
call with no tools. These stages are not independent assessor models. No retrieval,
citations, additional tool execution or calibrated confidence is asserted.

## Registry and deployment approval

An actual platform actor with current `ai.manage` may create the four DRAFT records
using `POST /api/v1/platform/ai/writing-execution-profiles`. The request contains
code/name, provider/model, safe configuration and approved prompt templates.
An independent platform actor with current `ai.manage` and `ai.review` approves
the four exact UUIDs and `expected_profile_version` using
`POST /api/v1/platform/ai/writing-execution-profiles/approve`. Self approval, stale
fingerprints and incompatible schemas/workflows are denied. This records an
append-only `writing_execution.profile_approved` event. API metadata responses
exclude prompt content and configuration payloads.

The supported genuine adapter is `OPENAI`, dated model
`gpt-5.4-mini-2026-03-17`, adapter `1`, Responses structured output. It accepts
only `https://eu.api.openai.com/v1/responses`, region `EU`, no tools, and the fixed
credential reference `PRUFAI_WRITING_OPENAI_API_KEY`. Its configuration requires:

- `schema_version: "1"`, `adapter_version: "1"`, exact endpoint and region;
- safe `deployment_id` (OpenAI project), `model_configuration_version` and
  `processing_approval_reference`;
- `credential_reference: "PRUFAI_WRITING_OPENAI_API_KEY"`;
- `processing` containing safe references for legal basis, consent policy, data
  processing agreement, residency approval, retention policy, logging/training
  controls, candidate-data transfer authorization and data minimization;
- optional `max_output_tokens`, bounded from 1024 through 8192 (default 4096).

Exact processing-reference field names are in
`app/writing_execution/runtime.py:PROCESSING_REFERENCES`. References must identify
actual approved arrangements; the implementation cannot establish legal approval.
The EU endpoint alone grants none. The request uses `store: false`; this does not
claim universal zero retention, legal compliance or a provider training policy.

Separately approve the exact bundle fingerprint in the API and worker deployment
using `WRITING_APPROVED_PROFILE_FINGERPRINTS='["<exact SHA256>"]'`, and provide the
credential from the deployment secret manager. Never put credentials in registry
JSON, requests, seed data, logs or Git. The setting defaults to an empty allowlist;
missing approval, processing configuration or credentials reports UNCONFIGURED.
There is no provider fallback. Do not enable external processing for real candidate
text without approved arrangements. No genuine-provider smoke was performed by
the technical suite.

Official adapter references: [model snapshot and structured-output support](https://developers.openai.com/api/docs/models/gpt-5.4-mini),
[structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[processing and residency controls](https://developers.openai.com/api/docs/guides/your-data).
These describe transport capabilities, not Writing scoring quality.

## Worker and institution use

Use the repository's existing PostgreSQL environment and apply additive migrations:

```sh
cd services/api
python3 -m alembic upgrade head
python3 -m app.writing_execution.worker
# Bounded technical/manual consumption of at most one eligible job:
python3 -m app.writing_execution.worker --once
```

API and worker need the same database and runtime approval configuration.
`WRITING_PROVIDER_TIMEOUT_SECONDS` defaults to 60 and is bounded from 1 to 120.
The worker uses atomic `FOR UPDATE SKIP LOCKED` claims, renewable leases, fencing
tokens, bounded retries and explicit cancellation. A failed/expired delivery cannot
complete a superseded claim. Provider output and final handoff commit atomically;
retry cannot leave a false completed run. Safe failure categories omit provider
bodies and candidate payloads.

An actual current institution membership with `subjective.assess` can request
`POST /api/v1/organisations/{org}/writing-executions` with exact evidence/profile
identities and an operation UUID. Scoped options, job listing/detail, retry and
cancel endpoints share this prefix. A platform identity alone grants no tenant
access. Candidate self-assessment and cross-tenant replay are denied.

The existing `/institution/writing-reviews` page displays execution mode and exact
state. The assessment detail displays original Writing, rubric, criterion
proposals, bounded evidence references and actual execution provenance. Human
review requires the existing review authority, exact checksum, operation UUID,
bounded reason and a complete explicit set of criterion decisions. Choices start
blank; the UI cannot force a blind copy of AI marks. Published separation-of-duties
policy remains enforced. Approved successors preserve the original proposal.

Control Centre AI Operations exposes `writing-proposals`: whitelisted job state,
profile/workflow/model versions, retry count, timestamps, safe failure category and
human-review status. It excludes candidate text, prompt snapshots, private provider
payloads and claim/credential tokens. System status describes configuration
availability only; CONFIGURED or TEST_ONLY does not assert worker health or a
successful provider call.

## Evidence safety and technical acceptance

Candidate/task content is untrusted data in a user input JSON document, never
interpolated into system instructions. Strict JSON schema is followed by domain
validation of exact run/source/rubric identity, complete known criteria, Decimal
mark bounds, permitted levels, rationale, exact Unicode quote offsets and limits.
Unknown/missing criteria, stale checksums, forged evidence, unsupported mode,
arbitrary tools or extra output properties fail before proposal creation.
This establishes structural constraints, not a universal semantic prompt-injection
guarantee or examiner accuracy.

The bounded foundation supports candidate-text evidence. A required source-backed
rubric fails closed with SOURCE_BACKED_WRITING_UNCONFIGURED; it does not invent
retrieval or source citations. The technical demo retains its required normative
Standard Pack source binding and a non-required rubric source binding, explicitly
selecting `source_backed: false` while requiring exact candidate evidence.

`DETERMINISTIC_TEST` / `writing-fixture-v1` is available only with
`WRITING_TEST_ADAPTER_ENABLED=true` in explicit automated test configuration.
Development additionally requires both guarded `PRUFAI_DEMO_ENABLED=1` and
`PRUFAI_E2E_ALLOW_DEMO=1`. Production rejects activation. Its registry qualification
is labelled AUTOMATED_TECHNICAL_TEST, cannot be manually approved as genuine AI and
never authorizes external processing. It is never an automatic fallback.

The official development seed creates a separate AI-assisted synthetic candidate,
exam, policy and rubric prerequisites, with no attempt, execution, proposal, score,
result or release. The complete browser runner retains every earlier objective,
HUMAN_ONLY Writing/Speaking and transcription journey and adds one TEST ONLY Writing
journey. The candidate really submits LONG_TEXT; the institution really queues the
job; a separate worker process executes it; the reviewer sees the pending proposal.
The journey stops without approval or release.

The required `test_writing_execution_postgresql_integration.py` suite uses fresh
Alembic-migrated PostgreSQL, never `Base.metadata.create_all()`. CI asserts positive
execution and zero required skips. It tests requests, claims/fencing, process
crashes/retries, exact pins, strict output, atomic handoff, human review,
immutability and tenant isolation. Separate real MinIO, Redis and prior domain
regressions remain required. HTTP unit tests are transport-contract evidence only.

`tests/fixtures/writing_execution_safety.json` is a small synthetic technical safety
set. It does not establish German Writing scoring accuracy, CEFR reliability,
fairness, examiner agreement or a quality release gate. Authentic AI readiness
requires a separately approved real-provider smoke through this exact workflow.
Absent that execution, report REAL PROVIDER SMOKE = NOT RUN and AUTHENTIC AI
VERIFIED = NO.
