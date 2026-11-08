from collections import Counter, defaultdict
from typing import Any, Unpack

from sqlalchemy import Select, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.config import Settings
from app.knowledge.storage import S3CompatibleObjectStorage, StorageError, build_object_storage
from app.models import (
    AIRun,
    AIRunStep,
    AssessmentResult,
    CandidateAudioRecordingSession,
    CandidateExamAssignment,
    CandidateExamAttempt,
    CandidateMediaAsset,
    CandidateResponse,
    CandidateResponseMediaBinding,
    CandidateResponseRevision,
    ExamVersionItemBinding,
    ExamVersionModule,
    ExamVersionSection,
    ExamVersionTask,
    HumanReviewRequest,
    ItemScore,
    QuestionBankItemVersion,
    ResultAppeal,
    ResultCorrection,
    ResultRelease,
    ResultVersion,
    ScoringRun,
    SpeakingExecutionJob,
    SpeechTranscription,
    SpeechTranscriptionVersion,
    StatementOfResults,
    SubjectiveAssessment,
    SubjectiveAssessmentVersion,
    WritingExecutionJob,
)
from app.models.enums import PackResponseMode, QuestionBankSkill
from app.models.privacy import PrivacyCase
from app.models.transcription_execution import SpeechTranscriptionJob
from app.privacy.schemas import Category
from app.writing_assessment.service import canonical_checksum

EvidenceRow = Row[Unpack[tuple[Any, ...]]]
EvidenceQuery = Select[Unpack[tuple[Any, ...]]]

METHOD = "assessment-lineage-v1"
ROW_LIMIT = 100
HEAD_LIMIT = 4
SECONDARY = {
    Category.REDIS: "CONFIGURATION_INVENTORIED",
    Category.BROWSER: "OPERATOR_VERIFICATION_REQUIRED",
    Category.TEMPORARY_FILES: "CONFIGURATION_INVENTORIED",
    Category.LOGS_TRACES: "OPERATOR_VERIFICATION_REQUIRED",
    Category.DATABASE_BACKUPS: "NOT_VERIFIED",
    Category.OBJECT_BACKUPS: "NOT_VERIFIED",
    Category.EXTERNAL_PROCESSORS: "NOT_VERIFIED",
}

DEPENDENCIES = {
    Category.ASSIGNMENTS: ["ATTEMPTS"],
    Category.ATTEMPTS: [
        "OBJECTIVE_ANSWERS",
        "WRITING_REVISIONS",
        "ORIGINAL_AUDIO",
        "SCORING_RESULTS",
    ],
    Category.OBJECTIVE_ANSWERS: ["SCORING_RESULTS"],
    Category.WRITING_REVISIONS: ["AI_EXECUTION", "HUMAN_REVIEW", "SCORING_RESULTS"],
    Category.ORIGINAL_AUDIO: ["TRANSCRIPTIONS", "AI_EXECUTION", "HUMAN_REVIEW"],
    Category.TRANSCRIPTIONS: ["AI_EXECUTION", "HUMAN_REVIEW"],
    Category.AI_EXECUTION: ["HUMAN_REVIEW"],
    Category.HUMAN_REVIEW: ["SCORING_RESULTS"],
    Category.SCORING_RESULTS: ["RELEASE_STATEMENTS", "APPEALS_CORRECTIONS"],
    Category.RELEASE_STATEMENTS: ["APPEALS_CORRECTIONS"],
    Category.APPEALS_CORRECTIONS: ["SCORING_RESULTS"],
}


class Snapshot:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.tables: dict[Category, dict[str, int]] = defaultdict(dict)
        self.rows: list[list[Any]] = []
        self.incomplete: set[Category] = set()
        self.conflicts: set[Category] = set()
        self.blockers: set[str] = set()
        self.object_states: Counter[str] = Counter()

    def read(self, query: EvidenceQuery) -> list[EvidenceRow]:
        return list(
            self.session.execute(query.order_by(query.selected_columns[0]).limit(ROW_LIMIT + 1))
        )

    def add(
        self,
        category: Category,
        table: str,
        query: EvidenceQuery,
        *,
        ownership: tuple[Any, Any] | None = None,
    ) -> list[EvidenceRow]:
        rows = self.read(query)
        if len(rows) > ROW_LIMIT:
            self.incomplete.add(category)
        valid = rows[:ROW_LIMIT]
        if ownership is not None:
            valid = [row for row in valid if tuple(row[-2:]) == ownership]
            if len(valid) != len(rows[:ROW_LIMIT]):
                self.conflicts.add(category)
        self.record(category, table, valid)
        return valid

    def record(self, category: Category, table: str, rows: list[EvidenceRow]) -> None:
        self.tables[category][table] = len(rows)
        self.rows.append([category, table, [[str(value) for value in row] for row in rows]])

    def output(self, category: Category, root_incomplete: bool) -> dict[str, Any]:
        if category not in self.tables:
            return {
                "category": category,
                "coverage": SECONDARY.get(category, "NOT_SUPPORTED"),
                "classification": "UNSUPPORTED",
                "count": None,
                "complete": False,
                "authoritative_storage": None,
                "ownership": "NOT_VERIFIED",
                "relationships": [],
                "tables": {},
            }
        complete = not (
            root_incomplete or category in self.incomplete or category in self.conflicts
        )
        return {
            "category": category,
            "coverage": "DIRECTLY_DISCOVERED",
            "classification": "LEGAL_REVIEW_REQUIRED" if complete else "SCOPE_INCOMPLETE",
            "count": sum(self.tables[category].values()),
            "complete": complete,
            "authoritative_storage": (
                "POSTGRESQL_AND_PRIVATE_OBJECT_STORAGE"
                if category == Category.ORIGINAL_AUDIO
                else "POSTGRESQL"
            ),
            "ownership": "SCOPE_CONFLICT" if category in self.conflicts else "EXACT_RELATIONSHIPS",
            "relationships": DEPENDENCIES[category],
            "tables": self.tables[category],
            **(
                {"object_classes": {"ORIGINAL_CANDIDATE_AUDIO": dict(self.object_states)}}
                if category == Category.ORIGINAL_AUDIO
                else {}
            ),
        }


def _ids(rows: list[EvidenceRow]) -> list[Any]:
    return [row[0] for row in rows]


def _responses(snapshot: Snapshot, attempts: dict[Any, Any]) -> dict[Any, tuple[Any, Any, Any]]:
    response, revision = CandidateResponse, CandidateResponseRevision
    item, binding = QuestionBankItemVersion, ExamVersionItemBinding
    responses = snapshot.read(
        select(
            response.id,
            response.attempt_id,
            response.current_revision,
            item.response_mode,
            item.skill,
            binding.id.label("item_binding_id"),
            ExamVersionModule.exam_version_id,
        )
        .join(binding, binding.id == response.exam_version_item_binding_id)
        .join(item, item.id == binding.question_bank_item_version_id)
        .join(ExamVersionTask, ExamVersionTask.id == binding.exam_version_task_id)
        .join(ExamVersionSection, ExamVersionSection.id == ExamVersionTask.exam_version_section_id)
        .join(ExamVersionModule, ExamVersionModule.id == ExamVersionSection.exam_version_module_id)
        .where(response.attempt_id.in_(attempts))
    )
    objective = Category.OBJECTIVE_ANSWERS
    writing = Category.WRITING_REVISIONS
    owned = [
        row for row in responses[:ROW_LIMIT] if attempts[row.attempt_id] == row.exam_version_id
    ]
    if len(owned) != len(responses[:ROW_LIMIT]):
        snapshot.conflicts.update((objective, writing))
    objective_modes = {
        PackResponseMode.SELECT_ONE,
        PackResponseMode.SELECT_MULTIPLE,
        PackResponseMode.MATCH,
        PackResponseMode.ORDER,
    }
    snapshot.record(
        objective,
        response.__tablename__,
        [r for r in owned if r[3] in objective_modes],
    )
    revisions = snapshot.read(
        select(
            revision.id,
            revision.attempt_id,
            revision.candidate_response_id,
            revision.payload_checksum,
        ).where(revision.candidate_response_id.in_(_ids(owned)))
    )
    parents = {row[0]: row for row in owned}
    valid = [row for row in revisions[:ROW_LIMIT] if parents[row[2]][1] == row[1]]
    if len(valid) != len(revisions[:ROW_LIMIT]):
        snapshot.conflicts.update((objective, writing))
    if len(responses) > ROW_LIMIT or len(revisions) > ROW_LIMIT:
        snapshot.incomplete.update(
            (objective, writing, Category.AI_EXECUTION, Category.HUMAN_REVIEW)
        )
    snapshot.record(
        objective,
        revision.__tablename__,
        [r for r in valid if parents[r[2]][3] in objective_modes],
    )
    snapshot.record(
        writing,
        revision.__tablename__,
        [
            r
            for r in valid
            if parents[r[2]][4] == QuestionBankSkill.WRITING
            and parents[r[2]][3] in {PackResponseMode.TEXT, PackResponseMode.LONG_TEXT}
        ],
    )
    return {row[0]: (row[1], row[2], parents[row[2]].item_binding_id) for row in valid}


def _audio(
    snapshot: Snapshot,
    attempts: list[Any],
    case: PrivacyCase,
    settings: Settings,
    revisions: dict[Any, tuple[Any, Any, Any]],
) -> None:
    category = Category.ORIGINAL_AUDIO
    recording, asset = CandidateAudioRecordingSession, CandidateMediaAsset
    owners = (case.organisation_id, case.subject_user_id)
    sessions = snapshot.add(
        category,
        recording.__tablename__,
        select(
            recording.id,
            recording.attempt_id,
            recording.status,
            recording.storage_key,
            recording.upload_sha256,
            recording.upload_byte_size,
            recording.exam_version_item_binding_id,
            recording.organisation_id,
            recording.candidate_user_id,
        ).where(recording.attempt_id.in_(attempts)),
        ownership=owners,
    )
    assets = snapshot.add(
        category,
        asset.__tablename__,
        select(
            asset.id,
            asset.recording_session_id,
            asset.attempt_id,
            asset.sha256,
            asset.byte_size,
            asset.storage_key,
            asset.status,
            asset.exam_version_item_binding_id,
            asset.organisation_id,
            asset.candidate_user_id,
        ).where(asset.attempt_id.in_(attempts)),
        ownership=owners,
    )
    staged = {row[0]: row for row in sessions}
    verified = {}
    objects = {}
    for row in assets:
        receipt = staged.get(row.recording_session_id)
        key = f"candidate-audio/{case.organisation_id}/{row.attempt_id}/{row.recording_session_id}"
        if (
            receipt is None
            or receipt.attempt_id != row.attempt_id
            or receipt.storage_key != key
            or row.storage_key != key
            or receipt.upload_sha256 != row.sha256
            or receipt.upload_byte_size != row.byte_size
            or receipt.exam_version_item_binding_id != row.exam_version_item_binding_id
        ):
            snapshot.conflicts.add(category)
            snapshot.object_states["OBJECT_OWNERSHIP_UNRESOLVED"] += 1
            continue
        objects[key] = row.byte_size
        verified[row.id] = (row.attempt_id, row.sha256, row.exam_version_item_binding_id)
    for row in sessions:
        if row.upload_sha256 and row.upload_byte_size:
            key = f"candidate-audio/{case.organisation_id}/{row.attempt_id}/{row.id}"
            if row.storage_key == key:
                objects[key] = row.upload_byte_size
            else:
                snapshot.conflicts.add(category)
                snapshot.object_states["OBJECT_OWNERSHIP_UNRESOLVED"] += 1
    binding = CandidateResponseMediaBinding
    bindings = snapshot.add(
        category,
        binding.__tablename__,
        select(
            binding.id,
            binding.candidate_media_asset_id,
            binding.candidate_response_revision_id,
            binding.binding_role,
        ).where(binding.candidate_media_asset_id.in_(verified)),
    )
    if any(
        row[3] != "PRIMARY_AUDIO"
        or revisions.get(row[2], (None, None, None))[::2] != verified[row[1]][::2]
        for row in bindings
    ):
        snapshot.conflicts.add(category)
    if category in case.categories:
        _head_objects(snapshot, settings, objects)
    if category in snapshot.conflicts:
        snapshot.conflicts.add(Category.TRANSCRIPTIONS)

    transcript, version = SpeechTranscription, SpeechTranscriptionVersion
    transcriptions = snapshot.add(
        Category.TRANSCRIPTIONS,
        transcript.__tablename__,
        select(
            transcript.id,
            transcript.asset_id,
            transcript.status,
        ).where(transcript.asset_id.in_(verified)),
    )
    parents = {row.id: row.asset_id for row in transcriptions}
    versions = snapshot.add(
        Category.TRANSCRIPTIONS,
        version.__tablename__,
        select(
            version.id,
            version.transcription_id,
            version.candidate_media_asset_id,
            version.audio_sha256,
            version.checksum,
            version.status,
            version.execution_job_id,
        ).where(version.transcription_id.in_(parents)),
    )
    if any(row[2] != parents[row[1]] or row[3] != verified[parents[row[1]]][1] for row in versions):
        snapshot.conflicts.add(Category.TRANSCRIPTIONS)
    job = SpeechTranscriptionJob
    jobs = snapshot.add(
        Category.TRANSCRIPTIONS,
        job.__tablename__,
        select(
            job.id,
            job.attempt_id,
            job.candidate_media_asset_id,
            job.candidate_response_revision_id,
            job.candidate_response_media_binding_id,
            job.transcription_id,
            job.status,
            job.organisation_id,
            job.candidate_user_id,
        ).where(job.attempt_id.in_(attempts)),
        ownership=owners,
    )
    pins = {row[0]: (row[1], row[2]) for row in bindings}
    if any(
        verified.get(row[2], (None, None))[0] != row[1]
        or revisions.get(row[3], (None, None))[0] != row[1]
        or pins.get(row[4]) != (row[2], row[3])
        or parents.get(row[5]) != row[2]
        for row in jobs
    ):
        snapshot.conflicts.add(Category.TRANSCRIPTIONS)
    job_pins = {row.id: (row.transcription_id, row.candidate_media_asset_id) for row in jobs}
    if any(row[6] and job_pins.get(row[6]) != (row[1], row[2]) for row in versions):
        snapshot.conflicts.add(Category.TRANSCRIPTIONS)
    if any(row.status in {"QUEUED", "RUNNING", "RETRYABLE"} for row in jobs):
        snapshot.blockers.add("TRANSCRIPTION_PENDING")
    if category in snapshot.incomplete:
        snapshot.incomplete.add(Category.TRANSCRIPTIONS)


def _head_objects(snapshot: Snapshot, settings: Settings, objects: dict[str, int]) -> None:
    if not objects:
        return
    try:
        storage = build_object_storage(settings, metadata_probe=True)
    except StorageError:
        storage = None
    for index, (key, size) in enumerate(sorted(objects.items())):
        state = "METADATA_VERIFICATION_UNAVAILABLE"
        if isinstance(storage, S3CompatibleObjectStorage) and index < HEAD_LIMIT:
            state, metadata_identity = storage.probe_metadata(key, size)
            snapshot.rows.append(["OBJECT_HEAD", index, state, metadata_identity])
        snapshot.object_states[state] += 1
    if any(state != "OBJECT_KNOWN" for state in snapshot.object_states):
        snapshot.incomplete.add(Category.ORIGINAL_AUDIO)
        snapshot.blockers.add("OBJECT_METADATA_UNRESOLVED")


def _assessment(
    snapshot: Snapshot,
    attempts: list[Any],
    case: PrivacyCase,
    revisions: dict[Any, tuple[Any, Any, Any]],
) -> None:
    ai, human = Category.AI_EXECUTION, Category.HUMAN_REVIEW
    runs: set[Any] = set()
    for job in (WritingExecutionJob, SpeakingExecutionJob):
        rows = snapshot.add(
            ai,
            job.__tablename__,
            select(
                job.id,
                job.attempt_id,
                job.candidate_response_id,
                job.candidate_response_revision_id,
                job.ai_run_id,
                job.status,
                job.exam_version_item_binding_id,
                job.input_checksum,
                job.output_checksum,
                job.organisation_id,
                job.candidate_user_id,
            ).where(job.attempt_id.in_(attempts)),
            ownership=(case.organisation_id, case.subject_user_id),
        )
        for row in rows:
            if revisions.get(row[3]) == (row[1], row[2], row.exam_version_item_binding_id):
                runs.add(row.ai_run_id)
            else:
                snapshot.conflicts.add(ai)
        if any(row.status in {"QUEUED", "RUNNING", "RETRYABLE"} for row in rows):
            snapshot.blockers.add("ASSESSMENT_EXECUTION_PENDING")
    actual_runs = snapshot.add(
        ai,
        AIRun.__tablename__,
        select(
            AIRun.id,
            AIRun.status,
            AIRun.organisation_id,
        ).where(AIRun.id.in_(runs)),
    )
    owned_runs = [row.id for row in actual_runs if row.organisation_id == case.organisation_id]
    if len(owned_runs) != len(actual_runs):
        snapshot.conflicts.add(ai)
    snapshot.add(
        ai,
        AIRunStep.__tablename__,
        select(
            AIRunStep.id,
            AIRunStep.status,
            AIRunStep.run_id,
        ).where(AIRunStep.run_id.in_(owned_runs)),
    )

    assessment = SubjectiveAssessment
    parents = snapshot.add(
        human,
        assessment.__tablename__,
        select(
            assessment.id,
            assessment.attempt_id,
            assessment.organisation_id,
            assessment.candidate_user_id,
        ).where(assessment.attempt_id.in_(attempts)),
        ownership=(case.organisation_id, case.subject_user_id),
    )
    version = SubjectiveAssessmentVersion
    versions = snapshot.add(
        human,
        version.__tablename__,
        select(
            version.id,
            version.attempt_id,
            version.candidate_response_id,
            version.candidate_response_revision_id,
            version.exam_version_item_binding_id,
            version.status,
            version.checksum,
        ).where(version.subjective_assessment_id.in_(_ids(parents))),
    )
    if any(
        revisions.get(row[3]) != (row[1], row[2], row.exam_version_item_binding_id)
        for row in versions
    ):
        snapshot.conflicts.add(human)
    review = HumanReviewRequest
    rows = snapshot.add(
        human,
        review.__tablename__,
        select(
            review.id,
            review.status,
            review.reviewed_checksum,
        ).where(
            review.entity_type.in_(
                ("SUBJECTIVE_ASSESSMENT_VERSION", "SubjectiveAssessmentVersion")
            ),
            review.entity_id.in_(_ids(versions)),
        ),
    )
    if any(row.status.value in {"PENDING", "IN_REVIEW", "CHANGES_REQUESTED"} for row in rows):
        snapshot.blockers.add("HUMAN_REVIEW_PENDING")


def _results(snapshot: Snapshot, attempts: list[Any], case: PrivacyCase) -> None:
    scores, release, disputes = (
        Category.SCORING_RESULTS,
        Category.RELEASE_STATEMENTS,
        Category.APPEALS_CORRECTIONS,
    )
    owners = (case.organisation_id, case.subject_user_id)
    run, result, version = ScoringRun, AssessmentResult, ResultVersion
    runs = snapshot.add(
        scores,
        run.__tablename__,
        select(
            run.id,
            run.attempt_id,
            run.status,
            run.checksum,
            run.organisation_id,
            run.candidate_user_id,
        ).where(run.attempt_id.in_(attempts)),
        ownership=owners,
    )
    items = ItemScore
    snapshot.add(
        scores,
        items.__tablename__,
        select(
            items.id,
            items.status,
            items.candidate_response_revision_id,
            items.response_checksum,
        ).where(items.scoring_run_id.in_(_ids(runs))),
    )
    results = snapshot.add(
        scores,
        result.__tablename__,
        select(
            result.id,
            result.attempt_id,
            result.organisation_id,
            result.candidate_user_id,
        ).where(result.attempt_id.in_(attempts)),
        ownership=owners,
    )
    versions = snapshot.add(
        scores,
        version.__tablename__,
        select(
            version.id,
            version.result_id,
            version.scoring_run_id,
            version.status,
            version.checksum,
        ).where(version.result_id.in_(_ids(results))),
    )
    result_attempts = {row.id: row.attempt_id for row in results}
    run_attempts = {row.id: row.attempt_id for row in runs}
    version_results = {row.id: (row.result_id, row.scoring_run_id) for row in versions}
    if any(
        run_attempts.get(row.scoring_run_id) != result_attempts[row.result_id] for row in versions
    ):
        snapshot.conflicts.add(scores)
    if any(
        row.status.value in {"DRAFT", "INCOMPLETE", "REVIEW_REQUIRED", "APPROVED"}
        for row in versions
    ):
        snapshot.blockers.add("RESULT_REVIEW_OR_RELEASE_PENDING")

    rel, statement = ResultRelease, StatementOfResults
    releases = snapshot.add(
        release,
        rel.__tablename__,
        select(
            rel.id,
            rel.attempt_id,
            rel.assessment_result_id,
            rel.result_version_id,
            rel.scoring_run_id,
            rel.status,
            rel.checksum,
            rel.organisation_id,
            rel.candidate_user_id,
        ).where(rel.attempt_id.in_(attempts)),
        ownership=owners,
    )
    release_versions = {row.id: (row.attempt_id, row.result_version_id) for row in releases}
    if any(
        version_results.get(row.result_version_id) != (row.assessment_result_id, row.scoring_run_id)
        or result_attempts.get(row.assessment_result_id) != row.attempt_id
        for row in releases
    ):
        snapshot.conflicts.add(release)
    statements = snapshot.add(
        release,
        statement.__tablename__,
        select(
            statement.id,
            statement.release_id,
            statement.attempt_id,
            statement.result_version_id,
            statement.status,
            statement.checksum,
            statement.organisation_id,
            statement.candidate_user_id,
        ).where(statement.release_id.in_(release_versions)),
        ownership=owners,
    )
    if any(
        release_versions[row.release_id] != (row.attempt_id, row.result_version_id)
        for row in statements
    ):
        snapshot.conflicts.add(release)

    appeal, correction = ResultAppeal, ResultCorrection
    appeals = snapshot.add(
        disputes,
        appeal.__tablename__,
        select(
            appeal.id,
            appeal.status,
            appeal.challenged_result_checksum,
            appeal.organisation_id,
            appeal.candidate_user_id,
        ).where(appeal.attempt_id.in_(attempts)),
        ownership=owners,
    )
    corrections = snapshot.add(
        disputes,
        correction.__tablename__,
        select(
            correction.id,
            correction.status,
            correction.source_result_checksum,
            correction.organisation_id,
        ).where(correction.source_result_version_id.in_(version_results)),
    )
    if any(row.organisation_id != case.organisation_id for row in corrections):
        snapshot.conflicts.add(disputes)
    if any(
        row.status.value in {"DRAFT", "SUBMITTED", "UNDER_REVIEW", "INFORMATION_REQUIRED"}
        for row in appeals
    ):
        snapshot.blockers.add("APPEAL_PENDING")
    if any(
        row.status.value
        in {"REQUESTED", "IN_PROGRESS", "AWAITING_COMPONENT_REVIEW", "READY_FOR_RESCORING"}
        for row in corrections
    ):
        snapshot.blockers.add("CORRECTION_PENDING")
    if scores in snapshot.incomplete or scores in snapshot.conflicts:
        snapshot.incomplete.update((release, disputes))


def discover(session: Session, case: PrivacyCase, settings: Settings) -> tuple[dict[str, Any], str]:
    snapshot = Snapshot(session)
    assignment, attempt = CandidateExamAssignment, CandidateExamAttempt
    owners = (case.organisation_id, case.subject_user_id)
    assignments = snapshot.add(
        Category.ASSIGNMENTS,
        assignment.__tablename__,
        select(
            assignment.id,
            assignment.status,
            assignment.exam_version_id,
        ).where(assignment.organisation_id == owners[0], assignment.candidate_user_id == owners[1]),
    )
    attempts = snapshot.add(
        Category.ATTEMPTS,
        attempt.__tablename__,
        select(
            attempt.id,
            attempt.status,
            attempt.runtime_plan_checksum,
            attempt.assignment_id,
            attempt.exam_version_id,
            attempt.organisation_id,
            attempt.candidate_user_id,
        ).where(attempt.assignment_id.in_(_ids(assignments))),
        ownership=owners,
    )
    exam_versions = {row.id: row.exam_version_id for row in assignments}
    owned_attempts = [
        row for row in attempts if exam_versions[row.assignment_id] == row.exam_version_id
    ]
    if len(owned_attempts) != len(attempts):
        snapshot.conflicts.add(Category.ATTEMPTS)
    if any(row.status.value in {"CREATED", "READY", "IN_PROGRESS"} for row in owned_attempts):
        snapshot.blockers.add("ATTEMPT_OPEN")
    attempt_ids = _ids(owned_attempts)
    revisions = _responses(snapshot, {row.id: row.exam_version_id for row in owned_attempts})
    _audio(snapshot, attempt_ids, case, settings, revisions)
    _assessment(snapshot, attempt_ids, case, revisions)
    _results(snapshot, attempt_ids, case)
    root_incomplete = bool(
        {Category.ASSIGNMENTS, Category.ATTEMPTS} & (snapshot.incomplete | snapshot.conflicts)
    )
    if snapshot.conflicts:
        snapshot.blockers.add("OWNERSHIP_OR_LINEAGE_CONFLICT")
    if snapshot.incomplete:
        snapshot.blockers.add("SCOPE_INCOMPLETE")
    return (
        {
            "organisation_id": str(case.organisation_id),
            "categories": [
                snapshot.output(Category(category), root_incomplete) for category in case.categories
            ],
            "dependencies": sorted(snapshot.blockers),
            "dependency_status": "DEPENDENCY_BLOCKED"
            if snapshot.blockers
            else "LEGAL_REVIEW_REQUIRED",
        },
        canonical_checksum(snapshot.rows),
    )
