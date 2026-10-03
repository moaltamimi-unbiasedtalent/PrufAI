from __future__ import annotations

# Evidence payload construction keeps field names adjacent for audit review.
# ruff: noqa: E501
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationService, TenantContext
from app.models import (
    AppealInformationEntry,
    AssessmentResult,
    HumanReviewRequest,
    QuestionBankItemVersion,
    ResultAppeal,
    ResultAppealVersion,
    ResultCorrection,
    ResultCorrectionObjectiveBinding,
    ResultModerationCase,
    ResultVersion,
    ScoringRun,
    SubjectiveAssessmentVersion,
)
from app.models.enums import (
    AppealDecision,
    AppealStatus,
    CorrectionStatus,
    CorrectionType,
    HumanReviewStatus,
    ModerationStatus,
    QuestionBankLifecycleStatus,
    ResultVersionStatus,
    SubjectiveAssessmentStatus,
)
from app.scoring.service import ScoringService
from app.writing_assessment.service import SubjectiveAssessmentService


class ResultGovernanceError(ValueError):
    """A governed lifecycle or authorization violation."""


@dataclass(frozen=True)
class AppealPolicy:
    """Explicit product policy. No provider deadline is invented by this service."""

    eligible_result_statuses: frozenset[ResultVersionStatus] = frozenset(
        {ResultVersionStatus.RELEASED}
    )
    allow_withdrawal: bool = True
    allow_multiple_appeals: bool = False
    window_days: Optional[int] = None

    def __post_init__(self) -> None:
        if self.window_days is not None and self.window_days < 0:
            raise ValueError("appeal window must be non-negative")


@dataclass(frozen=True)
class ResultEvidenceBundle:
    result_version_id: uuid.UUID
    result_checksum: str
    scoring_run_id: uuid.UUID
    scoring_run_checksum: Optional[str]
    exam_version_id: uuid.UUID
    standard_pack_version_id: uuid.UUID
    payload: dict[str, Any]
    checksum: str


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ResultGovernanceService:
    """Append-only result moderation and appeal lifecycle operations."""

    _TRANSITIONS: dict[AppealStatus, frozenset[AppealStatus]] = {
        AppealStatus.SUBMITTED: frozenset({AppealStatus.UNDER_REVIEW, AppealStatus.WITHDRAWN}),
        AppealStatus.UNDER_REVIEW: frozenset(
            {
                AppealStatus.INFORMATION_REQUIRED,
                AppealStatus.ADJUDICATED_UPHELD,
                AppealStatus.ADJUDICATED_PARTIALLY_UPHELD,
                AppealStatus.ADJUDICATED_REJECTED,
                AppealStatus.WITHDRAWN,
            }
        ),
        AppealStatus.INFORMATION_REQUIRED: frozenset(
            {AppealStatus.UNDER_REVIEW, AppealStatus.WITHDRAWN}
        ),
        AppealStatus.ADJUDICATED_UPHELD: frozenset({AppealStatus.CLOSED}),
        AppealStatus.ADJUDICATED_PARTIALLY_UPHELD: frozenset({AppealStatus.CLOSED}),
        AppealStatus.ADJUDICATED_REJECTED: frozenset({AppealStatus.CLOSED}),
        AppealStatus.WITHDRAWN: frozenset({AppealStatus.CLOSED}),
        AppealStatus.CLOSED: frozenset(),
        AppealStatus.DRAFT: frozenset({AppealStatus.SUBMITTED}),
    }

    @staticmethod
    def build_evidence_bundle(
        session: Session, result_version_id: uuid.UUID
    ) -> ResultEvidenceBundle:
        version = session.get(ResultVersion, result_version_id)
        if version is None:
            raise ResultGovernanceError("result version not found")
        run = session.get(ScoringRun, version.scoring_run_id)
        if run is None:
            raise ResultGovernanceError("scoring run not found")
        snapshot = run.snapshot
        payload: dict[str, Any] = {
            "result_version": {
                "id": str(version.id),
                "result_id": str(version.result_id),
                "version_number": version.version_number,
                "status": version.status.value,
                "checksum": version.checksum,
                "response_set_checksum": version.response_set_checksum,
                "rule_provenance": version.rule_provenance,
            },
            "scoring_run": {
                "id": str(run.id),
                "checksum": run.checksum,
                "response_set_checksum": run.response_set_checksum,
                "configuration_checksum": run.configuration_checksum,
                "scoring_engine": f"{run.scoring_engine_name}@{run.scoring_engine_version}",
            },
            "exam_version_id": str(run.exam_version_id),
            "standard_pack_version_id": str(run.standard_pack_version_id),
            "snapshot": {
                "id": str(snapshot.id) if snapshot else None,
                "response_set_checksum": snapshot.response_set_checksum if snapshot else None,
                "runtime_plan_checksum": snapshot.runtime_plan_checksum if snapshot else None,
                "snapshot_json": snapshot.snapshot_json if snapshot else None,
            },
            "item_scores": [
                {
                    "id": str(item.id),
                    "binding_id": str(item.exam_version_item_binding_id),
                    "response_id": str(item.candidate_response_id)
                    if item.candidate_response_id
                    else None,
                    "response_revision_id": str(item.candidate_response_revision_id)
                    if item.candidate_response_revision_id
                    else None,
                    "status": item.status.value,
                    "awarded_points": str(item.awarded_points)
                    if item.awarded_points is not None
                    else None,
                    "maximum_points": str(item.maximum_points),
                    "response_checksum": item.response_checksum,
                    "scoring_policy": item.scoring_policy,
                }
                for item in sorted(run.item_scores, key=lambda row: str(row.id))
            ],
            "components": [
                {
                    "id": str(component.id),
                    "level": component.level,
                    "source_entity_id": component.source_entity_id,
                    "raw_awarded_points": str(component.raw_awarded_points)
                    if component.raw_awarded_points is not None
                    else None,
                    "raw_maximum_points": str(component.raw_maximum_points),
                    "transformed_value": str(component.transformed_value)
                    if component.transformed_value is not None
                    else None,
                    "status": component.status.value,
                    "provenance": component.provenance,
                }
                for component in sorted(run.components, key=lambda row: str(row.id))
            ],
        }
        return ResultEvidenceBundle(
            result_version_id=version.id,
            result_checksum=version.checksum,
            scoring_run_id=run.id,
            scoring_run_checksum=run.checksum,
            exam_version_id=run.exam_version_id,
            standard_pack_version_id=run.standard_pack_version_id,
            payload=payload,
            checksum=_checksum(payload),
        )

    @staticmethod
    def _result_for_context(
        session: Session, context: TenantContext, result_version_id: uuid.UUID
    ) -> tuple[AssessmentResult, ResultVersion]:
        version = session.get(ResultVersion, result_version_id)
        if version is None:
            raise ResultGovernanceError("result version not found")
        result = session.get(AssessmentResult, version.result_id)
        if result is None or result.organisation_id != context.organisation_id:
            raise ResultGovernanceError("result is outside the organisation scope")
        return result, version

    @classmethod
    def submit_appeal(
        cls,
        session: Session,
        context: TenantContext,
        *,
        result_version_id: uuid.UUID,
        category: str,
        grounds: str,
        operation_id: str,
        policy: AppealPolicy = AppealPolicy(),
    ) -> ResultAppeal:
        if not context.organisation_id:
            raise ResultGovernanceError("organisation context required")
        if not operation_id or len(operation_id) > 120:
            raise ResultGovernanceError("operation_id is required")
        if not category or len(category) > 80:
            raise ResultGovernanceError("appeal category is required")
        if not grounds or len(grounds.strip()) > 10000:
            raise ResultGovernanceError("appeal grounds must be between 1 and 10000 characters")
        existing_version = session.scalar(
            select(ResultAppealVersion).where(ResultAppealVersion.operation_id == operation_id)
        )
        if existing_version is not None:
            existing = session.get(ResultAppeal, existing_version.appeal_id)
            if existing is not None and (
                existing.candidate_user_id != context.user_id
                or existing_version.checksum
                != _checksum({"category": category, "grounds": grounds.strip()})
            ):
                raise ResultGovernanceError("operation_id already used with a different payload")
            if existing is not None:
                return existing
        result, version = cls._result_for_context(session, context, result_version_id)
        if result.candidate_user_id != context.user_id:
            raise ResultGovernanceError("candidate may appeal only their own result")
        if version.status not in policy.eligible_result_statuses:
            raise ResultGovernanceError("result is not eligible for appeal")
        if not policy.allow_multiple_appeals and session.scalar(
            select(ResultAppeal).where(
                ResultAppeal.result_id == result.id,
                ResultAppeal.status.not_in([AppealStatus.WITHDRAWN, AppealStatus.CLOSED]),
            )
        ):
            raise ResultGovernanceError("an active appeal already exists")
        bundle = cls.build_evidence_bundle(session, version.id)
        now = _now()
        fingerprint = _checksum({"category": category, "grounds": grounds.strip()})
        appeal = ResultAppeal(
            organisation_id=result.organisation_id,
            candidate_user_id=result.candidate_user_id,
            attempt_id=result.attempt_id,
            result_id=result.id,
            challenged_result_version_id=version.id,
            scoring_run_id=version.scoring_run_id,
            exam_version_id=result.exam_version_id,
            standard_pack_version_id=bundle.standard_pack_version_id,
            challenged_result_checksum=version.checksum,
            status=AppealStatus.SUBMITTED,
            category=category,
            operation_id=operation_id,
            current_version_number=1,
            current_checksum=fingerprint,
            created_at=now,
            updated_at=now,
        )
        session.add(appeal)
        session.flush()
        appeal_version = ResultAppealVersion(
            appeal_id=appeal.id,
            version_number=1,
            status=AppealStatus.SUBMITTED,
            category=category,
            grounds=grounds.strip(),
            challenged_result_version_id=version.id,
            challenged_result_checksum=version.checksum,
            evidence_bundle_checksum=bundle.checksum,
            operation_id=operation_id,
            actor_user_id=context.user_id,
            checksum=fingerprint,
            created_at=now,
        )
        session.add(appeal_version)
        AuditService.record(
            session,
            context,
            event_type="appeal.created",
            entity_type="result_appeal",
            organisation_id=result.organisation_id,
            entity_id=str(appeal.id),
            entity_version="1",
            metadata={
                "result_version_id": str(version.id),
                "result_checksum": version.checksum,
                "category": category,
            },
            require_audit_permission=False,
        )
        AuditService.record(
            session,
            context,
            event_type="appeal.submitted",
            entity_type="result_appeal",
            organisation_id=result.organisation_id,
            entity_id=str(appeal.id),
            entity_version="1",
            metadata={"result_version_id": str(version.id)},
            require_audit_permission=False,
        )
        session.flush()
        return appeal

    @classmethod
    def transition(
        cls, session: Session, context: TenantContext, appeal_id: uuid.UUID, target: AppealStatus
    ) -> ResultAppeal:
        appeal = session.scalar(
            select(ResultAppeal).where(ResultAppeal.id == appeal_id).with_for_update()
        )
        if appeal is None or appeal.organisation_id != context.organisation_id:
            raise ResultGovernanceError("appeal not found")
        if target not in cls._TRANSITIONS.get(appeal.status, frozenset()):
            raise ResultGovernanceError(
                f"invalid appeal transition {appeal.status.value} -> {target.value}"
            )
        if target in {
            AppealStatus.UNDER_REVIEW,
            AppealStatus.INFORMATION_REQUIRED,
            AppealStatus.CLOSED,
        }:
            AuthorizationService.require_permission(
                context, "appeal.adjudicate", appeal.organisation_id
            )
        if target is AppealStatus.WITHDRAWN and context.user_id != appeal.candidate_user_id:
            AuthorizationService.require_permission(
                context, "appeal.adjudicate", appeal.organisation_id
            )
        appeal.status = target
        appeal.updated_at = _now()
        event_type = {
            AppealStatus.UNDER_REVIEW: "appeal.review_started",
            AppealStatus.INFORMATION_REQUIRED: "appeal.information_requested",
            AppealStatus.WITHDRAWN: "appeal.withdrawn",
            AppealStatus.CLOSED: "appeal.closed",
        }.get(target, f"appeal.{target.value.lower()}")
        AuditService.record(
            session,
            context,
            event_type=event_type,
            entity_type="result_appeal",
            organisation_id=appeal.organisation_id,
            entity_id=str(appeal.id),
            metadata={"status": target.value},
            require_audit_permission=False,
        )
        return appeal

    @classmethod
    def initiate_correction(
        cls,
        session: Session,
        context: TenantContext,
        *,
        appeal_id: uuid.UUID,
        appeal_version_number: int,
        appeal_checksum: str,
        evidence_bundle_checksum: str,
        correction_type: CorrectionType,
        operation_id: str,
        corrections: dict[str, dict[str, Any]] | None = None,
        transcription_factory: Any = None,
        objective_bindings: list[dict[str, str]] | None = None,
    ) -> ResultCorrection:
        """Execute a governed component correction and rescore, without score editing."""
        AuthorizationService.require_permission(context, "appeal.correct", context.organisation_id)
        appeal = session.scalar(
            select(ResultAppeal).where(ResultAppeal.id == appeal_id).with_for_update()
        )
        if appeal is None or appeal.organisation_id != context.organisation_id:
            raise ResultGovernanceError("appeal not found")
        existing = session.scalar(
            select(ResultCorrection).where(ResultCorrection.operation_id == operation_id)
        )
        if existing is not None:
            if (
                existing.appeal_id != appeal.id
                or existing.correction_type != correction_type
                or existing.source_result_checksum != appeal.challenged_result_checksum
            ):
                raise ResultGovernanceError("operation_id already used with a different correction")
            return existing
        current = session.scalar(
            select(ResultAppealVersion).where(
                ResultAppealVersion.appeal_id == appeal.id,
                ResultAppealVersion.version_number == appeal_version_number,
            )
        )
        bundle = cls.build_evidence_bundle(session, appeal.challenged_result_version_id)
        if (
            current is None
            or current.checksum != appeal_checksum
            or appeal.current_checksum != appeal_checksum
            or current.evidence_bundle_checksum != evidence_bundle_checksum
            or bundle.checksum != evidence_bundle_checksum
        ):
            raise ResultGovernanceError("stale correction source")
        if current.decision not in {
            AppealDecision.UPHOLD,
            AppealDecision.PARTIALLY_UPHOLD,
        }:
            raise ResultGovernanceError("only an upheld appeal may initiate correction")
        now = _now()
        correction = ResultCorrection(
            organisation_id=appeal.organisation_id,
            appeal_id=appeal.id,
            appeal_version_id=current.id,
            source_result_version_id=appeal.challenged_result_version_id,
            source_result_checksum=appeal.challenged_result_checksum,
            correction_type=correction_type,
            status=CorrectionStatus.IN_PROGRESS,
            operation_id=operation_id,
            created_by_user_id=context.user_id,
            created_at=now,
            updated_at=now,
            metadata_json={"evidence_bundle_checksum": evidence_bundle_checksum},
        )
        session.add(correction)
        session.flush()
        cls._audit_correction(session, context, "result.correction_initiated", correction)
        try:
            if correction_type is CorrectionType.TRANSCRIPTION_REASSESSMENT:
                if transcription_factory is None:
                    raise ResultGovernanceError("a governed transcription factory is required")
                new_transcript = transcription_factory(session, context, appeal)
                correction.new_transcription_version_id = new_transcript.id
                cls._audit_correction(
                    session, context, "result.correction_component_created", correction
                )
            elif correction_type in {
                CorrectionType.WRITING_REASSESSMENT,
                CorrectionType.SPEAKING_REASSESSMENT,
            }:
                source = cls._subjective_source(session, appeal, correction_type)
                if source is None:
                    raise ResultGovernanceError("approved subjective source is unavailable")
                review = HumanReviewRequest(
                    review_type="RESULT_CORRECTION",
                    entity_type="SUBJECTIVE_ASSESSMENT_VERSION",
                    entity_id=source.id,
                    status=HumanReviewStatus.PENDING,
                    requested_by=context.user_id,
                    requested_at=now,
                    reviewed_checksum=source.checksum,
                )
                session.add(review)
                session.flush()
                successor = SubjectiveAssessmentService.correct(
                    session, context, source, corrections or {}, review
                )
                correction.new_component_version_id = successor.id
                cls._audit_correction(
                    session, context, "result.correction_component_created", correction
                )
            elif correction_type is CorrectionType.OBJECTIVE_RESCORING:
                bindings = objective_bindings or []
                if not bindings:
                    raise ResultGovernanceError(
                        "objective correction requires governed replacements"
                    )
                objective_map: dict[str, str] = {}
                for binding in bindings:
                    original = session.get(
                        QuestionBankItemVersion, uuid.UUID(binding["original_item_version_id"])
                    )
                    replacement = session.get(
                        QuestionBankItemVersion, uuid.UUID(binding["replacement_item_version_id"])
                    )
                    if original is None or replacement is None:
                        raise ResultGovernanceError("objective replacement version not found")
                    if original.item_id != replacement.item_id:
                        raise ResultGovernanceError("objective replacement must retain stable item")
                    if original.response_mode != replacement.response_mode:
                        raise ResultGovernanceError("objective replacement response mode mismatch")
                    if replacement.status not in {
                        QuestionBankLifecycleStatus.APPROVED,
                        QuestionBankLifecycleStatus.PUBLISHED,
                        QuestionBankLifecycleStatus.ACTIVE,
                    }:
                        raise ResultGovernanceError("objective replacement is not eligible")
                    session.add(
                        ResultCorrectionObjectiveBinding(
                            result_correction_id=correction.id,
                            original_item_version_id=original.id,
                            replacement_item_version_id=replacement.id,
                            original_item_checksum=original.checksum,
                            replacement_item_checksum=replacement.checksum,
                            created_at=now,
                        )
                    )
                    objective_map[str(original.id)] = str(replacement.id)
                correction.metadata_json = {
                    **correction.metadata_json,
                    "objective_bindings": objective_map,
                }
            else:
                raise ResultGovernanceError("unsupported correction type")
            correction.status = CorrectionStatus.READY_FOR_RESCORING
            session.flush()
            run = ScoringService.score(
                session,
                context,
                appeal.attempt_id,
                objective_item_overrides=(
                    correction.metadata_json.get("objective_bindings", {})
                    if correction_type is CorrectionType.OBJECTIVE_RESCORING
                    else None
                ),
            )
            correction.new_scoring_run_id = run.id
            result = session.get(AssessmentResult, appeal.result_id)
            if result is None:
                raise ResultGovernanceError("assessment result not found")
            new_version = max(result.versions, key=lambda item: item.version_number)
            correction.new_result_version_id = new_version.id
            correction.status = CorrectionStatus.RESCORED
            correction.completed_at = _now()
            correction.updated_at = _now()
            cls._audit_correction(session, context, "result.correction_rescored", correction)
            cls._audit_correction(session, context, "result.correction_completed", correction)
            return correction
        except Exception as exc:  # noqa: BLE001
            correction.status = CorrectionStatus.FAILED
            correction.failure_category = type(exc).__name__[:120]
            correction.updated_at = _now()
            cls._audit_correction(session, context, "result.correction_failed", correction)
            if isinstance(exc, ResultGovernanceError):
                raise
            raise ResultGovernanceError("governed correction failed") from exc

    @staticmethod
    def _subjective_source(
        session: Session, appeal: ResultAppeal, correction_type: CorrectionType
    ) -> SubjectiveAssessmentVersion | None:
        rows = list(
            session.scalars(
                select(SubjectiveAssessmentVersion).where(
                    SubjectiveAssessmentVersion.attempt_id == appeal.attempt_id,
                    SubjectiveAssessmentVersion.status == SubjectiveAssessmentStatus.APPROVED,
                )
            )
        )
        speaking = correction_type is CorrectionType.SPEAKING_REASSESSMENT
        return next(
            (row for row in rows if bool(row.candidate_media_asset_id) is speaking),
            None,
        )

    @staticmethod
    def _audit_correction(
        session: Session, context: TenantContext, event_type: str, correction: ResultCorrection
    ) -> None:
        AuditService.record(
            session,
            context,
            event_type=event_type,
            entity_type="result_correction",
            organisation_id=correction.organisation_id,
            entity_id=str(correction.id),
            metadata={
                "correction_type": correction.correction_type.value,
                "status": correction.status.value,
                "source_result_version_id": str(correction.source_result_version_id),
                "new_component_version_id": str(correction.new_component_version_id)
                if correction.new_component_version_id
                else None,
                "new_scoring_run_id": str(correction.new_scoring_run_id)
                if correction.new_scoring_run_id
                else None,
                "new_result_version_id": str(correction.new_result_version_id)
                if correction.new_result_version_id
                else None,
            },
            require_audit_permission=False,
        )

    @classmethod
    def adjudicate(
        cls,
        session: Session,
        context: TenantContext,
        *,
        appeal_id: uuid.UUID,
        appeal_version_number: int,
        appeal_checksum: str,
        evidence_bundle_checksum: str,
        decision: AppealDecision,
        operation_id: str,
        reason: str,
    ) -> ResultAppeal:
        appeal = session.scalar(
            select(ResultAppeal).where(ResultAppeal.id == appeal_id).with_for_update()
        )
        if appeal is None or appeal.organisation_id != context.organisation_id:
            raise ResultGovernanceError("appeal not found")
        AuthorizationService.require_permission(
            context, "appeal.adjudicate", appeal.organisation_id
        )
        if not operation_id:
            raise ResultGovernanceError("operation_id is required")
        repeated = session.scalar(
            select(ResultAppealVersion).where(ResultAppealVersion.operation_id == operation_id)
        )
        if repeated is not None:
            if (
                repeated.appeal_id != appeal.id
                or repeated.decision is not decision
                or repeated.decision_reason != reason[:10000]
            ):
                raise ResultGovernanceError("operation_id already used with a different decision")
            return appeal
        current = session.scalar(
            select(ResultAppealVersion).where(
                ResultAppealVersion.appeal_id == appeal.id,
                ResultAppealVersion.version_number == appeal_version_number,
            )
        )
        if (
            current is None
            or current.checksum != appeal_checksum
            or appeal.current_checksum != appeal_checksum
        ):
            raise ResultGovernanceError("stale appeal version")
        bundle = cls.build_evidence_bundle(session, appeal.challenged_result_version_id)
        if (
            bundle.checksum != evidence_bundle_checksum
            or current.evidence_bundle_checksum != evidence_bundle_checksum
        ):
            raise ResultGovernanceError("stale evidence bundle")
        if appeal.status is not AppealStatus.UNDER_REVIEW:
            raise ResultGovernanceError("appeal is not under review")
        next_status = {
            AppealDecision.UPHOLD: AppealStatus.ADJUDICATED_UPHELD,
            AppealDecision.PARTIALLY_UPHOLD: AppealStatus.ADJUDICATED_PARTIALLY_UPHELD,
            AppealDecision.REJECT: AppealStatus.ADJUDICATED_REJECTED,
            AppealDecision.REQUEST_INFORMATION: AppealStatus.INFORMATION_REQUIRED,
        }[decision]
        now = _now()
        decision_checksum = _checksum(
            {"appeal": appeal_checksum, "decision": decision.value, "reason": reason}
        )
        session.add(
            ResultAppealVersion(
                appeal_id=appeal.id,
                version_number=appeal.current_version_number + 1,
                status=next_status,
                category=current.category,
                grounds=current.grounds,
                challenged_result_version_id=appeal.challenged_result_version_id,
                challenged_result_checksum=appeal.challenged_result_checksum,
                evidence_bundle_checksum=evidence_bundle_checksum,
                operation_id=operation_id,
                decision=decision,
                decision_reason=reason[:10000],
                actor_user_id=context.user_id,
                checksum=decision_checksum,
                created_at=now,
            )
        )
        appeal.current_version_number += 1
        appeal.current_checksum = decision_checksum
        appeal.status = next_status
        appeal.updated_at = now
        AuditService.record(
            session,
            context,
            event_type="appeal.adjudicated",
            entity_type="result_appeal",
            organisation_id=appeal.organisation_id,
            entity_id=str(appeal.id),
            entity_version=str(appeal.current_version_number),
            metadata={
                "decision": decision.value,
                "result_version_id": str(appeal.challenged_result_version_id),
            },
            require_audit_permission=False,
        )
        return appeal

    @classmethod
    def add_information(
        cls, session: Session, context: TenantContext, *, appeal_id: uuid.UUID, message: str
    ) -> AppealInformationEntry:
        if not message or len(message.strip()) > 10000:
            raise ResultGovernanceError(
                "information message must be between 1 and 10000 characters"
            )
        appeal = session.scalar(
            select(ResultAppeal).where(ResultAppeal.id == appeal_id).with_for_update()
        )
        if appeal is None or appeal.organisation_id != context.organisation_id:
            raise ResultGovernanceError("appeal not found")
        if context.user_id != appeal.candidate_user_id:
            AuthorizationService.require_permission(
                context, "appeal.adjudicate", appeal.organisation_id
            )
            if appeal.status is not AppealStatus.UNDER_REVIEW:
                raise ResultGovernanceError(
                    "reviewer information request requires an active review"
                )
        elif appeal.status is not AppealStatus.INFORMATION_REQUIRED:
            raise ResultGovernanceError("candidate information is not currently requested")
        entry = AppealInformationEntry(
            appeal_id=appeal.id,
            appeal_version_id=appeal.versions[-1].id,
            direction="CANDIDATE" if context.user_id == appeal.candidate_user_id else "REVIEWER",
            message=message.strip(),
            created_by_user_id=context.user_id,
            created_at=_now(),
        )
        session.add(entry)
        appeal.status = (
            AppealStatus.UNDER_REVIEW
            if context.user_id == appeal.candidate_user_id
            else AppealStatus.INFORMATION_REQUIRED
        )
        appeal.updated_at = _now()
        AuditService.record(
            session,
            context,
            event_type=(
                "appeal.information_provided"
                if context.user_id == appeal.candidate_user_id
                else "appeal.information_requested"
            ),
            entity_type="result_appeal",
            organisation_id=appeal.organisation_id,
            entity_id=str(appeal.id),
            metadata={"appeal_version_id": str(entry.appeal_version_id)},
            require_audit_permission=False,
        )
        return entry

    @classmethod
    def create_moderation_case(
        cls,
        session: Session,
        context: TenantContext,
        *,
        result_version_id: uuid.UUID,
        reason: str,
        operation_id: str,
        priority: str = "NORMAL",
    ) -> ResultModerationCase:
        AuthorizationService.require_permission(context, "result.moderate", context.organisation_id)
        result, version = cls._result_for_context(session, context, result_version_id)
        existing = session.scalar(
            select(ResultModerationCase).where(ResultModerationCase.operation_id == operation_id)
        )
        if existing:
            if existing.result_version_id != version.id or existing.reason != reason[:120]:
                raise ResultGovernanceError(
                    "operation_id already used with a different moderation case"
                )
            return existing
        case = ResultModerationCase(
            organisation_id=result.organisation_id,
            attempt_id=result.attempt_id,
            result_id=result.id,
            result_version_id=version.id,
            scoring_run_id=version.scoring_run_id,
            result_checksum=version.checksum,
            status=ModerationStatus.OPEN,
            reason=reason[:120],
            priority=priority[:30],
            operation_id=operation_id,
            created_at=_now(),
        )
        session.add(case)
        AuditService.record(
            session,
            context,
            event_type="result_moderation.created",
            entity_type="result_moderation_case",
            organisation_id=result.organisation_id,
            entity_id=str(case.id),
            metadata={
                "result_version_id": str(version.id),
                "result_checksum": version.checksum,
                "reason": reason[:120],
            },
            require_audit_permission=False,
        )
        session.flush()
        return case

    @classmethod
    def decide_moderation(
        cls,
        session: Session,
        context: TenantContext,
        *,
        case_id: uuid.UUID,
        result_checksum: str,
        outcome: ModerationStatus,
        operation_id: str,
    ) -> ResultModerationCase:
        AuthorizationService.require_permission(context, "result.moderate", context.organisation_id)
        case = session.get(ResultModerationCase, case_id)
        if case is None or case.organisation_id != context.organisation_id:
            raise ResultGovernanceError("moderation case not found")
        if case.metadata_json.get("decision_operation_id") == operation_id:
            if case.outcome != outcome.value:
                raise ResultGovernanceError(
                    "operation_id already used with a different moderation decision"
                )
            return case
        if case.result_checksum != result_checksum:
            raise ResultGovernanceError("stale moderation case")
        if case.status not in {ModerationStatus.OPEN, ModerationStatus.IN_REVIEW}:
            raise ResultGovernanceError("moderation case is already closed")
        if outcome not in {
            ModerationStatus.NO_CHANGE,
            ModerationStatus.CORRECTION_REQUIRED,
            ModerationStatus.ESCALATED,
            ModerationStatus.CLOSED,
        }:
            raise ResultGovernanceError("invalid moderation outcome")
        case.status = outcome
        case.outcome = outcome.value
        case.metadata_json = {**case.metadata_json, "decision_operation_id": operation_id}
        case.closed_at = (
            _now()
            if outcome
            in {
                ModerationStatus.NO_CHANGE,
                ModerationStatus.CORRECTION_REQUIRED,
                ModerationStatus.CLOSED,
            }
            else None
        )
        case.closed_by_user_id = context.user_id
        event = {
            ModerationStatus.NO_CHANGE: "result_moderation.no_change",
            ModerationStatus.CORRECTION_REQUIRED: "result_moderation.correction_required",
            ModerationStatus.ESCALATED: "result_moderation.review_started",
            ModerationStatus.CLOSED: "result_moderation.closed",
        }[outcome]
        AuditService.record(
            session,
            context,
            event_type=event,
            entity_type="result_moderation_case",
            organisation_id=case.organisation_id,
            entity_id=str(case.id),
            metadata={
                "result_version_id": str(case.result_version_id),
                "result_checksum": case.result_checksum,
                "outcome": outcome.value,
            },
            require_audit_permission=False,
        )
        return case
