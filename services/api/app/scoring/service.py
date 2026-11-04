from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationService, TenantContext
from app.authorization.membership import require_active_tenant_permission
from app.models import (
    AssessmentResult,
    CandidateExamAttempt,
    CandidateResponseRevision,
    ExamVersion,
    ExamVersionItemBinding,
    HumanReviewRequest,
    ItemScore,
    QuestionBankItemVersion,
    ResultCorrection,
    ResultCorrectionObjectiveBinding,
    ResultRelease,
    ResultVersion,
    RubricVersion,
    ScoringComponent,
    ScoringResponseSnapshot,
    ScoringRun,
    StandardPackVersion,
    SubjectiveAssessmentVersion,
)
from app.models.enums import (
    AssessmentClassification,
    CandidateAttemptStatus,
    CorrectionStatus,
    HumanReviewStatus,
    ItemScoreStatus,
    QuestionBankScoringPolicy,
    QuestionBankSkill,
    ResultOutcome,
    ResultVersionStatus,
    RuleCategory,
    RuleEvaluationStatus,
    ScoringRunStatus,
    SubjectiveAssessmentStatus,
)
from app.rules.engine import EvaluationContext, RulesEngine
from app.speaking_assessment.governance import HumanSpeakingService
from app.writing_assessment.governance import HumanWritingService
from app.writing_assessment.service import RubricError

ENGINE_NAME = "prufai_objective_scoring"
ENGINE_VERSION = "1.0"


class ScoringError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def checksum(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


def _audit(
    session: Session,
    context: TenantContext,
    event: str,
    entity: Any,
    metadata: dict[str, Any] | None = None,
) -> None:
    if context.organisation_id:
        AuditService.record(
            session,
            context,
            event_type=event,
            entity_type=type(entity).__name__,
            entity_id=str(entity.id),
            organisation_id=context.organisation_id,
            require_audit_permission=False,
            metadata=metadata or {},
        )


def _snapshot(attempt: CandidateExamAttempt) -> tuple[dict[str, Any], str]:
    responses = {str(item.exam_version_item_binding_id): item for item in attempt.responses}
    items: list[dict[str, Any]] = []
    for module in attempt.runtime_plan.get("modules", []):
        for section in module.get("sections", []):
            for task in section.get("tasks", []):
                for item in task.get("items", []):
                    binding_id = str(item["binding_id"])
                    response = responses.get(binding_id)
                    revision = None
                    payload: dict[str, Any] | None = None
                    payload_checksum = None
                    if response:
                        payload = response.current_payload
                        revision = max(
                            response.revisions, key=lambda row: row.revision_number, default=None
                        )
                        payload_checksum = checksum(payload)
                    items.append(
                        {
                            "module_id": str(module.get("module_id")),
                            "section_id": str(section.get("section_id")),
                            "task_id": str(task.get("task_id")),
                            "binding_id": binding_id,
                            "question_bank_item_version_id": item.get("item_version_id"),
                            "response_id": str(response.id) if response else None,
                            "response_revision_id": str(revision.id) if revision else None,
                            "revision_number": response.current_revision if response else None,
                            "payload": payload,
                            "payload_checksum": payload_checksum,
                        }
                    )
    data = {
        "attempt_id": str(attempt.id),
        "exam_version_id": str(attempt.exam_version_id),
        "runtime_plan_checksum": attempt.runtime_plan_checksum,
        "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
        "items": items,
    }
    return data, checksum(data)


def _unanswered_award(item: QuestionBankItemVersion) -> Decimal:
    policy = item.scoring_metadata.get("unanswered_policy")
    if not isinstance(policy, dict) or policy.get("type") != "ZERO" or "points" not in policy:
        raise ScoringError(
            "SCORING_CONFIGURATION_UNSUPPORTED: unanswered policy is not explicitly defined"
        )
    value = _decimal(policy.get("points"))
    if value != Decimal("0"):
        raise ScoringError("SCORING_CONFIGURATION_UNSUPPORTED: unanswered ZERO policy must award 0")
    return value


def _configured_partial(
    item: QuestionBankItemVersion, selected: set[str], correct: set[str], maximum: Decimal
) -> Decimal | None:
    policy = item.scoring_metadata.get("partial_credit_policy")
    if policy is None:
        return None
    if not isinstance(policy, dict) or policy.get("type") != "DECLARED_TABLE":
        raise ScoringError("SCORING_CONFIGURATION_UNSUPPORTED: invalid partial-credit policy")
    known_options = {
        str(option.get("option_id"))
        for option in item.content.get("options", [])
        if isinstance(option, dict) and option.get("option_id") is not None
    }
    if known_options and not selected <= known_options:
        raise ScoringError("SCORING_CONFIGURATION_INVALID: response contains unknown option")
    table = policy.get("points_by_correct_count")
    if not isinstance(table, dict) or not table:
        raise ScoringError("SCORING_CONFIGURATION_INVALID: declared table is empty")
    for key, raw_value in table.items():
        try:
            count = int(key)
            value = _decimal(raw_value)
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise ScoringError("SCORING_CONFIGURATION_INVALID: malformed declared table") from exc
        if count < 0 or value < 0 or value > maximum:
            raise ScoringError(
                "SCORING_CONFIGURATION_INVALID: declared table value is out of bounds"
            )
    if selected - correct:
        raise ScoringError("SCORING_CONFIGURATION_INVALID: response contains an impossible option")
    mapped_value = table.get(str(len(selected & correct)))
    if mapped_value is None:
        raise ScoringError(
            "SCORING_CONFIGURATION_UNSUPPORTED: response has no declared table mapping"
        )
    return _decimal(mapped_value)


def objective_award(
    item: QuestionBankItemVersion, payload: dict[str, Any] | None
) -> tuple[ItemScoreStatus, Decimal | None]:
    """Apply only exact keys or explicitly declared bounded partial credit."""
    maximum = (
        item.max_points
        if item.max_points is not None
        else _decimal(item.scoring_metadata.get("max_points"))
    )
    if payload is None:
        return ItemScoreStatus.UNANSWERED, _unanswered_award(item)
    correct = {str(value) for value in item.content.get("correct_option_ids", [])}
    if item.response_mode.value == "SELECT_ONE":
        return ItemScoreStatus.SCORED, maximum if payload.get("option_id") in correct and len(
            correct
        ) == 1 else Decimal("0")
    if item.response_mode.value == "SELECT_MULTIPLE":
        selected = {str(value) for value in payload.get("option_ids", [])}
        partial = _configured_partial(item, selected, correct, maximum)
        return ItemScoreStatus.SCORED, partial if partial is not None else (
            maximum if selected == correct else Decimal("0")
        )
    return ItemScoreStatus.NOT_SCORABLE, None


class ScoringService:
    @staticmethod
    def _requires_human_speaking_membership(
        session: Session,
        attempt: CandidateExamAttempt,
        item_map: dict[str, QuestionBankItemVersion],
    ) -> bool:
        for item in item_map.values():
            if item.skill is not QuestionBankSkill.SPEAKING or not item.rubric_reference:
                continue
            try:
                rubric_id = uuid.UUID(item.rubric_reference)
            except (ValueError, TypeError, AttributeError):
                continue
            rubric = session.get(RubricVersion, rubric_id)
            if rubric is not None and rubric.ai_assessment_policy.get("mode") == "HUMAN_ONLY":
                return True
        speaking_ids = [
            item.id for item in item_map.values() if item.skill is QuestionBankSkill.SPEAKING
        ]
        human_target = session.scalar(
            select(SubjectiveAssessmentVersion.id)
            .where(
                SubjectiveAssessmentVersion.attempt_id == attempt.id,
                SubjectiveAssessmentVersion.assessment_origin == "HUMAN",
                SubjectiveAssessmentVersion.question_bank_item_version_id.in_(speaking_ids),
            )
            .limit(1)
        )
        return human_target is not None

    @staticmethod
    def _current_approved_subjective_versions(
        versions: Iterable[SubjectiveAssessmentVersion],
    ) -> dict[str, SubjectiveAssessmentVersion]:
        grouped: dict[str, list[SubjectiveAssessmentVersion]] = {}
        for version in versions:
            binding = str(version.exam_version_item_binding_id)
            grouped.setdefault(binding, []).append(version)
        selected: dict[str, SubjectiveAssessmentVersion] = {}
        for binding, history in grouped.items():
            if len({row.version_number for row in history}) != len(history):
                raise ScoringError("SUBJECTIVE_AUTHORITY_CONFLICT: duplicate version numbers")
            roots = {getattr(row, "subjective_assessment_id", None) for row in history}
            if len(roots) != 1:
                raise ScoringError("SUBJECTIVE_AUTHORITY_CONFLICT: conflicting assessment roots")
            approvals = [
                row for row in history if row.status is SubjectiveAssessmentStatus.APPROVED
            ]
            if len(approvals) > 1:
                raise ScoringError("SUBJECTIVE_AUTHORITY_CONFLICT: multiple approved versions")
            latest = max(history, key=lambda row: row.version_number)
            pending = {
                SubjectiveAssessmentStatus.CREATED,
                SubjectiveAssessmentStatus.AI_EVALUATION_RUNNING,
                SubjectiveAssessmentStatus.AI_PROPOSED,
                SubjectiveAssessmentStatus.REVIEW_REQUIRED,
                SubjectiveAssessmentStatus.IN_REVIEW,
            }
            if any(row is not latest and row.status in pending for row in history):
                raise ScoringError("SUBJECTIVE_AUTHORITY_CONFLICT: unresolved active ancestor")
            if approvals and latest.status is not SubjectiveAssessmentStatus.APPROVED:
                raise ScoringError(
                    "CURRENT_SUBJECTIVE_ASSESSMENT_UNRESOLVED: newer assessment requires resolution"
                )
            if latest.status is SubjectiveAssessmentStatus.APPROVED:
                if (
                    getattr(latest, "assessment_origin", None) == "HUMAN"
                    and latest.version_number > 1
                ):
                    predecessor = next(
                        (row for row in history if row.id == latest.supersedes_version_id), None
                    )
                    if predecessor is None or predecessor.version_number >= latest.version_number:
                        raise ScoringError(
                            "SUBJECTIVE_AUTHORITY_CONFLICT: broken successor lineage"
                        )
                    if (
                        latest.candidate_media_asset_id is not None
                        and predecessor.version_number != latest.version_number - 1
                    ):
                        raise ScoringError(
                            "SUBJECTIVE_AUTHORITY_CONFLICT: broken Speaking successor"
                        )
                selected[binding] = latest
        return selected

    @staticmethod
    def _human_scoring_evidence(
        session: Session,
        source: SubjectiveAssessmentVersion,
        attempt: CandidateExamAttempt,
        item: QuestionBankItemVersion,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        """Shared identity checks precede the skill-specific evidence validator."""
        binding = session.get(ExamVersionItemBinding, source.exam_version_item_binding_id)
        revision = session.get(CandidateResponseRevision, source.candidate_response_revision_id)
        exam = session.get(ExamVersion, attempt.exam_version_id)
        root = source.assessment
        if (
            attempt.status is not CandidateAttemptStatus.SUBMITTED
            or root.organisation_id != attempt.organisation_id
            or root.candidate_user_id != attempt.candidate_user_id
            or root.attempt_id != attempt.id
            or root.exam_version_id != attempt.exam_version_id
            or root.exam_version_item_binding_id != source.exam_version_item_binding_id
            or root.question_bank_item_version_id != item.id
            or source.attempt_id != attempt.id
            or source.exam_version_id != attempt.exam_version_id
            or source.question_bank_item_version_id != item.id
            or str(source.exam_version_item_binding_id) != response["binding_id"]
            or str(source.candidate_response_id) != response["response_id"]
            or str(source.candidate_response_revision_id) != response["response_revision_id"]
            or source.response_checksum != response["payload_checksum"]
            or str(source.standard_pack_version_id)
            != str(attempt.runtime_plan.get("standard_pack_version_id"))
            or binding is None
            or binding.question_bank_item_version_id != item.id
            or binding.task.section.module.exam_version_id != attempt.exam_version_id
            or revision is None
            or revision.response.current_revision != revision.revision_number
            or revision.revision_number != response["revision_number"]
            or exam is None
            or exam.standard_pack_version_id != source.standard_pack_version_id
        ):
            raise ScoringError("INVALID_HUMAN_ASSESSMENT: exact submitted identity does not match")
        maximum = (
            item.max_points
            if item.max_points is not None
            else _decimal(item.scoring_metadata.get("max_points"))
        )
        try:
            if item.skill is QuestionBankSkill.WRITING:
                return HumanWritingService.approved_scoring_evidence(
                    session, source, attempt, item, response, maximum
                )
            if item.skill is QuestionBankSkill.SPEAKING:
                return HumanSpeakingService.approved_scoring_evidence(
                    session, source, attempt, item, response, maximum
                )
            raise RubricError("HUMAN scoring requires an explicit supported skill")
        except (RubricError, ArithmeticError, ValueError) as exc:
            raise ScoringError(f"INVALID_HUMAN_ASSESSMENT: {exc}") from exc

    @staticmethod
    def _selected_evidence(
        session: Session,
        attempt: CandidateExamAttempt,
        snapshot: dict[str, Any],
        item_map: dict[str, QuestionBankItemVersion],
        *,
        lock: bool,
    ) -> tuple[dict[str, SubjectiveAssessmentVersion], dict[str, dict[str, Any]]]:
        query = (
            select(SubjectiveAssessmentVersion)
            .where(SubjectiveAssessmentVersion.attempt_id == attempt.id)
            .order_by(SubjectiveAssessmentVersion.id)
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        selected = ScoringService._current_approved_subjective_versions(session.scalars(query))
        evidence: dict[str, dict[str, Any]] = {}
        for row in snapshot["items"]:
            item = item_map.get(str(row["question_bank_item_version_id"]))
            source = selected.get(row["binding_id"])
            if item is not None and source is not None and source.assessment_origin == "HUMAN":
                evidence[row["binding_id"]] = ScoringService._human_scoring_evidence(
                    session, source, attempt, item, row
                )
        return selected, evidence

    @staticmethod
    def assert_run_current_authority(session: Session, run: ScoringRun) -> None:
        """Validate a new consequential action; historical reads never call this gate."""
        human_target = session.scalar(
            select(SubjectiveAssessmentVersion.id)
            .where(
                SubjectiveAssessmentVersion.attempt_id == run.attempt_id,
                SubjectiveAssessmentVersion.assessment_origin == "HUMAN",
            )
            .limit(1)
        )
        persisted_human = any(
            isinstance(score.scoring_metadata.get("component"), dict)
            and score.scoring_metadata["component"].get("assessment_origin") == "HUMAN"
            for score in run.item_scores
        )
        # Existing objective/AI release contracts remain in ResultReleaseService.
        # Genuine HUMAN history never inherits a legacy no-snapshot exemption.
        if human_target is None and not persisted_human:
            return
        attempt = session.get(CandidateExamAttempt, run.attempt_id)
        if (
            attempt is None
            or attempt.status is not CandidateAttemptStatus.SUBMITTED
            or run.status is not ScoringRunStatus.COMPLETED
            or run.organisation_id != attempt.organisation_id
            or run.candidate_user_id != attempt.candidate_user_id
            or run.exam_version_id != attempt.exam_version_id
        ):
            raise ScoringError("STALE_SCORING_EVIDENCE: run ownership or state does not match")
        snapshot, fingerprint = _snapshot(attempt)
        if (
            run.response_set_checksum != fingerprint
            or run.snapshot is None
            or run.snapshot.snapshot_json != snapshot
            or run.snapshot.response_set_checksum != fingerprint
            or run.snapshot.attempt_id != attempt.id
            or run.snapshot.exam_version_id != attempt.exam_version_id
            or run.snapshot.runtime_plan_checksum != attempt.runtime_plan_checksum
            or run.snapshot.submitted_at != attempt.submitted_at
            or str(run.standard_pack_version_id)
            != str(attempt.runtime_plan.get("standard_pack_version_id"))
        ):
            raise ScoringError("STALE_SCORING_EVIDENCE: submitted response snapshot changed")
        item_map = {
            str(item.id): item
            for item in session.scalars(
                select(QuestionBankItemVersion).where(
                    QuestionBankItemVersion.id.in_(
                        [
                            uuid.UUID(str(row["question_bank_item_version_id"]))
                            for row in snapshot["items"]
                            if row["question_bank_item_version_id"]
                        ]
                    )
                )
            )
        }
        selected, human = ScoringService._selected_evidence(
            session, attempt, snapshot, item_map, lock=True
        )
        if not ScoringService._run_uses_subjective_evidence(
            session, run, snapshot, item_map, selected, human
        ):
            raise ScoringError(
                "STALE_SCORING_EVIDENCE: run does not pin the current approved assessment"
            )

    @staticmethod
    def _run_uses_subjective_evidence(
        session: Session,
        run: ScoringRun,
        snapshot: dict[str, Any],
        item_map: dict[str, QuestionBankItemVersion],
        selected: dict[str, SubjectiveAssessmentVersion],
        human: dict[str, dict[str, Any]],
    ) -> bool:
        scores = {str(score.exam_version_item_binding_id): score for score in run.item_scores}
        for row in snapshot["items"]:
            item = item_map.get(str(row["question_bank_item_version_id"]))
            if item is None:
                return False
            score = scores.get(row["binding_id"])
            if score is not None and score.question_bank_item_version_id != item.id:
                replacement = session.get(
                    QuestionBankItemVersion, score.question_bank_item_version_id
                )
                if replacement is None:
                    return False
                governed = session.scalar(
                    select(ResultCorrectionObjectiveBinding.id)
                    .join(
                        ResultCorrection,
                        ResultCorrection.id
                        == ResultCorrectionObjectiveBinding.result_correction_id,
                    )
                    .where(
                        ResultCorrection.new_scoring_run_id == run.id,
                        ResultCorrection.status == CorrectionStatus.RESCORED,
                        ResultCorrectionObjectiveBinding.original_item_version_id == item.id,
                        ResultCorrectionObjectiveBinding.replacement_item_version_id
                        == score.question_bank_item_version_id,
                        ResultCorrectionObjectiveBinding.original_item_checksum == item.checksum,
                        ResultCorrectionObjectiveBinding.replacement_item_checksum
                        == replacement.checksum,
                    )
                )
                if (
                    item.scoring_policy is not QuestionBankScoringPolicy.OBJECTIVE_KEYED
                    or replacement is None
                    or governed is None
                ):
                    return False
                item = replacement
            maximum = (
                item.max_points
                if item.max_points is not None
                else _decimal(item.scoring_metadata.get("max_points"))
            )
            if (
                score is None
                or score.scoring_run_id != run.id
                or score.scoring_policy != item.scoring_policy.value
                or str(score.candidate_response_id) != str(row["response_id"])
                or str(score.candidate_response_revision_id) != str(row["response_revision_id"])
                or score.response_checksum != row["payload_checksum"]
                or score.maximum_points != maximum
                or score.question_bank_item_version_id != item.id
            ):
                return False
            if item.scoring_policy is QuestionBankScoringPolicy.OBJECTIVE_KEYED:
                continue
            source = selected.get(row["binding_id"])
            if (
                source is None
                or score is None
                or score.status is not ItemScoreStatus.SCORED
                or score.scoring_metadata.get("subjective_assessment_version_id") != str(source.id)
                or score.awarded_points != source.approved_score
                or any(
                    score.scoring_metadata.get(key) != value
                    for key, value in human.get(row["binding_id"], {}).items()
                )
            ):
                return False
        return True

    @staticmethod
    def scoring_readiness(
        session: Session, context: TenantContext, attempt_id: uuid.UUID
    ) -> dict[str, Any]:
        """Side-effect-free institution projection using the scorer's selection and validators."""
        AuthorizationService.require_permission(context, "result.read", context.organisation_id)
        attempt = session.get(CandidateExamAttempt, attempt_id)
        if attempt is None or attempt.organisation_id != context.organisation_id:
            raise ScoringError("attempt is not available in this organisation")
        snapshot, _fingerprint = _snapshot(attempt)
        item_map = {
            str(item.id): item
            for item in session.scalars(
                select(QuestionBankItemVersion).where(
                    QuestionBankItemVersion.id.in_(
                        [
                            uuid.UUID(str(row["question_bank_item_version_id"]))
                            for row in snapshot["items"]
                            if row["question_bank_item_version_id"]
                        ]
                    )
                )
            )
        }
        speaking = any(item.skill is QuestionBankSkill.SPEAKING for item in item_map.values())
        human_speaking = ScoringService._requires_human_speaking_membership(
            session, attempt, item_map
        )
        if human_speaking:
            require_active_tenant_permission(
                session, context, "result.read", attempt.organisation_id
            )
        versions = list(
            session.scalars(
                select(SubjectiveAssessmentVersion).where(
                    SubjectiveAssessmentVersion.attempt_id == attempt.id
                )
            )
        )
        latest = {
            str(row.exam_version_item_binding_id): row
            for row in sorted(versions, key=lambda value: value.version_number)
        }
        components = []
        for row in snapshot["items"]:
            item = item_map.get(str(row["question_bank_item_version_id"]))
            if item is None or item.scoring_policy is QuestionBankScoringPolicy.OBJECTIVE_KEYED:
                continue
            source = latest.get(row["binding_id"])
            components.append(
                {
                    "binding_id": row["binding_id"],
                    "skill": item.skill.value if item.skill else "UNSPECIFIED",
                    "status": source.status.value if source else "MISSING",
                    "assessment_version_id": str(source.id) if source else None,
                    "checksum": source.checksum if source else None,
                }
            )
        reasons: list[dict[str, Any]] = []
        state = "READY"
        try:
            if attempt.status is not CandidateAttemptStatus.SUBMITTED:
                raise ScoringError("INVALID_ATTEMPT_STATE: only submitted attempts may be scored")
            if any(
                str(row["question_bank_item_version_id"]) not in item_map
                for row in snapshot["items"]
            ):
                raise ScoringError("MISSING_ITEM_VERSION: exact item version is unavailable")
            selected, evidence = ScoringService._selected_evidence(
                session, attempt, snapshot, item_map, lock=False
            )
            for component in components:
                if component["binding_id"] not in selected:
                    state = "INCOMPLETE"
                    reasons.append(
                        {
                            **component,
                            "code": "PENDING_ASSESSMENT",
                            "message": "Independent approval of this assessment is required.",
                        }
                    )
        except ScoringError as exc:
            selected, evidence, state = {}, {}, "CONFLICT"
            reasons.append(
                {
                    "code": str(exc).split(":", 1)[0],
                    "message": "Resolve current assessment evidence through governed review.",
                }
            )
        permission = AuthorizationService.has_permission(
            context, "scoring.run", attempt.organisation_id
        )
        if human_speaking:
            permission = context.membership_id is not None and "scoring.run" in context.permissions
        existing = session.scalar(
            select(ScoringRun)
            .where(
                ScoringRun.attempt_id == attempt.id,
                ScoringRun.status == ScoringRunStatus.COMPLETED,
            )
            .order_by(ScoringRun.started_at.desc(), ScoringRun.id.desc())
            .limit(1)
        )
        uses_current = (
            existing is not None
            and state == "READY"
            and existing.response_set_checksum == _fingerprint
            and ScoringService._run_uses_subjective_evidence(
                session, existing, snapshot, item_map, selected, evidence
            )
        )
        return {
            "state": state,
            "has_speaking": speaking,
            "reasons": reasons,
            "components": components,
            "can_score": permission
            and (state == "READY" or (not speaking and state == "INCOMPLETE")),
            "assessment_version_ids": sorted(str(value.id) for value in selected.values()),
            "evidence_checksum": checksum(evidence),
            "existing_run_id": str(existing.id) if existing is not None else None,
            "uses_current_evidence": uses_current,
        }

    @staticmethod
    def fail_run(
        session: Session,
        run: ScoringRun,
        category: str,
        context: TenantContext | None = None,
    ) -> ScoringRun:
        if run.status in {ScoringRunStatus.COMPLETED, ScoringRunStatus.CANCELLED}:
            raise ScoringError("scoring run is already terminal")
        run.status = ScoringRunStatus.FAILED
        run.failure_category = category
        run.failed_at = _now()
        session.flush()
        if context is not None:
            _audit(
                session,
                context,
                "scoring.run.failed",
                run,
                {
                    "scoring_run_id": str(run.id),
                    "attempt_id": str(run.attempt_id),
                    "failure_category": category,
                    "engine": f"{run.scoring_engine_name}@{run.scoring_engine_version}",
                },
            )
        return run

    @staticmethod
    def score(
        session: Session,
        context: TenantContext,
        attempt_id: uuid.UUID,
        objective_item_overrides: dict[str, str] | None = None,
    ) -> ScoringRun:
        try:
            return ScoringService._score(
                session, context, attempt_id, objective_item_overrides=objective_item_overrides
            )
        except ScoringError:
            run = session.scalar(
                select(ScoringRun).where(
                    ScoringRun.attempt_id == attempt_id,
                    ScoringRun.status == ScoringRunStatus.RUNNING,
                )
            )
            if run is not None:
                ScoringService.fail_run(session, run, "SCORING_CONFIGURATION_INCOMPLETE", context)
                session.commit()
            raise

    @staticmethod
    def _score(
        session: Session,
        context: TenantContext,
        attempt_id: uuid.UUID,
        *,
        objective_item_overrides: dict[str, str] | None = None,
    ) -> ScoringRun:
        if context.organisation_id is None:
            raise ScoringError("organisation context required")
        AuthorizationService.require_permission(context, "scoring.run", context.organisation_id)
        attempt = session.get(CandidateExamAttempt, attempt_id)
        if attempt is None or attempt.organisation_id != context.organisation_id:
            raise ScoringError("attempt is not available in this organisation")
        if attempt.status is not CandidateAttemptStatus.SUBMITTED:
            raise ScoringError("INVALID_ATTEMPT_STATE: only SUBMITTED attempts may be scored")
        if attempt.submitted_at is None:
            raise ScoringError("MISSING_RESPONSE_SNAPSHOT: submitted_at is required")
        attempt = session.scalar(
            select(CandidateExamAttempt)
            .where(CandidateExamAttempt.id == attempt.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if attempt is None or attempt.status is not CandidateAttemptStatus.SUBMITTED:
            raise ScoringError("INVALID_ATTEMPT_STATE: submitted attempt changed")
        snapshot_json, response_checksum = _snapshot(attempt)
        pack_reference = attempt.runtime_plan.get("standard_pack_version_id")
        pack = session.get(
            StandardPackVersion, uuid.UUID(str(pack_reference)) if pack_reference else None
        )
        if pack is None:
            raise ScoringError("SCORING_CONFIGURATION_INCOMPLETE: StandardPackVersion missing")
        item_map = {
            str(item.id): item
            for item in session.scalars(
                select(QuestionBankItemVersion).where(
                    QuestionBankItemVersion.id.in_(
                        [
                            uuid.UUID(str(row["question_bank_item_version_id"]))
                            for row in snapshot_json["items"]
                            if row["question_bank_item_version_id"]
                        ]
                    )
                )
            )
        }
        if objective_item_overrides:
            replacement_ids = set(objective_item_overrides.values())
            replacements = {
                str(item.id): item
                for item in session.scalars(
                    select(QuestionBankItemVersion).where(
                        QuestionBankItemVersion.id.in_(replacement_ids)
                    )
                )
            }
            for original_id, replacement_id in objective_item_overrides.items():
                if original_id not in item_map or replacement_id not in replacements:
                    raise ScoringError("OBJECTIVE_CORRECTION_SOURCE_MISSING")
                item_map[original_id] = replacements[replacement_id]
        if ScoringService._requires_human_speaking_membership(session, attempt, item_map):
            require_active_tenant_permission(
                session, context, "scoring.run", attempt.organisation_id
            )
        subjective_versions, human_evidence = ScoringService._selected_evidence(
            session, attempt, snapshot_json, item_map, lock=True
        )
        writing_evidence = {
            binding: evidence
            for binding, evidence in human_evidence.items()
            if evidence["component"]["skill"] == "WRITING"
        }
        speaking_evidence = {
            binding: evidence
            for binding, evidence in human_evidence.items()
            if evidence["component"]["skill"] == "SPEAKING"
        }
        rule_refs = [
            {
                "id": str(binding.rule_version_id),
                "label": binding.rule_version.version_label,
                "checksum": binding.rule_version.checksum,
            }
            for binding in pack.rule_bindings
        ]
        configuration = {
            "engine": [ENGINE_NAME, ENGINE_VERSION],
            "standard_pack_version_id": str(pack.id),
            "configuration_checksum": pack.configuration_checksum,
            "item_versions": [
                item["question_bank_item_version_id"] for item in snapshot_json["items"]
            ],
            "objective_item_overrides": objective_item_overrides or {},
            "item_scoring_configuration": [
                {
                    "item_version_id": item_id,
                    "checksum": item_map[item_id].checksum,
                    "scoring_policy": item_map[item_id].scoring_policy.value,
                    "max_points": str(item_map[item_id].max_points)
                    if item_map[item_id].max_points is not None
                    else None,
                    "scoring_metadata": item_map[item_id].scoring_metadata,
                }
                for item_id in sorted(item_map)
            ],
            "approved_subjective_assessments": [
                {
                    "binding_id": binding_id,
                    "assessment_version_id": str(version.id),
                    "checksum": version.checksum,
                    "approved_score": str(version.approved_score),
                }
                for binding_id, version in sorted(subjective_versions.items())
            ],
            **({"human_writing_evidence": writing_evidence} if writing_evidence else {}),
            **({"human_speaking_evidence": speaking_evidence} if speaking_evidence else {}),
            "rule_versions": rule_refs,
        }
        configuration_checksum = checksum(configuration)
        existing = session.scalar(
            select(ScoringRun).where(
                ScoringRun.attempt_id == attempt.id,
                ScoringRun.response_set_checksum == response_checksum,
                ScoringRun.scoring_engine_version == ENGINE_VERSION,
                ScoringRun.configuration_checksum == configuration_checksum,
            )
        )
        if existing and existing.status is ScoringRunStatus.COMPLETED:
            return existing
        now = _now()
        run = existing or ScoringRun(
            attempt_id=attempt.id,
            exam_version_id=attempt.exam_version_id,
            organisation_id=attempt.organisation_id,
            candidate_user_id=attempt.candidate_user_id,
            standard_pack_version_id=pack.id,
            status=ScoringRunStatus.RUNNING,
            response_set_checksum=response_checksum,
            scoring_engine_name=ENGINE_NAME,
            scoring_engine_version=ENGINE_VERSION,
            configuration_checksum=configuration_checksum,
            started_at=now,
            initiated_by_user_id=context.user_id,
        )
        session.add(run)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            replay = session.scalar(
                select(ScoringRun).where(
                    ScoringRun.attempt_id == attempt.id,
                    ScoringRun.response_set_checksum == response_checksum,
                    ScoringRun.scoring_engine_version == ENGINE_VERSION,
                    ScoringRun.configuration_checksum == configuration_checksum,
                )
            )
            if replay is not None:
                return replay
            raise
        _audit(
            session,
            context,
            "scoring.run.created",
            run,
            {"response_set_checksum": response_checksum},
        )
        run.snapshot = ScoringResponseSnapshot(
            attempt_id=attempt.id,
            exam_version_id=attempt.exam_version_id,
            runtime_plan_checksum=attempt.runtime_plan_checksum,
            submitted_at=attempt.submitted_at,
            response_set_checksum=response_checksum,
            snapshot_json=snapshot_json,
            created_at=now,
        )
        raw_total = Decimal("0")
        max_total = Decimal("0")
        pending = 0
        aggregates: dict[tuple[str, str], list[Any]] = {}
        component_evidence: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in snapshot_json["items"]:
            item = item_map.get(str(row["question_bank_item_version_id"]))
            if item is None:
                raise ScoringError("MISSING_ITEM_VERSION")
            maximum = (
                item.max_points
                if item.max_points is not None
                else _decimal(item.scoring_metadata.get("max_points"))
            )
            policy = item.scoring_policy.value
            subjective = subjective_versions.get(row["binding_id"])
            awarded: Decimal | None = None
            score_status = ItemScoreStatus.SCORED
            if policy != QuestionBankScoringPolicy.OBJECTIVE_KEYED.value:
                if subjective is not None and subjective.approved_score is not None:
                    score_status = ItemScoreStatus.SCORED
                    awarded = Decimal(str(subjective.approved_score))
                else:
                    score_status = ItemScoreStatus.PENDING_ASSESSMENT
                    pending += 1
            else:
                score_status, awarded = objective_award(item, row["payload"])
            if awarded is not None:
                raw_total += awarded
            max_total += maximum
            for level, source_id in (
                ("TASK", row["task_id"]),
                ("SECTION", row["section_id"]),
                ("MODULE", row["module_id"]),
                ("EXAM", str(attempt.exam_version_id)),
            ):
                aggregate = aggregates.setdefault(
                    (level, source_id), [Decimal("0"), Decimal("0"), 0]
                )
                aggregate[0] = aggregate[0] + (awarded or Decimal("0"))
                aggregate[1] = aggregate[1] + maximum
                aggregate[2] = aggregate[2] + (
                    1 if score_status is ItemScoreStatus.PENDING_ASSESSMENT else 0
                )
                if row["binding_id"] in human_evidence:
                    component_evidence.setdefault((level, source_id), []).append(
                        human_evidence[row["binding_id"]]["component"]
                    )
            score = ItemScore(
                scoring_run_id=run.id,
                exam_version_item_binding_id=uuid.UUID(row["binding_id"]),
                question_bank_item_version_id=item.id,
                candidate_response_id=uuid.UUID(row["response_id"]) if row["response_id"] else None,
                candidate_response_revision_id=uuid.UUID(row["response_revision_id"])
                if row["response_revision_id"]
                else None,
                scoring_policy=policy,
                status=score_status,
                awarded_points=awarded,
                maximum_points=maximum,
                response_checksum=row["payload_checksum"],
                scoring_metadata={
                    "engine": ENGINE_VERSION,
                    "policy": item.scoring_metadata,
                    "subjective_assessment_version_id": str(subjective.id)
                    if policy != QuestionBankScoringPolicy.OBJECTIVE_KEYED.value
                    and subjective is not None
                    else None,
                    **human_evidence.get(row["binding_id"], {}),
                },
                created_at=now,
            )
            session.add(score)
        for (level, source_id), (awarded, maximum, pending_count) in aggregates.items():
            run.components.append(
                ScoringComponent(
                    level=level,
                    source_entity_id=source_id,
                    raw_awarded_points=awarded,
                    raw_maximum_points=maximum,
                    status=ItemScoreStatus.PENDING_ASSESSMENT
                    if pending_count
                    else ItemScoreStatus.SCORED,
                    provenance={
                        "standard_pack_version_id": str(pack.id),
                        **(
                            {"assessment_components": component_evidence[(level, source_id)]}
                            if (level, source_id) in component_evidence
                            else {}
                        ),
                    },
                    created_at=now,
                )
            )
        rule_provenance: dict[str, Any] = {
            "rule_versions": configuration["rule_versions"],
            "evaluations": [],
        }
        outcome = ResultOutcome.UNDETERMINED
        if pack.rule_bindings:
            rules = [
                (binding.rule_version.definition, binding.rule_version)
                for binding in pack.rule_bindings
                if binding.rule_version.definition.category
                in {RuleCategory.RESULT, RuleCategory.CERTIFICATION, RuleCategory.SCORING}
            ]
            if rules:
                exam_for_facts = session.get(ExamVersion, attempt.exam_version_id)
                if exam_for_facts is None:
                    raise ScoringError("MISSING_ITEM_VERSION")
                report = RulesEngine().evaluate(
                    rules,
                    EvaluationContext(
                        {
                            "score": {
                                "raw_awarded_points": str(raw_total),
                                "raw_maximum_points": str(max_total),
                            },
                            "result": {
                                "components_complete": pending == 0,
                                "pending_assessment_count": pending,
                                "objective_scoring_complete": pending == 0,
                            },
                            "exam": {"classification": exam_for_facts.classification.value},
                        },
                        standard_pack_version_id=str(pack.id),
                        standard_pack_checksum=pack.configuration_checksum,
                    ),
                )
                rule_provenance["evaluations"] = [
                    {
                        "rule_version_id": result.rule_version_id,
                        "category": result.category,
                        "status": result.status.value,
                        "failure_code": result.failure_code,
                    }
                    for result in report.results
                ]
                if report.overall_status is RuleEvaluationStatus.PASS:
                    outcome = ResultOutcome.PASS
                elif report.overall_status is RuleEvaluationStatus.FAIL:
                    outcome = ResultOutcome.FAIL
        run.status = ScoringRunStatus.COMPLETED
        run.completed_at = _now()
        run.checksum = checksum(
            {
                "response_set_checksum": response_checksum,
                "configuration": configuration,
                "raw": [str(raw_total), str(max_total)],
            }
        )
        result = session.scalar(
            select(AssessmentResult).where(AssessmentResult.attempt_id == attempt.id)
        )
        if result is None:
            result = AssessmentResult(
                attempt_id=attempt.id,
                candidate_user_id=attempt.candidate_user_id,
                organisation_id=attempt.organisation_id,
                exam_version_id=attempt.exam_version_id,
                created_at=now,
            )
            session.add(result)
            session.flush()
            _audit(session, context, "result.created", result)
        previous = max(result.versions, key=lambda row: row.version_number, default=None)
        governed_release = (
            session.scalar(
                select(ResultRelease.id).where(ResultRelease.result_version_id == previous.id)
            )
            if previous
            else None
        )
        if (
            previous
            and previous.status is ResultVersionStatus.RELEASED
            and governed_release is None
        ):
            previous.status = ResultVersionStatus.SUPERSEDED
            _audit(
                session,
                context,
                "result.version.superseded",
                previous,
                {"superseded_by_attempt_id": str(attempt.id)},
            )
        rv_status = (
            ResultVersionStatus.INCOMPLETE if pending else ResultVersionStatus.REVIEW_REQUIRED
        )
        rv = ResultVersion(
            result=result,
            scoring_run_id=run.id,
            version_number=(previous.version_number + 1 if previous else 1),
            status=rv_status,
            response_set_checksum=response_checksum,
            scoring_run_checksum=run.checksum,
            raw_awarded_points=raw_total,
            raw_maximum_points=max_total,
            outcome=outcome,
            incomplete_reason="PENDING_ASSESSMENT" if pending else None,
            rule_provenance=rule_provenance,
            checksum="",
            created_at=now,
            supersedes_version_id=previous.id if previous else None,
        )
        rv.checksum = checksum(
            {
                "result_id": str(result.id),
                "version": rv.version_number,
                "run": run.checksum,
                "raw": [str(raw_total), str(max_total)],
                "outcome": outcome.value,
                "incomplete": rv.incomplete_reason,
                "rules": rule_provenance,
            }
        )
        session.add(rv)
        session.flush()
        _audit(session, context, "result.version.created", rv)
        _audit(session, context, "scoring.run.completed", run, {"result_version_id": str(rv.id)})
        return run

    @staticmethod
    def assessment_components(run: ScoringRun | None) -> list[dict[str, Any]]:
        """Safe snapshot projection from this exact run, never a latest assessment lookup."""
        if run is None:
            return []
        fields = (
            "skill",
            "assessment_origin",
            "subjective_assessment_version_id",
            "subjective_assessment_checksum",
            "rubric_version_id",
            "rubric_checksum",
            "candidate_response_revision_id",
            "response_checksum",
            "task_spec_id",
            "approved_score",
            "maximum_points",
        )
        result = []
        for score in sorted(
            run.item_scores, key=lambda item: str(item.exam_version_item_binding_id)
        ):
            component = score.scoring_metadata.get("component")
            if (
                isinstance(component, dict)
                and component.get("skill") in {"WRITING", "SPEAKING"}
                and component.get("assessment_origin") == "HUMAN"
            ):
                public = {field: component.get(field) for field in fields}
                if component.get("skill") == "SPEAKING":
                    if (
                        component.get("assessment_mode") != "HUMAN_ONLY"
                        or component.get("evidence_mode") != "AUDIO"
                    ):
                        continue
                    public.update({"assessment_mode": "HUMAN_ONLY", "evidence_mode": "AUDIO"})
                result.append(public)
        return result

    @staticmethod
    def review_projection(session: Session, version: ResultVersion) -> dict[str, Any] | None:
        """Read-only review state for an already-authorized result projection."""
        review = session.scalar(
            select(HumanReviewRequest)
            .where(
                HumanReviewRequest.entity_type == "RESULT_VERSION",
                HumanReviewRequest.entity_id == version.id,
                HumanReviewRequest.reviewed_checksum == version.checksum,
            )
            .order_by(HumanReviewRequest.requested_at.desc(), HumanReviewRequest.id.desc())
            .limit(1)
        )
        if review is None:
            return None
        return {
            "id": str(review.id),
            "status": review.status.value,
            "reviewed_checksum": review.reviewed_checksum,
        }

    @staticmethod
    def request_review(
        session: Session, context: TenantContext, version_id: uuid.UUID
    ) -> HumanReviewRequest:
        AuthorizationService.require_permission(context, "result.review", context.organisation_id)
        version = ScoringService._consequential_version(
            session, context, version_id, permission="result.review"
        )
        if version.status is ResultVersionStatus.INCOMPLETE:
            raise ScoringError("incomplete result cannot enter release review")
        if version.status not in {
            ResultVersionStatus.REVIEW_REQUIRED,
            ResultVersionStatus.REJECTED,
        }:
            raise ScoringError("result is not awaiting review")
        review = HumanReviewRequest(
            review_type="RESULT_RELEASE",
            entity_type="RESULT_VERSION",
            entity_id=version.id,
            status=HumanReviewStatus.PENDING,
            requested_by=context.user_id,
            requested_at=_now(),
            reviewed_checksum=version.checksum,
        )
        session.add(review)
        version.review_requested_at = _now()
        session.flush()
        _audit(session, context, "result.review_requested", version)
        return review

    @staticmethod
    def approve_review(
        session: Session, context: TenantContext, version_id: uuid.UUID
    ) -> ResultVersion:
        AuthorizationService.require_permission(context, "result.review", context.organisation_id)
        version = ScoringService._consequential_version(
            session, context, version_id, permission="result.review"
        )
        if version.status is ResultVersionStatus.SUPERSEDED:
            raise ScoringError("stale review artifact")
        if version.status is not ResultVersionStatus.REVIEW_REQUIRED:
            raise ScoringError("result is not awaiting review")
        review = session.scalar(
            select(HumanReviewRequest).where(
                HumanReviewRequest.entity_type == "RESULT_VERSION",
                HumanReviewRequest.entity_id == version.id,
                HumanReviewRequest.status == HumanReviewStatus.PENDING,
            )
        )
        if review is None or review.reviewed_checksum != version.checksum:
            raise ScoringError("stale review artifact")
        review.status = HumanReviewStatus.APPROVED
        review.decided_by = context.user_id
        review.decided_at = _now()
        version.status = ResultVersionStatus.APPROVED
        version.approved_by_user_id = context.user_id
        version.approved_at = _now()
        session.flush()
        _audit(session, context, "result.approved", version)
        return version

    @staticmethod
    def release(session: Session, context: TenantContext, version_id: uuid.UUID) -> ResultVersion:
        AuthorizationService.require_permission(context, "result.release", context.organisation_id)
        version = ScoringService._consequential_version(
            session, context, version_id, permission="result.release"
        )
        if version.status is not ResultVersionStatus.APPROVED:
            raise ScoringError("result requires human approval")
        if any(
            evaluation.get("status") in {"FAIL", "ERROR"}
            for evaluation in version.rule_provenance.get("evaluations", [])
        ):
            raise ScoringError("result is blocked by governed RulesEngine evaluation")
        result = session.get(AssessmentResult, version.result_id)
        attempt = session.get(CandidateExamAttempt, result.attempt_id) if result else None
        exam = session.get(ExamVersion, result.exam_version_id) if result else None
        if (
            attempt is None
            or exam is None
            or exam.classification is AssessmentClassification.OFFICIAL_CERTIFICATION
        ):
            raise ScoringError("CERTIFICATION.NOT_AUTHORIZED")
        version.status = ResultVersionStatus.RELEASED
        version.released_by_user_id = context.user_id
        version.released_at = _now()
        session.flush()
        _audit(session, context, "result.released", version)
        return version

    @staticmethod
    def reject_review(
        session: Session, context: TenantContext, version_id: uuid.UUID
    ) -> ResultVersion:
        AuthorizationService.require_permission(context, "result.review", context.organisation_id)
        version = ScoringService._consequential_version(
            session, context, version_id, permission="result.review"
        )
        if version.status is ResultVersionStatus.SUPERSEDED:
            raise ScoringError("stale review artifact")
        if version.status is not ResultVersionStatus.REVIEW_REQUIRED:
            raise ScoringError("result is not awaiting review")
        review = session.scalar(
            select(HumanReviewRequest).where(
                HumanReviewRequest.entity_type == "RESULT_VERSION",
                HumanReviewRequest.entity_id == version.id,
                HumanReviewRequest.status == HumanReviewStatus.PENDING,
            )
        )
        if review is None or review.reviewed_checksum != version.checksum:
            raise ScoringError("stale review artifact")
        review.status = HumanReviewStatus.REJECTED
        review.decided_by = context.user_id
        review.decided_at = _now()
        version.status = ResultVersionStatus.REJECTED
        version.reviewed_by_user_id = context.user_id
        version.reviewed_at = _now()
        session.flush()
        _audit(session, context, "result.rejected", version)
        return version

    @staticmethod
    def diff(
        session: Session, context: TenantContext, left_id: uuid.UUID, right_id: uuid.UUID
    ) -> dict[str, Any]:
        left = ScoringService._owned_version(session, context, left_id)
        right = ScoringService._owned_version(session, context, right_id)
        return {
            "score_changed": left.raw_awarded_points != right.raw_awarded_points
            or left.raw_maximum_points != right.raw_maximum_points,
            "outcome_changed": left.outcome != right.outcome,
            "completion_changed": left.status != right.status
            or left.incomplete_reason != right.incomplete_reason,
            "rule_provenance_changed": left.rule_provenance != right.rule_provenance,
            "scoring_run_changed": left.scoring_run_checksum != right.scoring_run_checksum,
        }

    @staticmethod
    def _owned_version(
        session: Session, context: TenantContext, version_id: uuid.UUID
    ) -> ResultVersion:
        version = session.get(ResultVersion, version_id)
        result = session.get(AssessmentResult, version.result_id) if version else None
        if version is None or result is None or result.organisation_id != context.organisation_id:
            raise ScoringError("result is not available in this organisation")
        return version

    @staticmethod
    def _consequential_version(
        session: Session, context: TenantContext, version_id: uuid.UUID, *, permission: str
    ) -> ResultVersion:
        preliminary = ScoringService._owned_version(session, context, version_id)
        parent = preliminary.result
        attempt = session.scalar(
            select(CandidateExamAttempt)
            .where(CandidateExamAttempt.id == parent.attempt_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = session.scalar(
            select(AssessmentResult)
            .where(AssessmentResult.id == parent.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        version = session.scalar(
            select(ResultVersion)
            .where(ResultVersion.id == version_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if attempt is None or result is None or version is None:
            raise ScoringError("result provenance is unavailable")
        if max(result.versions, key=lambda row: row.version_number).id != version.id:
            raise ScoringError("stale result version")
        run = session.get(ScoringRun, version.scoring_run_id)
        if run is None:
            raise ScoringError("result scoring run is unavailable")
        if any(
            score.scoring_metadata.get("component", {}).get("skill") == "SPEAKING"
            for score in run.item_scores
            if isinstance(score.scoring_metadata.get("component"), dict)
        ):
            require_active_tenant_permission(session, context, permission, result.organisation_id)
        if version.status is not ResultVersionStatus.INCOMPLETE:
            ScoringService.assert_run_current_authority(session, run)
        return version

    @staticmethod
    def candidate_results(session: Session, context: TenantContext) -> list[dict[str, Any]]:
        rows = session.execute(
            select(ResultVersion, AssessmentResult)
            .join(AssessmentResult, ResultVersion.result_id == AssessmentResult.id)
            .where(
                AssessmentResult.candidate_user_id == context.user_id,
                AssessmentResult.organisation_id == context.organisation_id,
                ResultVersion.status == ResultVersionStatus.RELEASED,
            )
        ).all()
        return [ScoringService.safe_result(version, result) for version, result in rows]

    @staticmethod
    def safe_result(version: ResultVersion, result: AssessmentResult) -> dict[str, Any]:
        return {
            "result_id": str(result.id),
            "result_version_id": str(version.id),
            "attempt_id": str(result.attempt_id),
            "status": version.status.value,
            "raw_awarded_points": str(version.raw_awarded_points)
            if version.raw_awarded_points is not None
            else None,
            "raw_maximum_points": str(version.raw_maximum_points)
            if version.raw_maximum_points is not None
            else None,
            "outcome": version.outcome.value if version.outcome else None,
            "released_at": version.released_at,
        }
