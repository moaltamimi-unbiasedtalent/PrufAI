# Integrated MVP Step 2A: human-governed Writing

This journey connects the Candidate App, Institution Portal and read-only
Control Centre using the human-only Writing foundation merged in PR #23. It uses
one original German B1 LANGUAGE/PRACTICE task. No AI proposal, provider call,
worker, transcript or Speaking assessment is created or implied.

The original content is labelled **SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL**.
There is no invented provider threshold. With no governed outcome rule, the
result correctly remains `UNDETERMINED`.

## Existing integration points

`O` is `/api/v1/organisations/{organisation_id}`, `C` is `/api/v1/candidate` and
`P` is `/api/v1/platform/control-center`.

| Action | Existing service/model | API | Product surface |
| --- | --- | --- | --- |
| Inspect exact Standard Pack | `standard_packs/service.py`, StandardPackVersion | `P/standards?kind=versions` | `/control-center/standards` |
| Activate exact rubric | `writing_assessment/governance.py`, RubricVersion and HumanReviewRequest | `O/rubrics/{rubric}/versions/{version}/{request-review,approve,publish}` | `/institution/rubrics` and version detail |
| Assign published Writing exam | `runtime/service.py`, CandidateExamAssignment | `POST O/exam-assignments` | `/institution/assignments` |
| Save, resume, submit Writing | `runtime/service.py`, CandidateResponseRevision | `C/exam-attempts/{attempt}/{runtime,submit}`, `PUT .../responses/{binding}` | `/candidate/exams/{attempt}` |
| Initialize human assessment | `HumanWritingService`, SubjectiveAssessmentVersion | `POST O/subjective-assessments/human` | `/institution/writing-reviews` |
| Mark and independently approve | `HumanWritingService`, SubjectiveCriterionAssessment and HumanReviewRequest | `O/subjective-assessments/{version}/{human-proposal,human-approve,human-decision}` | `/institution/writing-reviews/{assessment}` |
| Score exact approved evidence | `scoring/service.py`, ScoringRun, ItemScore and ResultVersion | `POST O/exam-attempts/{attempt}/score` | Exact-attempt link to `/institution/overview#attempt-{attempt}` |
| Review result separately | `ScoringService`, result checksum-bound HumanReviewRequest | `O/result-versions/{version}/{request-review,approve}` | `/institution/results/{result}` |
| Release approved result | `result_release/service.py`, ResultRelease | `POST O/result-versions/{version}/release` | Result detail, then `/institution/releases/{release}` |
| Issue and read practice statement | `ResultReleaseService`, StatementOfResults | `POST O/releases/{release}/statement`, `C/releases/{release}/statement` | Institution release detail and `/candidate/results/releases/{release}` |
| Inspect safe audit | Existing audit writers and ControlCentreService | `P/audit?event_type=...` | `/control-center/audit` |

No parallel assessment architecture or new migration is introduced. The head is
resolved dynamically by Alembic; PR #23's `0025_writing_human_review` is the
starting schema, rather than a permanent hard-coded head expectation.

## Governance and evidence

Human marking requires a rubric that explicitly opts into `HUMAN_ONLY`. An
AI-assisted policy is not silently replaced. The rubric must follow actual
`DRAFT → HUMAN_REVIEW → APPROVED → PUBLISHED` transitions, with different
requester and approver.

The assessor initializes the exact submitted response and published rubric.
Every criterion requires bounded points, a permitted level, a human rationale
and any required exact text spans. A different authorized human approves the
checksum-bound proposal. Candidates, foreign tenants and generic AI-review
endpoints cannot grant that approval.

Before subjective approval, scoring produces an `INCOMPLETE` result that cannot
enter result approval or release. Institution inspection permits a controlled
**Rescore incomplete attempt** after marking is complete. Scoring consumes the
current approved assessment, verifies its exact response/item/task/rubric pins,
complete criterion values and aggregate, and records its identity/checksum.
Retries with unchanged evidence return the existing ScoringRun. Changed approved
evidence creates a new run/result version; an older incomplete result remains.

Safe component provenance is recorded with ItemScore and ScoringComponent and
projected as `assessment_components`. It contains the skill, HUMAN origin,
assessment version/checksum, rubric version/checksum, submitted revision/checksum,
task reference and approved component score. It excludes candidate text,
rationale, quotes, reviewer-private notes and AI confidence. Scoring, release and
statement views use the persisted exact run rather than a newly selected live
assessment. Historical statements are not rewritten to add new fields.

New statements include practice classification, raw score, outcome, issuer,
issue time, safe assessment components and exact release/result/run/pack/attempt
provenance inside the checksummed payload. They are Statements of Results, not
official Goethe, telc or DTZ certificates. The candidate UI renders a selected
safe summary and verifies that statement references match the exact release.

Existing result review and release remain separate authorized actions. New
release and statement operations use their existing replay/stale-version checks.
Approved criterion rows, nested evidence and issued artifacts remain immutable.
The main browser journey does not require a correction. PostgreSQL acceptance
also exercises the existing upheld-appeal Writing reassessment path: an authorized
reviewer distinct from the original human assessor can review and correct those
marks, producing an approved successor, a new ScoringRun and a new ResultVersion.
That path validates the exact appeal/correction/source/review lineage rather
than accepting an arbitrary human successor. Original evidence and issued
statement payloads remain; a successor is not automatically released or issued
a new statement.

## Original development walkthrough

Use the existing non-destructive setup in [local-demo.md](local-demo.md). A fresh
dedicated PostgreSQL/pgvector database is required for a fresh walkthrough. The
development seed preserves completed attempts and does not reset developer data.

The seed supplies **Human Writing Practice Candidate**
(`writing.candidate@demo.prufai.local`) and **Human B1 German Writing Practice —
SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL**, version `human-writing-b1-v1`.
Its rubric starts DRAFT; no Writing response, assessment, score or result is
seeded. Accounts use the documented local-only password or the operator's
`PRUFAI_DEMO_PASSWORD` override.

1. **Admin:** open Writing rubrics, inspect the original task/rubric metadata and
   request review of `human-writing-b1-v1`.
2. **Reviewer:** approve and publish that exact rubric. The requester cannot
   approve their own request.
3. **Platform Admin:** inspect the exact Writing Standard Pack/version in
   Standards & packs. Platform inspection does not grant institution marking.
4. **Admin:** assign the published Writing exam to Human Writing Practice
   Candidate through Assign practice assessment.
5. **Candidate:** prepare/start the exam, read the original task, write a
   response, save revision 1, edit/save revision 2, reload/resume, then submit.
   Submission is read-only. Logout clears any unsynchronized account-specific
   offline draft before another account signs in.
6. **Reviewer A:** open Writing review, select the submitted revision and its
   exact published rubric, initialize HUMAN assessment, enter all criterion
   judgments and submit for independent review.
7. **Admin / Reviewer B:** inspect the response, rubric and criteria, then
   approve the exact assessment. The criterion author cannot approve it.
8. Follow **Inspect submitted attempt and scoring →**. Score the attempt; if an
   earlier run is incomplete, use **Rescore incomplete attempt**. Inspect the
   exact approved HUMAN component and new ScoringRun/ResultVersion.
9. Request human result review, approve the reviewed result, then separately
   release it. In release detail choose **Issue Statement of Results**.
10. **Candidate:** open released results, then the result and statement. Inspect
    Writing/HUMAN origin, practice classification, raw points, issue time,
    issuer, checksums and exact provenance. Another candidate cannot read it.
11. **Platform Admin:** filter Audit evidence for `rubric.published`,
    `subjective_assessment.human_initialized`, `subjective_assessment.human_proposed`,
    `subjective_assessment.human_approved`, `scoring.run.completed`,
    `result.version.created`, `result.release_created`, `result.released` and
    `statement.created`. These views exclude the Writing response and private
    assessment rationale/quotes.

## Required acceptance

From `services/api`, against an Alembic-upgraded dedicated real PostgreSQL
database:

```sh
DATABASE_URL="$POSTGRES_INTEGRATION_URL" python3 -m alembic upgrade head
PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 \
  python3 -m pytest tests/test_integrated_writing_postgresql_integration.py -v
```

Missing required PostgreSQL configuration fails rather than skips. Integration
fixtures use migrations and real services, not `Base.metadata.create_all()` or
direct insertion of an assessed outcome. The existing required foundation suite
also runs, retaining rubric, criterion, evidence, stale-operation, independent
review and real connection-contention coverage.

The existing [browser runner](integrated-mvp.md#automated-real-browser-acceptance)
builds real Next.js and starts FastAPI against a fresh migrated PostgreSQL
database. `tests/browser/writing-human.spec.cjs` performs the complete journey,
including incomplete-score recovery, keyboard activation/save, mobile layouts,
candidate isolation and safe audit inspection. It adds an independent negative
scenario for candidate access to Writing marking and rubric activation. Successful
server responses are not mocked. The existing three objective/privacy/RBAC
journeys still run.

The text-only browser runner retains its explicit development memory storage;
it is not storage-persistence evidence. Separate required CI tests run against
real MinIO, and Redis integrations remain separate. No AI worker or inference is
required for this explicitly human-only journey. Recorded/live Speaking Step 2B
and automatic merge remain outside this step.
