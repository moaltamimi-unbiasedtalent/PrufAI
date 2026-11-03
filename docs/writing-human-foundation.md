# Governed human Writing foundation

This prerequisite implements human marking and rubric activation for original
practice Writing. It does not execute an AI model or fabricate an AI proposal.
The assessment origin is explicit: new manual versions are `HUMAN`; historical
versions retain `UNSPECIFIED`. An AI-associated historical record is not evidence
that genuine model inference occurred. Existing AI proposal guards remain separate.

## Evidence and authority

The existing organisation permissions remain authoritative:

| Action | Required permission | Additional restriction |
| --- | --- | --- |
| Inspect rubric | `rubric.read` | Exact organisation scope |
| Request rubric review, approve, publish | `rubric.manage` | Approver differs from requester |
| Inspect Writing | `subjective.read` | Candidate cannot inspect another candidate through reviewer routes |
| Initialize and enter human criteria | `subjective.assess` | Submitted Writing; assessor is not the candidate |
| Approve, reject, request changes | `subjective.review` | Reviewer differs from criterion author and is not the candidate |

No new role or permission grants are introduced. A platform identity does not gain
institution marking authority merely by having platform permissions. Each write
checks the authenticated organisation, domain permission, entity ownership and
version state on the server.

Rubrics follow `DRAFT → HUMAN_REVIEW → APPROVED → PUBLISHED`. Review pins the
content checksum and records requester, deciding actor and time. Publication
requires that exact approved review. The rubric pins a StandardPackVersion and
Writing task specification, assessment family, language and skill. The new human
path also requires the exact rubric pinned by the exam item.

Human assessments follow `REVIEW_REQUIRED → IN_REVIEW → APPROVED`, or a preserved
`REJECTED` / `CHANGES_REQUESTED` decision. Reassessment creates a successor version;
it does not overwrite the decision. Initialization pins the submitted response
revision/checksum, exam/item binding and published rubric/checksum. Criterion
entry supplies every criterion exactly once, finite bounded points, an allowed
performance level, a bounded human rationale and exact response text spans.
Unicode evidence offsets use code points, consistent with the API.

Independent review approves the exact checksum-bound proposal. Human criteria
have no AI confidence; their confidence and AI step references are null. AI run,
model, prompt, agent and workflow references remain null and `ai_proposal` remains
empty. Human approval neither releases a result nor issues a statement or
certificate. The existing scorer can consume the approved subjective version;
the complete candidate-to-statement Writing journey remains a later Step 2A PR.

Every mutation accepts an `operation_id` UUID. Durable operation receipts bind
organisation, actor, action, target and request checksum in the same transaction
as the domain action and audit. Replaying the same authorized request is safe;
reusing its identity with a different payload or actor conflicts. Database row
locks and unique receipts protect concurrent execution. Stale checksums conflict.
Published rubric content and approved assessment/criterion content are immutable,
including nested evidence and attempted status-downgrade bypasses.

## Product routes and API

Institution navigation exposes **Writing rubrics** at `/institution/rubrics` and
the existing **Writing reviews** at `/institution/writing-reviews`. The rubric
detail is `/institution/rubrics/{rubricId}/versions/{versionId}`. The assessment
detail uses `/institution/writing-reviews/{assessmentId}`. Buttons reflect the
actual permissions, state and independent-review requirement; server checks
remain authoritative.

All routes below have prefix `/api/v1/organisations/{organisation_id}`:

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/rubrics` | Organisation rubric/version metadata |
| GET | `/rubrics/{rubric_id}/versions/{version_id}` | Exact rubric and review |
| POST | `/rubrics/{rubric_id}/versions/{version_id}/request-review` | Checksum-bound review request |
| POST | `/rubrics/{rubric_id}/versions/{version_id}/approve` | Independent approval |
| POST | `/rubrics/{rubric_id}/versions/{version_id}/publish` | Publish the approved version |
| GET | `/writing-review-options` | Submitted Writing and published rubric choices |
| POST | `/subjective-assessments/human` | Initialize exact human-origin evidence |
| POST | `/subjective-assessments/{version_id}/human-proposal` | Submit complete human criterion marks |
| POST | `/subjective-assessments/{version_id}/human-approve` | Independent approval |
| POST | `/subjective-assessments/{version_id}/human-decision` | Reject or request changes with a reason |

Domain audit events use `rubric.review_requested`, `rubric.approved`,
`rubric.published`, `subjective_assessment.human_initialized`,
`subjective_assessment.human_proposed`, `subjective_assessment.human_approved`,
`subjective_assessment.human_rejected` and
`subjective_assessment.human_changes_requested`. Audit stores identifiers,
operation identity and hashes, not candidate text, rubric content, rationale,
criterion marks or evidence quotes. The Control Centre Audit view exposes its
existing safe projection; it does not grant access to institution evidence.

## Original development fixture

Use the existing development-only seed documented in [local-demo.md](local-demo.md).
It adds `writing.candidate@demo.prufai.local`, display name **Human Writing Practice
Candidate**, and **Human B1 German Writing Practice — SYNTHETIC DEMO DATA — NOT
OFFICIAL MATERIAL** (`human-writing-b1-v1`). The local-only password follows the
existing seed default or the operator's `PRUFAI_DEMO_PASSWORD` override.

The original LANGUAGE/de/B1/PRACTICE task asks the candidate to write a course
absence message. The rubric has `TASK` and `LANGUAGE`, each worth up to three
points, and an explicit sum out of six. It invents no official pass threshold or
provider qualification. Its internal practice-policy document is explicitly
seed metadata, not an uploaded source or ingestion job.

The seed leaves the rubric **DRAFT** and the new candidate unassigned, with no
response, assessment, AI run or result. Through normal authenticated product
actions:

1. Admin opens Writing rubrics and requests review of `human-writing-b1-v1`.
2. Reviewer approves and publishes that exact rubric.
3. Admin assigns the original Writing exam to Human Writing Practice Candidate.
4. Candidate starts, saves, reloads and submits the response.
5. Reviewer initializes the human assessment, enters each criterion's points,
   level, exact quote and rationale, then submits for independent review.
6. Admin inspects the pinned response/rubric and approves or records a reasoned
   rejection/changes decision. The criterion author cannot approve their proposal.
7. Platform Admin can inspect safe governance audit metadata, without candidate
   text or institution marking authority.

Reseeding preserves completed work. Use a fresh dedicated PostgreSQL database for
a repeat walkthrough; do not reset or truncate developer databases.

## Schema and verification

Migration `0025_writing_human_review` follows `0024_result_release`. It adds explicit
assessment origin, nullable human criterion confidence and the durable operation
receipt table. Existing historical origins remain `UNSPECIFIED`. Downgrade refuses
to discard human criterion history that cannot fit the former non-null confidence
column. Resolve the current head dynamically with Alembic; do not hard-code this
revision as a permanent head expectation.

Run the required real PostgreSQL gate from `services/api` against a dedicated
PostgreSQL/pgvector database:

```sh
DATABASE_URL="$POSTGRES_INTEGRATION_URL" python3 -m alembic upgrade head
PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 \
  python3 -m pytest tests/test_writing_foundation_postgresql_integration.py -v
```

The test database must come from Alembic, not `Base.metadata.create_all()`. Missing
PostgreSQL configuration in required mode fails instead of skipping. The suite
uses normal authentication and real services for publication, human approval,
replay/concurrency, ownership, invalid criteria and immutable evidence.

The existing production-build browser runner discovers the original three
integrated objective tests and `tests/browser/writing-human.spec.cjs`. Install its
pinned acceptance tool separately, as documented in the integrated browser guide,
then use a fresh dedicated database:

```sh
DATABASE_URL=postgresql+psycopg://USER@HOST:PORT/FRESH_DATABASE \
PRUFAI_BROWSER_TOOL_DIR=/absolute/path/to/isolated/browser-tool \
  bash scripts/run-integrated-browser.sh
```

The runner applies migrations, reconciles synthetic demo data and starts the real
API with a production Next.js build. The new test performs actual authenticated
rubric review/publication, assignment, Writing save/resume/offline account
isolation, criterion entry and independent approval. It records screenshots and
checks safe audit visibility. It does not mock successful API responses or create
AI records. This text-only browser gate uses the runner's existing explicit
development memory object-storage configuration; separate required CI MinIO tests
remain the evidence for real object storage. Human Writing uses neither Redis
jobs nor a background worker; existing Redis regressions remain in CI.

AI execution, provider smoke inference and an AI worker are **not implemented or
verified by this human-only prerequisite**. Speaking Step 2B, automatic approval,
release, certification and automatic merge remain outside this PR.
