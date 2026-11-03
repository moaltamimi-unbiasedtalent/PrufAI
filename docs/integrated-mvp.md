# Integrated product MVP: objective B1 practice

This milestone connects the existing Candidate App, Institution Portal and
read-only Control Centre. Its golden candidate starts with no assignment,
attempt, ScoringRun, ResultVersion or release. An institution administrator
assigns a published exam, the candidate submits real persisted responses, and
the existing deterministic scorer creates the result. Human review, approval,
release and Statement of Results issuance remain separate authorized actions.

The content is original **SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL**. The
assessment is `PRACTICE`, `LANGUAGE`, German (`de`), B1. A raw score does not
invent a passing threshold: an outcome can correctly remain `UNDETERMINED`
when no governed outcome rule is configured. Official certification remains
default-deny. No inference, transcription, Writing or Speaking is part of this
objective journey.

## Existing service and persistence map

`O` below means `/api/v1/organisations/{organisation_id}`; `C` means
`/api/v1/candidate`; `P` means `/api/v1/platform/control-center`. Service paths
are under `services/api/app/`. Most existing domains query the SQLAlchemy
Session in their service; they do not have a separate repository module. The
Control Centre uses its existing `control_center/repository.py`.

| Step                         | Existing service / persistence                                                                    | Actual API                                                                                                       | Frontend                                                              | Authority                                                                                                                   | Existing acceptance                                               |
| ---------------------------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Standard Pack                | `standard_packs/service.py`; StandardPack, StandardPackVersion, source bindings and blueprint     | `P/standards?kind=versions`, `P/standards?kind=bindings`                                                         | `/control-center/standards`                                           | Direct platform entry plus `standard_pack.read`                                                                             | `test_standard_packs.py`, Control Centre PostgreSQL               |
| Question Bank                | `question_bank/service.py`; QuestionBankItemVersion and exact item key                            | `P/question-bank?organisation_id=...`                                                                            | `/control-center/question-bank` through organisation metadata link    | Direct platform entry, `question_bank.read`, explicit organisation-read capability for private metadata                     | Question Bank PostgreSQL, Control Centre PostgreSQL               |
| Exam Studio                  | `exams/service.py`; ExamVersion, hierarchy, item binding, checksum and review                     | `P/exams?organisation_id=...`; existing `/api/v1/platform/exam-versions/{id}` lifecycle endpoints                | `/control-center/exams`; existing authoring preview remains a preview | Direct platform entry plus `exam.read`; existing lifecycle permissions                                                      | Exam Studio PostgreSQL                                            |
| Assignment                   | `runtime/service.py`; CandidateExamAssignment                                                     | `GET O/exam-assignment-options`, `POST O/exam-assignments`                                                       | `/institution/assignments`                                            | `exam_assignment.manage`, `exam.read`, `membership.read` for selector; real assignment service checks ownership/eligibility | Integrated PostgreSQL and browser                                 |
| Runtime / response revisions | `runtime/service.py`; CandidateExamAttempt, CandidateResponse, CandidateResponseRevision          | `C/exam-assignments/{id}/attempts`, `C/exam-attempts/{id}/{start,runtime,submit}`, `PUT .../responses/{binding}` | `/candidate`, `/candidate/exams/{attemptId}`                          | Authenticated owner in exact organisation, runtime policy                                                                   | Runtime PostgreSQL, demo seed PostgreSQL, integrated browser      |
| ScoringRun / ResultVersion   | `scoring/service.py`; exact pinned item/response sets, ScoringRun and ResultVersion               | `POST O/exam-attempts/{id}/score`; `O/scoring-runs/{id}`; `O/results/{id}/versions`                              | `/institution/overview`, `/institution/results/{resultId}`            | `scoring.run`, `result.read`                                                                                                | Scoring PostgreSQL, integrated PostgreSQL                         |
| Human result approval        | `scoring/service.py`; HumanReviewRequest tied to checksum                                         | `POST O/result-versions/{id}/request-review`, `.../approve`                                                      | `/institution/results/{resultId}`                                     | `result.review`                                                                                                             | Scoring and release regression, integrated PostgreSQL/browser     |
| Governed release             | `result_release/service.py`; immutable ResultRelease pins exact result/run/pack/attempt/candidate | `POST O/result-versions/{id}/release`, `O/releases/{id}`                                                         | Result review then `/institution/releases/{releaseId}`                | `result.release` and organisation/lifecycle/checksum checks                                                                 | Release PostgreSQL and concurrency, integrated PostgreSQL/browser |
| Statement of Results         | `result_release/service.py`; StatementOfResults immutable structured payload and checksum         | `POST O/releases/{id}/statement`; `C/releases/{id}/statement`                                                    | Institution release detail, `/candidate/results/releases/{releaseId}` | Authorized issuer; authenticated owning candidate for read                                                                  | Release PostgreSQL, integrated PostgreSQL/browser                 |
| Appeal                       | `result_governance/service.py`; ResultAppeal and append-only versions/evidence                    | `POST C/results/{version}/appeals`, `C/appeals/{id}`; `O/appeals/{id}/{review,adjudicate}`                       | Candidate appeal/history; `/institution/appeals/{appealId}`           | Owner plus policy; `appeal.read`, `appeal.adjudicate`                                                                       | Governance PostgreSQL and integrated rejection journey            |
| Audit / platform inspection  | Existing audit writers plus `control_center/service.py` and repository                            | `P/audit?event_type=result.released`                                                                             | `/control-center/audit`                                               | Direct `platform.control_center.read` and `platform.audit.read`; no tenant impersonation                                    | Control Centre PostgreSQL and integrated browser                  |

No model or migration is introduced. The dynamic single migration head is
checked using Alembic's ScriptDirectory and compared to the live database.
Historical result and artifact payloads are never rewritten to add new fields.
New statements carry their issuing operator and exact result summary inside
their checksum-protected payload.

## Manual golden journey

Use a dedicated database and the repository workflow in [local-demo.md](local-demo.md).
The seed is explicitly development-only and must not reset a completed golden
candidate. Use another new database for another clean run. All listed accounts
use the existing development password or the operator's `PRUFAI_DEMO_PASSWORD`.

1. Sign in as `platform.admin@demo.prufai.local`. Inspect the golden Standard
   Pack/version and source binding. Open Organisations and follow the academy's
   **Exam metadata** link to inspect the golden published exam checksum.
2. Sign out. Sign in as `admin@demo.prufai.local`. Open **Assign practice
   assessment**. Choose **Golden B1 Practice Candidate** and the **Golden B1
   German Practice** version, then **Assign assessment**.
3. Sign out. Sign in as `golden.candidate@demo.prufai.local`. Open the one
   assignment, prepare it, and start. Select **Um 10 Uhr.** and wait for saved
   revision 1. Select **Um 9 Uhr.** and wait for revision 2. Reload, confirm
   revision 2 and the selected answer, then submit. The response becomes
   read-only. No released result is visible yet.
4. Sign out and return as Institution Admin. In Institution inspection, find
   the golden candidate's submitted attempt and choose **Score submitted
   attempt**. Inspect the persisted score, ScoringRun, ResultVersion and exact
   checksums. Request human review, reload to confirm its persistence, approve
   the reviewed result, and then separately release the approved result.
5. In release detail choose **Issue Statement of Results**. Inspect the exact
   release/result/run/pack/attempt/candidate/issuer references and checksum.
6. Return as the golden candidate. Open released results, then the result and
   statement. The raw score is 1/1; classification is PRACTICE. This is not an
   official provider certificate.
7. Submit an appeal against that exact result version. Return as the authorized
   institution actor, open the appeal, start review, inspect the pinned
   evidence and provide a reason for a valid rejection. Reject the appeal.
   Returning as candidate shows the decision; the original result/release
   remain. Rejection creates no corrected result or successor release.
8. Return as Platform Admin and filter Audit evidence for `result.released`.
   Inspect safe entity/correlation references. Candidate answers and private
   appeal text are not exposed through the platform audit view.

## Automated real browser acceptance

Browser tooling is isolated from application dependencies. The existing
`apps/web/package.json`, lockfile and Python dependency versions are unchanged.
The pinned test runner and Chromium follow the [Playwright installation
workflow](https://playwright.dev/docs/library). Install once into a temporary
tool directory:

```sh
export PRUFAI_BROWSER_TOOL_DIR="$(mktemp -d /tmp/prufai-browser-tool.XXXXXX)"
npm install --prefix "$PRUFAI_BROWSER_TOOL_DIR" --no-save --ignore-scripts @playwright/test@1.56.1
node "$PRUFAI_BROWSER_TOOL_DIR/node_modules/@playwright/test/cli.js" install chromium
```

Create a fresh PostgreSQL database with pgvector available, without resetting
any developer database. Then run from the repository root:

```sh
export DATABASE_URL='postgresql+psycopg://LOCAL_USER@127.0.0.1:PORT/NEW_ISOLATED_DATABASE'
bash scripts/run-integrated-browser.sh
```

The runner applies existing migrations, invokes the explicit demo seed, builds
with the exact API proxy target, starts real FastAPI and a production Next.js server, waits for readiness, and stops
only its own processes on exit. Override `PRUFAI_E2E_API_PORT` (default 8010)
and `PRUFAI_E2E_WEB_PORT` (default 3010) to avoid other local services.

The tests in `tests/browser/` exercise UI login, assignment, two saves,
reload/resume, submission, deterministic scoring, human approval/release,
statement, appeal rejection and safe platform inspection. Additional journeys
prove server RBAC and actual IndexedDB logout/account partitioning. Successful
scoring, release and backend responses are never mocked. The offline privacy
case only aborts synchronization requests to preserve a real queued response
until server-backed logout deletes it.

The CI browser job owns a separate fresh pgvector database and runs the same
script. API PostgreSQL acceptance runs separately with the required integration
flag. Existing real MinIO and Redis regressions are preserved in the Python
job. Memory storage in the objective browser process is an explicit development
setting and provides no evidence for audio persistence.

## Boundaries and follow-on work

- Original practice-policy metadata is a synthetic development prerequisite,
  not evidence of external source ingestion or provider authorization.
- Historic inspection candidate/assignment/results are separate from the new
  golden candidate. They are never accepted as golden-journey scoring proof.
- Full upheld correction/retranscription, Writing and recorded Speaking browser
  journeys are follow-on increments. Live Speaking and AI inference are absent.
- The executor can honestly remain UNCONFIGURED. No worker is required for
  objective deterministic scoring.
- Existing npm vulnerability findings require a separate security workstream.
  No framework/dependency upgrade or forceful audit fix belongs in this PR.
