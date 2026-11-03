"""Governed human Writing and rubric publication; independent of AI execution."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationService, TenantContext
from app.models import (
    CandidateExamAttempt,
    CandidateResponseRevision,
    ExamVersionItemBinding,
    HumanReviewRequest,
    ItemScore,
    QuestionBankItemVersion,
    ResultAppeal,
    ResultAppealVersion,
    ResultCorrection,
    ResultVersion,
    RubricVersion,
    SubjectiveAssessment,
    SubjectiveAssessmentVersion,
    SubjectiveCriterionAssessment,
    WritingGovernanceOperation,
)
from app.models.enums import (
    AppealDecision,
    CorrectionStatus,
    CorrectionType,
    HumanReviewStatus,
    QuestionBankSkill,
    RubricVersionStatus,
    SubjectiveAssessmentStatus,
    SubjectiveCriterionStatus,
)
from app.writing_assessment.service import (
    RubricError,
    RubricService,
    SubjectiveAssessmentService,
    _decimal,
    canonical_checksum,
    validate_rubric,
)


def _permission(context: TenantContext, permission: str) -> None:
    if context.organisation_id is None:
        raise PermissionError("organisation context required")
    AuthorizationService.require_permission(context, permission, context.organisation_id)


def _receipt(
    session: Session,
    context: TenantContext,
    operation_id: uuid.UUID,
    action: str,
    target_id: uuid.UUID,
    payload: Any,
) -> WritingGovernanceOperation | None:
    receipt = session.scalar(
        select(WritingGovernanceOperation).where(
            WritingGovernanceOperation.organisation_id == context.organisation_id,
            WritingGovernanceOperation.operation_id == operation_id,
        )
    )
    if receipt is not None and (
        receipt.actor_id != context.user_id
        or receipt.action != action
        or receipt.target_id != target_id
        or receipt.request_checksum != canonical_checksum(payload)
    ):
        raise RubricError("operation_id was already used for a different governance request")
    return receipt


def _record(
    session: Session,
    context: TenantContext,
    operation_id: uuid.UUID,
    action: str,
    target_id: uuid.UUID,
    payload: Any,
    result_id: uuid.UUID,
    *,
    entity_type: str,
    checksum: str,
) -> None:
    session.add(
        WritingGovernanceOperation(
            organisation_id=context.organisation_id,
            operation_id=operation_id,
            actor_id=context.user_id,
            action=action,
            target_id=target_id,
            request_checksum=canonical_checksum(payload),
            result_id=result_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    AuditService.record(
        session,
        context,
        event_type=action,
        entity_type=entity_type,
        entity_id=str(result_id),
        organisation_id=context.organisation_id,
        require_audit_permission=False,
        request_id=str(operation_id),
        new_state_hash=checksum,
        metadata={"operation_id": str(operation_id), "assessment_origin": "HUMAN"}
        if entity_type == "SubjectiveAssessmentVersion"
        else {"operation_id": str(operation_id)},
    )


def review_for(
    session: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    *,
    review_type: str | None = None,
) -> HumanReviewRequest | None:
    query = select(HumanReviewRequest).where(
        HumanReviewRequest.entity_type == entity_type,
        HumanReviewRequest.entity_id == entity_id,
    )
    if review_type is not None:
        query = query.where(HumanReviewRequest.review_type == review_type)
    return session.scalar(query.order_by(HumanReviewRequest.requested_at.desc()))


class RubricGovernanceService:
    @staticmethod
    def _version(session: Session, context: TenantContext, version_id: uuid.UUID) -> RubricVersion:
        _permission(context, "rubric.manage")
        version = session.scalar(
            select(RubricVersion)
            .where(RubricVersion.id == version_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if version is None or version.rubric.organisation_id != context.organisation_id:
            raise RubricError("rubric version is not available in this organisation")
        return version

    @staticmethod
    def _validate(version: RubricVersion) -> None:
        if not version.task_spec_id:
            raise RubricError("human Writing rubric requires an exact task specification")
        if version.skill is not QuestionBankSkill.WRITING:
            raise RubricError("human rubric lifecycle currently supports Writing only")
        if canonical_checksum(RubricService.checksum_payload(version)) != version.checksum:
            raise RubricError("rubric content does not match its reviewed checksum")

    @classmethod
    def transition(
        cls,
        session: Session,
        context: TenantContext,
        version_id: uuid.UUID,
        *,
        action: str,
        operation_id: uuid.UUID,
        expected_checksum: str,
    ) -> RubricVersion:
        version = cls._version(session, context, version_id)
        payload = {"expected_checksum": expected_checksum}
        event = f"rubric.{action}"
        receipt = _receipt(session, context, operation_id, event, version.id, payload)
        if receipt:
            return version
        if version.checksum != expected_checksum:
            raise RubricError("stale rubric checksum")
        cls._validate(version)
        from app.models import StandardPackVersion

        pack = session.get(StandardPackVersion, version.standard_pack_version_id)
        if pack is None:
            raise RubricError("rubric StandardPackVersion is unavailable")
        validate_rubric(
            criteria=version.criteria,
            performance_levels=version.performance_levels,
            aggregation_policy=version.aggregation_policy,
            evidence_policy=version.evidence_policy,
            assessment_family=version.assessment_family,
            language=version.language,
            skill=version.skill,
            standard_pack=pack,
            schema_version=version.schema_version,
        )
        now = datetime.now(timezone.utc)
        review = review_for(session, "RUBRIC_VERSION", version.id, review_type="RUBRIC_PUBLICATION")
        if action == "review_requested":
            if version.status is not RubricVersionStatus.DRAFT:
                raise RubricError("only a draft rubric can enter review")
            version.status = RubricVersionStatus.HUMAN_REVIEW
            session.add(
                HumanReviewRequest(
                    review_type="RUBRIC_PUBLICATION",
                    entity_type="RUBRIC_VERSION",
                    entity_id=version.id,
                    status=HumanReviewStatus.PENDING,
                    requested_by=context.user_id,
                    requested_at=now,
                    reviewed_checksum=version.checksum,
                )
            )
        elif action == "approved":
            if version.status is not RubricVersionStatus.HUMAN_REVIEW or review is None:
                raise RubricError("rubric is not awaiting human review")
            if (
                review.status is not HumanReviewStatus.PENDING
                or review.reviewed_checksum != version.checksum
            ):
                raise RubricError("stale rubric review")
            if review.requested_by == context.user_id:
                raise PermissionError("rubric approval requires an independent reviewer")
            review.status, review.decided_by, review.decided_at = (
                HumanReviewStatus.APPROVED,
                context.user_id,
                now,
            )
            review.decision = "APPROVED"
            version.status = RubricVersionStatus.APPROVED
            version.approved_at, version.approved_by_user_id = now, context.user_id
        elif action == "published":
            if (
                version.status is not RubricVersionStatus.APPROVED
                or review is None
                or review.status is not HumanReviewStatus.APPROVED
            ):
                raise RubricError("rubric requires exact human approval before publication")
            if review.reviewed_checksum != version.checksum:
                raise RubricError("stale rubric publication review")
            version.status = RubricVersionStatus.PUBLISHED
            version.published_at, version.published_by_user_id = now, context.user_id
        else:
            raise RubricError("unsupported rubric lifecycle action")
        _record(
            session,
            context,
            operation_id,
            event,
            version.id,
            payload,
            version.id,
            entity_type="RubricVersion",
            checksum=version.checksum,
        )
        session.flush()
        return version

    @classmethod
    def request_review(
        cls, session: Session, context: TenantContext, version_id: uuid.UUID, **kwargs: Any
    ) -> RubricVersion:
        return cls.transition(session, context, version_id, action="review_requested", **kwargs)

    @classmethod
    def approve(
        cls, session: Session, context: TenantContext, version_id: uuid.UUID, **kwargs: Any
    ) -> RubricVersion:
        return cls.transition(session, context, version_id, action="approved", **kwargs)

    @classmethod
    def publish(
        cls, session: Session, context: TenantContext, version_id: uuid.UUID, **kwargs: Any
    ) -> RubricVersion:
        return cls.transition(session, context, version_id, action="published", **kwargs)


class HumanWritingService:
    @staticmethod
    def normalize_correction_input(
        rubric: RubricVersion, corrections: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        known_codes = {str(row["code"]) for row in rubric.criteria}
        normalized: dict[str, dict[str, Any]] = {}
        for code, value in corrections.items():
            if (
                code not in known_codes
                or not isinstance(value, dict)
                or not set(value) <= {"approved_points", "approved_level"}
            ):
                raise RubricError("HUMAN correction contains unknown criterion values")
            normalized[code] = {}
            if "approved_points" in value:
                points = _decimal(value["approved_points"])
                if points != points.quantize(Decimal("0.0001")):
                    raise RubricError("HUMAN correction supports at most four decimal places")
                normalized[code]["approved_points"] = format(points, ".4f")
            if "approved_level" in value:
                normalized[code]["approved_level"] = str(value["approved_level"])
        return normalized

    @staticmethod
    def authorize_correction_actor(
        context: TenantContext, source: SubjectiveAssessmentVersion
    ) -> None:
        _permission(context, "subjective.review")
        _permission(context, "appeal.correct")
        if context.organisation_id != source.assessment.organisation_id:
            raise PermissionError("HUMAN correction is outside this organisation")
        assessor = source.provenance.get("assessed_by")
        if (
            not assessor
            or str(context.user_id) == assessor
            or context.user_id == source.assessment.candidate_user_id
        ):
            raise PermissionError("HUMAN correction requires an independent authorized reviewer")

    @staticmethod
    def correction_record(
        session: Session, source: SubjectiveAssessmentVersion, correction_id: uuid.UUID
    ) -> ResultCorrection:
        """Resolve only the existing upheld correction chain for this exact source."""
        correction = session.get(ResultCorrection, correction_id)
        appeal = session.get(ResultAppeal, correction.appeal_id) if correction else None
        decision = (
            session.get(ResultAppealVersion, correction.appeal_version_id) if correction else None
        )
        result = (
            session.get(ResultVersion, correction.source_result_version_id) if correction else None
        )
        if (
            correction is None
            or appeal is None
            or decision is None
            or result is None
            or correction.correction_type is not CorrectionType.WRITING_REASSESSMENT
            or correction.organisation_id != source.assessment.organisation_id
            or appeal.organisation_id != source.assessment.organisation_id
            or appeal.candidate_user_id != source.assessment.candidate_user_id
            or appeal.attempt_id != source.attempt_id
            or correction.source_result_version_id != appeal.challenged_result_version_id
            or correction.source_result_checksum != result.checksum
            or result.checksum != appeal.challenged_result_checksum
            or decision.appeal_id != appeal.id
            or decision.decision not in {AppealDecision.UPHOLD, AppealDecision.PARTIALLY_UPHOLD}
        ):
            raise RubricError("HUMAN correction requires an exact upheld appeal chain")
        score = session.scalar(
            select(ItemScore).where(
                ItemScore.scoring_run_id == result.scoring_run_id,
                ItemScore.exam_version_item_binding_id == source.exam_version_item_binding_id,
            )
        )
        if (
            score is None
            or score.scoring_metadata.get("subjective_assessment_version_id") != str(source.id)
            or score.scoring_metadata.get("subjective_assessment_checksum") != source.checksum
        ):
            raise RubricError("HUMAN correction source was not used by the challenged result")
        return correction

    @classmethod
    def authorize_correction(
        cls,
        session: Session,
        context: TenantContext,
        source: SubjectiveAssessmentVersion,
        review: HumanReviewRequest,
        correction_id: uuid.UUID | None,
    ) -> None:
        cls.authorize_correction_actor(context, source)
        if correction_id is None:
            raise RubricError("HUMAN correction requires a governed ResultCorrection")
        correction = cls.correction_record(session, source, correction_id)
        if (
            correction.status is not CorrectionStatus.IN_PROGRESS
            or correction.created_by_user_id != context.user_id
            or source.status is not SubjectiveAssessmentStatus.APPROVED
            or review.review_type != "RESULT_CORRECTION"
            or review.entity_id != source.id
            or review.entity_type != "SUBJECTIVE_ASSESSMENT_VERSION"
            or review.status is not HumanReviewStatus.PENDING
            or review.requested_by != context.user_id
            or review.reviewed_checksum != source.checksum
        ):
            raise RubricError("HUMAN correction review does not match its governed source")

    @classmethod
    def validate_corrected_approval(
        cls, session: Session, version: SubjectiveAssessmentVersion
    ) -> None:
        try:
            correction_id = uuid.UUID(str(version.provenance["result_correction_id"]))
            review_id = uuid.UUID(str(version.provenance["correction_review_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RubricError("HUMAN correction provenance is incomplete") from exc
        source = session.get(SubjectiveAssessmentVersion, version.supersedes_version_id)
        review = session.get(HumanReviewRequest, review_id)
        if source is None or source.assessment_origin != "HUMAN":
            raise RubricError("HUMAN correction source is unavailable")
        correction = cls.correction_record(session, source, correction_id)
        if (
            correction.status
            not in {CorrectionStatus.READY_FOR_RESCORING, CorrectionStatus.RESCORED}
            or correction.new_component_version_id != version.id
            or source.status is not SubjectiveAssessmentStatus.SUPERSEDED
            or version.provenance.get("source_version_id") != str(source.id)
            or version.provenance.get("source_checksum") != source.checksum
            or review is None
            or review.review_type != "RESULT_CORRECTION"
            or review.entity_type != "SUBJECTIVE_ASSESSMENT_VERSION"
            or review.entity_id != source.id
            or review.status is not HumanReviewStatus.APPROVED
            or review.reviewed_checksum != source.checksum
            or review.decided_by != correction.created_by_user_id
            or review.requested_by != correction.created_by_user_id
            or str(review.decided_by) != version.provenance.get("reviewed_by")
            or str(review.decided_by) == source.provenance.get("assessed_by")
            or review.decided_by == source.assessment.candidate_user_id
        ):
            raise RubricError("HUMAN correction lacks independent upheld approval provenance")
        identity_fields = (
            "subjective_assessment_id",
            "attempt_id",
            "exam_version_id",
            "exam_version_item_binding_id",
            "question_bank_item_version_id",
            "candidate_response_id",
            "candidate_response_revision_id",
            "rubric_version_id",
            "standard_pack_version_id",
            "response_checksum",
            "rubric_checksum",
        )
        if any(getattr(source, field) != getattr(version, field) for field in identity_fields):
            raise RubricError("HUMAN correction changed the pinned assessment evidence")
        expected = canonical_checksum(
            {
                "source_checksum": source.checksum,
                "approved_score": str(version.approved_score),
                "corrections": version.provenance.get("correction_input"),
                "supersedes_version_id": str(source.id),
            }
        )
        if version.checksum != expected:
            raise RubricError("HUMAN correction checksum does not match")

    @staticmethod
    def assert_human_mode(rubric: RubricVersion) -> None:
        if (
            rubric.ai_assessment_policy.get("mode") != "HUMAN_ONLY"
            or rubric.ai_assessment_policy.get("enabled") is not False
        ):
            raise RubricError("rubric does not explicitly authorize HUMAN_ONLY assessment")

    @staticmethod
    def _version(
        session: Session, context: TenantContext, version_id: uuid.UUID, permission: str
    ) -> SubjectiveAssessmentVersion:
        _permission(context, permission)
        version = session.scalar(
            select(SubjectiveAssessmentVersion)
            .where(SubjectiveAssessmentVersion.id == version_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if version is None or version.assessment.organisation_id != context.organisation_id:
            raise RubricError("human assessment is not available in this organisation")
        if version.assessment.candidate_user_id == context.user_id:
            raise PermissionError("candidate cannot assess or review their own response")
        if version.assessment_origin != "HUMAN":
            raise RubricError("assessment is not a human-origin Writing assessment")
        if any(
            (
                version.ai_run_id,
                version.workflow_version_id,
                version.agent_version_id,
                version.prompt_version_id,
                version.model_profile_id,
                version.ai_proposal,
            )
        ):
            raise RubricError("human assessment contains incompatible AI provenance")
        return version

    @staticmethod
    def _pins(
        session: Session, version: SubjectiveAssessmentVersion
    ) -> tuple[RubricVersion, CandidateResponseRevision]:
        rubric = session.get(RubricVersion, version.rubric_version_id)
        revision = session.get(CandidateResponseRevision, version.candidate_response_revision_id)
        binding = session.get(ExamVersionItemBinding, version.exam_version_item_binding_id)
        if rubric is None or revision is None or binding is None:
            raise RubricError("pinned Writing evidence is unavailable")
        if rubric.rubric.organisation_id != version.assessment.organisation_id:
            raise RubricError("rubric is not available in the assessment organisation")
        if rubric.status not in {RubricVersionStatus.PUBLISHED, RubricVersionStatus.ACTIVE}:
            raise RubricError("human assessment requires a published rubric")
        HumanWritingService.assert_human_mode(rubric)
        if (
            rubric.checksum != version.rubric_checksum
            or canonical_checksum(RubricService.checksum_payload(rubric)) != version.rubric_checksum
        ):
            raise RubricError("pinned rubric checksum is stale")
        if (
            revision.candidate_response_id != version.candidate_response_id
            or revision.attempt_id != version.attempt_id
            or revision.response.exam_version_item_binding_id
            != version.exam_version_item_binding_id
            or revision.payload_checksum != version.response_checksum
            or canonical_checksum(revision.payload) != version.response_checksum
        ):
            raise RubricError("pinned response checksum is stale")
        if (
            rubric.task_spec_id is None
            or binding.task.standard_pack_task_spec_id != rubric.task_spec_id
        ):
            raise RubricError("rubric is not bound to the exact Writing task")
        if binding.question_bank_item_version_id != version.question_bank_item_version_id:
            raise RubricError("pinned Writing item does not match exam binding")
        item = session.get(QuestionBankItemVersion, version.question_bank_item_version_id)
        try:
            bound_rubric_id = (
                uuid.UUID(item.rubric_reference) if item and item.rubric_reference else None
            )
        except (ValueError, TypeError, AttributeError):
            bound_rubric_id = None
        if bound_rubric_id != rubric.id:
            raise RubricError("Writing item does not pin this exact rubric version")
        if binding.task.section.module.exam_version_id != version.exam_version_id:
            raise RubricError("pinned Writing task does not match the exact exam version")
        return rubric, revision

    @classmethod
    def approved_scoring_evidence(
        cls,
        session: Session,
        version: SubjectiveAssessmentVersion,
        attempt: CandidateExamAttempt,
        item: QuestionBankItemVersion,
        response: dict[str, Any],
        maximum: Decimal,
    ) -> dict[str, Any]:
        """Validate pinned HUMAN evidence without disclosing rationale or quotes."""
        if (
            version.assessment_origin != "HUMAN"
            or version.status is not SubjectiveAssessmentStatus.APPROVED
        ):
            raise RubricError("Writing scoring requires an approved HUMAN assessment")
        if any(
            (
                version.ai_run_id,
                version.workflow_version_id,
                version.agent_version_id,
                version.prompt_version_id,
                version.model_profile_id,
                version.ai_proposal,
            )
        ):
            raise RubricError("HUMAN Writing scoring cannot consume AI provenance")
        if (
            version.attempt_id != attempt.id
            or version.exam_version_id != attempt.exam_version_id
            or version.assessment.organisation_id != attempt.organisation_id
            or version.assessment.candidate_user_id != attempt.candidate_user_id
            or version.question_bank_item_version_id != item.id
            or str(version.standard_pack_version_id)
            != str(attempt.runtime_plan.get("standard_pack_version_id"))
            or str(version.candidate_response_id) != response.get("response_id")
            or str(version.candidate_response_revision_id) != response.get("response_revision_id")
            or version.response_checksum != response.get("payload_checksum")
        ):
            raise RubricError("approved HUMAN Writing evidence does not match the scored response")
        rubric, revision = cls._pins(session, version)
        if (
            rubric.skill is not QuestionBankSkill.WRITING
            or item.skill is not QuestionBankSkill.WRITING
        ):
            raise RubricError("HUMAN scoring integration supports Writing only")
        if any(
            row.status is not SubjectiveCriterionStatus.APPROVED
            or row.approved_points is None
            or row.approved_level is None
            or row.confidence is not None
            or row.ai_run_step_id is not None
            for row in version.criterion_assessments
        ):
            raise RubricError("HUMAN Writing requires complete approved criterion values")
        entries, values = cls.validate_entries(
            rubric,
            str(revision.payload.get("text", "")),
            [
                {
                    "criterion_code": row.criterion_code,
                    "level": row.approved_level,
                    "points": str(row.approved_points),
                    "rationale": row.rationale,
                    "evidence": row.evidence,
                }
                for row in version.criterion_assessments
            ],
        )
        aggregate = SubjectiveAssessmentService._aggregate(rubric, values)
        if (
            version.approved_score is None
            or not aggregate.is_finite()
            or aggregate != version.approved_score
            or aggregate < 0
            or aggregate > maximum
        ):
            raise RubricError("approved HUMAN aggregate does not match its criterion values")
        approved_values = {
            entry["criterion_code"]: {"level": entry["level"], "points": entry["points"]}
            for entry in entries
        }
        if approved_values != version.human_approved_values:
            raise RubricError("approved HUMAN criterion provenance does not match")
        if version.provenance.get("human_correction"):
            cls.validate_corrected_approval(session, version)
        else:
            cls.validate_initial_approval(session, version)
        return {
            "subjective_assessment_checksum": version.checksum,
            "component": {
                "skill": "WRITING",
                "assessment_origin": "HUMAN",
                "subjective_assessment_version_id": str(version.id),
                "subjective_assessment_checksum": version.checksum,
                "rubric_version_id": str(rubric.id),
                "rubric_checksum": version.rubric_checksum,
                "candidate_response_revision_id": str(version.candidate_response_revision_id),
                "response_checksum": version.response_checksum,
                "task_spec_id": str(rubric.task_spec_id),
                "approved_score": str(aggregate),
                "maximum_points": str(maximum),
            },
            "approved_criteria": [
                {
                    "criterion_code": entry["criterion_code"],
                    "level": entry["level"],
                    "points": entry["points"],
                }
                for entry in entries
            ],
        }

    @staticmethod
    def validate_initial_approval(session: Session, version: SubjectiveAssessmentVersion) -> None:
        review = review_for(
            session,
            "SUBJECTIVE_ASSESSMENT_VERSION",
            version.id,
            review_type="HUMAN_WRITING_ASSESSMENT",
        )
        if (
            review is None
            or review.status is not HumanReviewStatus.APPROVED
            or review.decided_by is None
            or review.decided_by == review.requested_by
            or str(review.decided_by) != version.provenance.get("reviewed_by")
        ):
            raise RubricError("HUMAN Writing requires the exact independent approval")
        expected_checksum = canonical_checksum(
            {
                "reviewed_checksum": review.reviewed_checksum,
                "decision": "APPROVED",
                "approved_values": version.human_approved_values,
                "approved_score": str(version.approved_score),
                "provenance": version.provenance,
            }
        )
        if version.checksum != expected_checksum:
            raise RubricError("approved HUMAN assessment checksum does not match")

    @classmethod
    def initialize(
        cls,
        session: Session,
        context: TenantContext,
        *,
        operation_id: uuid.UUID,
        attempt_id: uuid.UUID,
        binding_id: uuid.UUID,
        rubric_version_id: uuid.UUID,
        response_checksum: str,
        rubric_checksum: str,
    ) -> SubjectiveAssessmentVersion:
        _permission(context, "subjective.assess")
        attempt = session.scalar(
            select(CandidateExamAttempt)
            .where(CandidateExamAttempt.id == attempt_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if attempt is None or attempt.organisation_id != context.organisation_id:
            raise RubricError("attempt is not available in this organisation")
        if attempt.candidate_user_id == context.user_id:
            raise PermissionError("candidate cannot assess their own response")
        payload = {
            "binding_id": str(binding_id),
            "rubric_version_id": str(rubric_version_id),
            "response_checksum": response_checksum,
            "rubric_checksum": rubric_checksum,
        }
        receipt = _receipt(
            session,
            context,
            operation_id,
            "subjective_assessment.human_initialized",
            attempt.id,
            payload,
        )
        if receipt:
            result = session.get(SubjectiveAssessmentVersion, receipt.result_id)
            if result is None:
                raise RubricError("governance replay result is unavailable")
            return result
        existing = session.scalar(
            select(SubjectiveAssessment).where(
                SubjectiveAssessment.attempt_id == attempt_id,
                SubjectiveAssessment.exam_version_item_binding_id == binding_id,
            )
        )
        if existing:
            current = SubjectiveAssessmentService.current_version(existing)
            if current and current.status not in {
                SubjectiveAssessmentStatus.REJECTED,
                SubjectiveAssessmentStatus.CHANGES_REQUESTED,
                SubjectiveAssessmentStatus.FAILED,
                SubjectiveAssessmentStatus.VOIDED,
            }:
                raise RubricError("Writing assessment already has a current review or approval")
        version = SubjectiveAssessmentService.create(
            session,
            context,
            attempt_id=attempt_id,
            binding_id=binding_id,
            rubric_version_id=rubric_version_id,
            _human_origin=True,
        )
        version.assessment_origin = "HUMAN"
        rubric, _revision = cls._pins(session, version)
        if rubric.skill is not QuestionBankSkill.WRITING:
            raise RubricError("human-only workflow supports Writing only")
        if (
            version.response_checksum != response_checksum
            or version.rubric_checksum != rubric_checksum
        ):
            raise RubricError("stale Writing response or rubric checksum")
        version.status = SubjectiveAssessmentStatus.REVIEW_REQUIRED
        version.provenance = {
            "assessment_origin": "HUMAN",
            "candidate_text_untrusted": True,
            "initialized_by": str(context.user_id),
            "initialized_at": datetime.now(timezone.utc).isoformat(),
        }
        version.checksum = canonical_checksum(
            {
                "origin": "HUMAN",
                "response_revision_id": str(version.candidate_response_revision_id),
                "response_checksum": response_checksum,
                "rubric_checksum": rubric_checksum,
                "task_spec_id": str(rubric.task_spec_id),
                "provenance": version.provenance,
            }
        )
        _record(
            session,
            context,
            operation_id,
            "subjective_assessment.human_initialized",
            attempt.id,
            payload,
            version.id,
            entity_type="SubjectiveAssessmentVersion",
            checksum=version.checksum,
        )
        session.flush()
        return version

    @staticmethod
    def validate_entries(
        rubric: RubricVersion, text: str, entries: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Decimal]]:
        expected = SubjectiveAssessmentService._criterion_map(rubric)
        if (
            not isinstance(entries, list)
            or len(entries) != len(expected)
            or len(entries) > 50
            or {str(item.get("criterion_code")) for item in entries} != set(expected)
        ):
            raise RubricError("human proposal must cover every rubric criterion exactly once")
        levels = {str(level["code"]) for level in rubric.performance_levels}
        normalized, values = [], {}
        for entry in entries:
            code = str(entry["criterion_code"])
            points = _decimal(entry.get("points"))
            level = str(entry.get("level"))
            rationale = entry.get("rationale")
            if (
                not points.is_finite()
                or points < 0
                or points > _decimal(expected[code]["max_points"])
                or level not in levels
            ):
                raise RubricError("human criterion score or level exceeds rubric bounds")
            if points != points.quantize(Decimal("0.0001")):
                raise RubricError("human criterion points support at most four decimal places")
            if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 2000:
                raise RubricError("human criterion rationale is required and bounded")
            evidence = entry.get("evidence", [])
            if not isinstance(evidence, list) or len(evidence) > 20:
                raise RubricError("human evidence must contain at most 20 exact text references")
            for reference in evidence:
                if not isinstance(reference, dict) or set(reference) != {"quote", "start", "end"}:
                    raise RubricError("human evidence requires an exact text span")
                start, end, quote = reference["start"], reference["end"], reference["quote"]
                if (
                    type(start) is not int
                    or type(end) is not int
                    or not isinstance(quote, str)
                    or not 0 <= start < end <= len(text)
                    or len(quote) > 2000
                    or text[start:end] != quote
                ):
                    raise RubricError("human evidence does not match the pinned response")
            if rubric.evidence_policy.get("required") and len(evidence) < int(
                rubric.evidence_policy.get("minimum_references", 1)
            ):
                raise RubricError("human proposal lacks required response evidence")
            values[code] = points
            normalized.append(
                {
                    "criterion_code": code,
                    "points": format(points, ".4f"),
                    "level": level,
                    "rationale": rationale.strip(),
                    "evidence": evidence,
                }
            )
        return sorted(normalized, key=lambda item: item["criterion_code"]), values

    @staticmethod
    def proposal_checksum(
        version: SubjectiveAssessmentVersion, entries: list[dict[str, Any]]
    ) -> str:
        return canonical_checksum(
            {
                "origin": "HUMAN",
                "response_revision_id": str(version.candidate_response_revision_id),
                "response_checksum": version.response_checksum,
                "rubric_version_id": str(version.rubric_version_id),
                "rubric_checksum": version.rubric_checksum,
                "criteria": entries,
                "proposed_score": format(version.proposed_score, ".4f")
                if version.proposed_score is not None
                else None,
                "provenance": version.provenance,
            }
        )

    @classmethod
    def propose(
        cls,
        session: Session,
        context: TenantContext,
        version_id: uuid.UUID,
        *,
        operation_id: uuid.UUID,
        expected_checksum: str,
        criteria: list[dict[str, Any]],
    ) -> SubjectiveAssessmentVersion:
        version = cls._version(session, context, version_id, "subjective.assess")
        payload = {"expected_checksum": expected_checksum, "criteria": criteria}
        receipt = _receipt(
            session,
            context,
            operation_id,
            "subjective_assessment.human_proposed",
            version.id,
            payload,
        )
        if receipt:
            return version
        if (
            version.status is not SubjectiveAssessmentStatus.REVIEW_REQUIRED
            or version.checksum != expected_checksum
        ):
            raise RubricError("human assessment is not an unchanged draft awaiting criterion entry")
        rubric, revision = cls._pins(session, version)
        entries, values = cls.validate_entries(
            rubric, str(revision.payload.get("text", "")), criteria
        )
        if version.criterion_assessments:
            raise RubricError("human proposal criteria already exist")
        for entry in entries:
            version.criterion_assessments.append(
                SubjectiveCriterionAssessment(
                    criterion_code=entry["criterion_code"],
                    proposed_level=entry["level"],
                    proposed_points=_decimal(entry["points"]),
                    confidence=None,
                    evidence=entry["evidence"],
                    rationale=entry["rationale"],
                    uncertainties=[],
                    disagreement={},
                    status=SubjectiveCriterionStatus.PROPOSED,
                )
            )
        version.proposed_score = SubjectiveAssessmentService._aggregate(rubric, values)
        version.provenance = {
            **version.provenance,
            "assessed_by": str(context.user_id),
            "initialized_checksum": expected_checksum,
            "assessed_at": datetime.now(timezone.utc).isoformat(),
        }
        version.checksum = cls.proposal_checksum(version, entries)
        version.status = SubjectiveAssessmentStatus.IN_REVIEW
        session.add(
            HumanReviewRequest(
                review_type="HUMAN_WRITING_ASSESSMENT",
                entity_type="SUBJECTIVE_ASSESSMENT_VERSION",
                entity_id=version.id,
                status=HumanReviewStatus.PENDING,
                requested_by=context.user_id,
                requested_at=datetime.now(timezone.utc),
                reviewed_checksum=version.checksum,
            )
        )
        _record(
            session,
            context,
            operation_id,
            "subjective_assessment.human_proposed",
            version.id,
            payload,
            version.id,
            entity_type="SubjectiveAssessmentVersion",
            checksum=version.checksum,
        )
        session.flush()
        return version

    @classmethod
    def _decision(
        cls,
        session: Session,
        context: TenantContext,
        version_id: uuid.UUID,
        *,
        operation_id: uuid.UUID,
        expected_checksum: str,
        decision: HumanReviewStatus,
        reason: str | None = None,
    ) -> SubjectiveAssessmentVersion:
        version = cls._version(session, context, version_id, "subjective.review")
        event = {
            HumanReviewStatus.APPROVED: "subjective_assessment.human_approved",
            HumanReviewStatus.REJECTED: "subjective_assessment.human_rejected",
            HumanReviewStatus.CHANGES_REQUESTED: "subjective_assessment.human_changes_requested",
        }.get(decision)
        if event is None:
            raise RubricError("unsupported human Writing review decision")
        payload = {
            "expected_checksum": expected_checksum,
            "decision": decision.value,
            "reason": reason,
        }
        receipt = _receipt(session, context, operation_id, event, version.id, payload)
        if receipt:
            return version
        if (
            version.status is not SubjectiveAssessmentStatus.IN_REVIEW
            or version.checksum != expected_checksum
        ):
            raise RubricError("stale or completed human Writing review")
        rubric, revision = cls._pins(session, version)
        review = review_for(
            session,
            "SUBJECTIVE_ASSESSMENT_VERSION",
            version.id,
            review_type="HUMAN_WRITING_ASSESSMENT",
        )
        if (
            review is None
            or review.status is not HumanReviewStatus.PENDING
            or review.reviewed_checksum != version.checksum
        ):
            raise RubricError("human Writing review does not match the exact proposal")
        if review.requested_by == context.user_id:
            raise PermissionError("human Writing approval requires an independent reviewer")
        if any(
            row.confidence is not None or row.ai_run_step_id is not None
            for row in version.criterion_assessments
        ):
            raise RubricError(
                "human Writing criteria cannot contain AI confidence or step provenance"
            )
        entries, values = cls.validate_entries(
            rubric,
            str(revision.payload.get("text", "")),
            [
                {
                    "criterion_code": row.criterion_code,
                    "level": row.proposed_level,
                    "points": str(row.proposed_points),
                    "rationale": row.rationale,
                    "evidence": row.evidence,
                }
                for row in version.criterion_assessments
            ],
        )
        if cls.proposal_checksum(version, entries) != review.reviewed_checksum:
            raise RubricError("human criterion proposal changed after review was requested")
        now = datetime.now(timezone.utc)
        if decision is HumanReviewStatus.APPROVED:
            for row in version.criterion_assessments:
                row.approved_level, row.approved_points = row.proposed_level, row.proposed_points
                row.status = SubjectiveCriterionStatus.APPROVED
            version.approved_score = SubjectiveAssessmentService._aggregate(rubric, values)
            version.human_approved_values = {
                entry["criterion_code"]: {"level": entry["level"], "points": entry["points"]}
                for entry in entries
            }
        elif not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise RubricError("a bounded reason is required for rejection or changes requested")
        version.status = SubjectiveAssessmentStatus(decision.value)
        version.provenance = {
            **version.provenance,
            "reviewed_by": str(context.user_id),
            "reviewed_at": now.isoformat(),
            "decision": decision.value,
        }
        version.checksum = canonical_checksum(
            {
                "reviewed_checksum": expected_checksum,
                "decision": decision.value,
                "approved_values": version.human_approved_values,
                "approved_score": str(version.approved_score)
                if version.approved_score is not None
                else None,
                "provenance": version.provenance,
            }
        )
        review.status, review.decided_by, review.decided_at = decision, context.user_id, now
        review.decision, review.reason = decision.value, reason
        _record(
            session,
            context,
            operation_id,
            event,
            version.id,
            payload,
            version.id,
            entity_type="SubjectiveAssessmentVersion",
            checksum=version.checksum,
        )
        session.flush()
        return version

    @classmethod
    def approve(
        cls, session: Session, context: TenantContext, version_id: uuid.UUID, **kwargs: Any
    ) -> SubjectiveAssessmentVersion:
        return cls._decision(
            session, context, version_id, decision=HumanReviewStatus.APPROVED, **kwargs
        )

    @classmethod
    def decide(
        cls, session: Session, context: TenantContext, version_id: uuid.UUID, **kwargs: Any
    ) -> SubjectiveAssessmentVersion:
        return cls._decision(session, context, version_id, **kwargs)
