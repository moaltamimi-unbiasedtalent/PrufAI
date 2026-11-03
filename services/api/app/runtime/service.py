from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.exams.service import exam_checksum
from app.models import (
    CandidateAudioRecordingSession,
    CandidateExamAssignment,
    CandidateExamAttempt,
    CandidateExamSession,
    CandidateMediaAsset,
    CandidateResponse,
    CandidateResponseRevision,
    ExamVersion,
    QuestionBankItemVersion,
    StandardPackModule,
    StandardPackSection,
    StandardPackTaskSpecification,
)
from app.models.enums import (
    CandidateAssignmentStatus,
    CandidateAttemptStatus,
    ExamVersionStatus,
    MembershipStatus,
    PackResponseMode,
    QuestionBankLifecycleStatus,
    QuestionBankRightsStatus,
    RoleScope,
    UserStatus,
)
from app.question_bank.service import delivery_projection
from app.speaking_audio.service import SpeakingAudioService

SUPPORTED_RESPONSE_MODES = {
    PackResponseMode.SELECT_ONE.value,
    PackResponseMode.SELECT_MULTIPLE.value,
    PackResponseMode.TEXT.value,
    PackResponseMode.LONG_TEXT.value,
    PackResponseMode.AUDIO.value,
}
ACTIVE_ATTEMPT_STATUSES = {CandidateAttemptStatus.READY, CandidateAttemptStatus.IN_PROGRESS}
PUBLISHABLE_RIGHTS = {
    QuestionBankRightsStatus.PRUFAI_ORIGINAL,
    QuestionBankRightsStatus.LICENSED,
    QuestionBankRightsStatus.PUBLIC_DOMAIN,
}


class RuntimeConflict(ValueError):
    pass


class RuntimeExpired(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _payload_checksum(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class CandidateRuntimeService:
    @staticmethod
    def assignment_options(session: Session, context: TenantContext) -> dict[str, Any]:
        """Resolve bounded, tenant-scoped choices for the existing assignment action."""
        from app.models import MembershipRole, OrganisationMembership, Role, User

        if context.organisation_id is None:
            raise AuthorizationError("organisation context required")
        for permission in ("exam_assignment.manage", "exam.read", "membership.read"):
            AuthorizationService.require_permission(context, permission, context.organisation_id)
        candidates = session.scalars(
            select(User)
            .join(OrganisationMembership, OrganisationMembership.user_id == User.id)
            .join(MembershipRole, MembershipRole.membership_id == OrganisationMembership.id)
            .join(Role, Role.id == MembershipRole.role_id)
            .where(
                OrganisationMembership.organisation_id == context.organisation_id,
                OrganisationMembership.status == MembershipStatus.ACTIVE,
                User.status == UserStatus.ACTIVE,
                Role.scope == RoleScope.TENANT,
                Role.key == "EXAMINER",
            )
            .order_by(User.display_name, User.id)
            .limit(100)
        )
        from app.models import Exam

        versions = session.scalars(
            select(ExamVersion)
            .join(Exam)
            .where(
                ExamVersion.status.in_([ExamVersionStatus.PUBLISHED, ExamVersionStatus.ACTIVE]),
                (Exam.scope_type == "PLATFORM_SHARED")
                | (
                    (Exam.scope_type == "ORGANISATION_PRIVATE")
                    & (Exam.organisation_id == context.organisation_id)
                ),
            )
            .order_by(Exam.name, ExamVersion.created_at.desc(), ExamVersion.id)
            .limit(100)
        )
        eligible = []
        for version in versions:
            try:
                CandidateRuntimeService._safe_plan(session, version)
            except ValueError:
                continue
            eligible.append(
                {
                    "id": str(version.id),
                    "exam_id": str(version.exam_id),
                    "exam_name": version.exam.name,
                    "version_label": version.version_label,
                    "status": version.status.value,
                    "classification": version.classification.value,
                    "standard_pack_version_id": str(version.standard_pack_version_id),
                    "checksum": version.checksum,
                }
            )
        return {
            "candidates": [
                {"id": str(user.id), "display_name": user.display_name} for user in candidates
            ],
            "exam_versions": eligible,
        }

    @staticmethod
    def _require_candidate(context: TenantContext, candidate_user_id: uuid.UUID) -> None:
        if context.user_id != candidate_user_id:
            raise RuntimeError("candidate ownership required")

    @staticmethod
    def _audit(
        session: Session,
        context: TenantContext,
        event_type: str,
        entity_type: str,
        entity_id: uuid.UUID,
        organisation_id: uuid.UUID,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        if AuthorizationService.has_permission(context, "audit.read", organisation_id):
            AuditService.record(
                session,
                context,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=str(entity_id),
                organisation_id=organisation_id,
                metadata=metadata or {},
            )

    @staticmethod
    def _assignment(session: Session, assignment_id: uuid.UUID) -> CandidateExamAssignment:
        assignment = session.get(CandidateExamAssignment, assignment_id)
        if assignment is None:
            raise ValueError("candidate assignment not found")
        return assignment

    @staticmethod
    def _attempt(session: Session, attempt_id: uuid.UUID) -> CandidateExamAttempt:
        attempt = session.get(CandidateExamAttempt, attempt_id)
        if attempt is None:
            raise ValueError("candidate attempt not found")
        return attempt

    @staticmethod
    def _owned_attempt(
        session: Session, context: TenantContext, attempt_id: uuid.UUID
    ) -> CandidateExamAttempt:
        attempt = CandidateRuntimeService._attempt(session, attempt_id)
        CandidateRuntimeService._require_candidate(context, attempt.candidate_user_id)
        if context.organisation_id != attempt.organisation_id:
            raise RuntimeError("organisation scope required")
        return attempt

    @staticmethod
    def _available(assignment: CandidateExamAssignment, point: datetime) -> bool:
        if assignment.status in {
            CandidateAssignmentStatus.CANCELLED,
            CandidateAssignmentStatus.COMPLETED,
            CandidateAssignmentStatus.EXPIRED,
        }:
            return False
        return (assignment.available_from is None or assignment.available_from <= point) and (
            assignment.available_until is None or assignment.available_until > point
        )

    @staticmethod
    def _safe_plan(session: Session, version: ExamVersion) -> dict[str, Any]:
        if version.status not in {ExamVersionStatus.PUBLISHED, ExamVersionStatus.ACTIVE}:
            raise ValueError("ExamVersion is not available for candidate delivery")
        if version.classification.value == "OFFICIAL_CERTIFICATION":
            raise ValueError("official certification delivery is not authorized")
        modules: list[dict[str, Any]] = []
        duration = 0
        for module in version.modules:
            pack_module = session.get(StandardPackModule, module.standard_pack_module_id)
            if pack_module is None:
                raise ValueError("runtime module is not part of the pinned Standard Pack")
            module_duration = pack_module.time_limit_seconds or 0
            sections: list[dict[str, Any]] = []
            for section in module.sections:
                pack_section = session.get(StandardPackSection, section.standard_pack_section_id)
                if pack_section is None:
                    raise ValueError("runtime section is not part of the pinned Standard Pack")
                if pack_module.time_limit_seconds is None and pack_section.time_limit_seconds:
                    module_duration += pack_section.time_limit_seconds
                tasks: list[dict[str, Any]] = []
                for task in section.tasks:
                    spec = session.get(
                        StandardPackTaskSpecification, task.standard_pack_task_spec_id
                    )
                    if spec is None:
                        raise ValueError("runtime task is not part of the pinned Standard Pack")
                    if spec.response_mode.value not in SUPPORTED_RESPONSE_MODES:
                        raise ValueError(
                            f"RUNTIME.UNSUPPORTED_RESPONSE_MODE:{spec.response_mode.value}"
                        )
                    items: list[dict[str, Any]] = []
                    for binding in task.item_bindings:
                        item = session.get(
                            QuestionBankItemVersion, binding.question_bank_item_version_id
                        )
                        if item is None:
                            raise ValueError("runtime item is missing")
                        if item.status is not QuestionBankLifecycleStatus.ACTIVE:
                            raise ValueError("runtime item is not ACTIVE")
                        if item.rights_status not in PUBLISHABLE_RIGHTS or (
                            item.rights_expires_at and item.rights_expires_at <= _now()
                        ):
                            raise ValueError("runtime item rights are not publishable")
                        safe = delivery_projection(item)
                        safe["binding_id"] = str(binding.id)
                        safe["response_mode"] = spec.response_mode.value
                        items.append(safe)
                    tasks.append(
                        {
                            "task_id": str(task.id),
                            "response_mode": spec.response_mode.value,
                            "navigation_mode": pack_section.navigation_mode.value,
                            "items": items,
                        }
                    )
                sections.append(
                    {
                        "section_id": str(section.id),
                        "navigation_mode": pack_section.navigation_mode.value,
                        "tasks": tasks,
                    }
                )
            modules.append(
                {
                    "module_id": str(module.id),
                    "navigation_mode": pack_module.navigation_mode.value,
                    "sections": sections,
                }
            )
            duration += module_duration
        return {
            "schema_version": "1",
            "exam_version_id": str(version.id),
            "standard_pack_version_id": str(version.standard_pack_version_id),
            "classification": version.classification.value,
            "duration_seconds": duration or None,
            "modules": modules,
        }

    @staticmethod
    def create_assignment(
        session: Session,
        context: TenantContext,
        *,
        candidate_user_id: uuid.UUID,
        exam_version_id: uuid.UUID,
        available_from: Optional[datetime] = None,
        available_until: Optional[datetime] = None,
        maximum_attempts: int = 1,
    ) -> CandidateExamAssignment:
        if context.organisation_id is None:
            raise RuntimeError("organisation context required")
        AuthorizationService.require_permission(
            context, "exam_assignment.manage", context.organisation_id
        )
        if maximum_attempts < 1:
            raise ValueError("maximum_attempts must be positive")
        version = session.get(ExamVersion, exam_version_id)
        if (
            version is None
            or (
                version.exam.scope_type == "ORGANISATION_PRIVATE"
                and version.exam.organisation_id != context.organisation_id
            )
            or version.exam.scope_type not in {"PLATFORM_SHARED", "ORGANISATION_PRIVATE"}
        ):
            raise ValueError("ExamVersion is outside the organisation scope")
        CandidateRuntimeService._safe_plan(session, version)
        if version.status not in {ExamVersionStatus.PUBLISHED, ExamVersionStatus.ACTIVE}:
            raise ValueError("ExamVersion is not assignable")
        from app.models import OrganisationMembership, User

        membership = session.scalar(
            select(OrganisationMembership).where(
                OrganisationMembership.organisation_id == context.organisation_id,
                OrganisationMembership.user_id == candidate_user_id,
            )
        )
        candidate = session.get(User, candidate_user_id)
        if membership is None or candidate is None or candidate.status.value != "ACTIVE":
            raise RuntimeError("candidate is not a member of this organisation")
        assignment = CandidateExamAssignment(
            organisation_id=context.organisation_id,
            candidate_user_id=candidate_user_id,
            exam_version_id=exam_version_id,
            status=CandidateAssignmentStatus.ASSIGNED,
            available_from=available_from,
            available_until=available_until,
            maximum_attempts=maximum_attempts,
            created_at=_now(),
            created_by_user_id=context.user_id,
        )
        session.add(assignment)
        session.flush()
        CandidateRuntimeService._audit(
            session,
            context,
            "exam_assignment.created",
            "CANDIDATE_ASSIGNMENT",
            assignment.id,
            assignment.organisation_id,
            {"exam_version_id": str(exam_version_id), "candidate_user_id": str(candidate_user_id)},
        )
        return assignment

    @staticmethod
    def list_assignments(session: Session, context: TenantContext) -> list[CandidateExamAssignment]:
        return list(
            session.scalars(
                select(CandidateExamAssignment)
                .where(
                    CandidateExamAssignment.candidate_user_id == context.user_id,
                    CandidateExamAssignment.organisation_id == context.organisation_id,
                )
                .order_by(CandidateExamAssignment.created_at.desc())
            )
        )

    @staticmethod
    def cancel_assignment(
        session: Session, context: TenantContext, assignment_id: uuid.UUID
    ) -> CandidateExamAssignment:
        assignment = CandidateRuntimeService._assignment(session, assignment_id)
        AuthorizationService.require_permission(
            context, "exam_assignment.manage", assignment.organisation_id
        )
        if context.organisation_id != assignment.organisation_id:
            raise RuntimeError("organisation scope required")
        if assignment.status is CandidateAssignmentStatus.COMPLETED:
            raise ValueError("completed assignment cannot be cancelled")
        assignment.status = CandidateAssignmentStatus.CANCELLED
        assignment.cancelled_at = _now()
        assignment.cancelled_by_user_id = context.user_id
        session.flush()
        CandidateRuntimeService._audit(
            session,
            context,
            "exam_assignment.cancelled",
            "CANDIDATE_ASSIGNMENT",
            assignment.id,
            assignment.organisation_id,
        )
        return assignment

    @staticmethod
    def expire_overdue(session: Session, context: TenantContext, limit: int = 100) -> int:
        rows = list(
            session.scalars(
                select(CandidateExamAttempt)
                .where(
                    CandidateExamAttempt.status == CandidateAttemptStatus.IN_PROGRESS,
                    CandidateExamAttempt.deadline_at <= _now(),
                )
                .limit(limit)
            )
        )
        for attempt in rows:
            CandidateRuntimeService.expire_if_due(session, context, attempt)
        session.flush()
        return len(rows)

    @staticmethod
    def create_attempt(
        session: Session, context: TenantContext, assignment_id: uuid.UUID
    ) -> CandidateExamAttempt:
        assignment = CandidateRuntimeService._assignment(session, assignment_id)
        CandidateRuntimeService._require_candidate(context, assignment.candidate_user_id)
        if context.organisation_id != assignment.organisation_id:
            raise RuntimeError("organisation scope required")
        point = _now()
        if not CandidateRuntimeService._available(assignment, point):
            raise ValueError("assignment is not currently available")
        locked = session.scalar(
            select(CandidateExamAssignment)
            .where(CandidateExamAssignment.id == assignment.id)
            .with_for_update()
        )
        if locked is None:
            raise ValueError("candidate assignment not found")
        attempts = list(
            session.scalars(
                select(CandidateExamAttempt).where(
                    CandidateExamAttempt.assignment_id == assignment.id
                )
            )
        )
        if len(attempts) >= assignment.maximum_attempts:
            raise ValueError("maximum attempt limit reached")
        if any(row.status in ACTIVE_ATTEMPT_STATUSES for row in attempts):
            raise RuntimeError("one active attempt per assignment is required")
        version = session.get(ExamVersion, assignment.exam_version_id)
        if version is None:
            raise ValueError("pinned ExamVersion not found")
        plan = CandidateRuntimeService._safe_plan(session, version)
        attempt = CandidateExamAttempt(
            assignment_id=assignment.id,
            candidate_user_id=assignment.candidate_user_id,
            organisation_id=assignment.organisation_id,
            exam_version_id=version.id,
            attempt_number=len(attempts) + 1,
            status=CandidateAttemptStatus.READY,
            delivery_manifest_checksum=exam_checksum(plan),
            runtime_plan_checksum=exam_checksum(plan),
            runtime_plan=plan,
            created_at=point,
        )
        session.add(attempt)
        session.flush()
        CandidateRuntimeService._audit(
            session,
            context,
            "exam_attempt.created",
            "CANDIDATE_ATTEMPT",
            attempt.id,
            attempt.organisation_id,
        )
        return attempt

    @staticmethod
    def start(
        session: Session, context: TenantContext, attempt_id: uuid.UUID
    ) -> CandidateExamAttempt:
        attempt = CandidateRuntimeService._owned_attempt(session, context, attempt_id)
        CandidateRuntimeService.expire_if_due(session, context, attempt)
        if attempt.status is CandidateAttemptStatus.IN_PROGRESS:
            return attempt
        if attempt.status is not CandidateAttemptStatus.READY:
            raise ValueError("attempt cannot be started")
        version = session.get(ExamVersion, attempt.exam_version_id)
        if (
            version is None
            or exam_checksum(CandidateRuntimeService._safe_plan(session, version))
            != attempt.runtime_plan_checksum
        ):
            raise ValueError("runtime plan checksum no longer matches the pinned ExamVersion")
        now = _now()
        duration = attempt.runtime_plan.get("duration_seconds")
        attempt.started_at = now
        attempt.deadline_at = now + timedelta(seconds=int(duration)) if duration else None
        attempt.status = CandidateAttemptStatus.IN_PROGRESS
        attempt.session = CandidateExamSession(
            attempt=attempt, last_seen_at=now, created_at=now, updated_at=now
        )
        session.add(attempt.session)
        session.flush()
        CandidateRuntimeService._audit(
            session,
            context,
            "exam_attempt.started",
            "CANDIDATE_ATTEMPT",
            attempt.id,
            attempt.organisation_id,
        )
        return attempt

    @staticmethod
    def expire_if_due(
        session: Session, context: TenantContext, attempt: CandidateExamAttempt
    ) -> CandidateExamAttempt:
        if (
            attempt.status is CandidateAttemptStatus.IN_PROGRESS
            and attempt.deadline_at
            and _now() >= attempt.deadline_at
        ):
            attempt.status = CandidateAttemptStatus.EXPIRED
            attempt.expired_at = _now()
            CandidateRuntimeService._audit(
                session,
                context,
                "exam_attempt.expired",
                "CANDIDATE_ATTEMPT",
                attempt.id,
                attempt.organisation_id,
            )
            session.flush()
        return attempt

    @staticmethod
    def heartbeat(
        session: Session, context: TenantContext, attempt_id: uuid.UUID
    ) -> CandidateExamAttempt:
        attempt = CandidateRuntimeService._owned_attempt(session, context, attempt_id)
        CandidateRuntimeService.expire_if_due(session, context, attempt)
        if attempt.session:
            attempt.session.last_seen_at = _now()
            attempt.session.updated_at = _now()
        session.flush()
        return attempt

    @staticmethod
    def _binding_payload(
        attempt: CandidateExamAttempt, binding_id: uuid.UUID
    ) -> tuple[dict[str, Any], str]:
        for module in attempt.runtime_plan["modules"]:
            for section in module["sections"]:
                for task in section["tasks"]:
                    for item in task["items"]:
                        if item["binding_id"] == str(binding_id):
                            return item, task["response_mode"]
        raise ValueError("binding is not part of this attempt")

    @staticmethod
    def _validate_payload(item: dict[str, Any], mode: str, payload: dict[str, Any]) -> None:
        if mode == PackResponseMode.SELECT_ONE.value:
            option_id = payload.get("option_id")
            options = {
                str(row.get("option_id")) for row in item.get("content", {}).get("options", [])
            }
            if (
                set(payload) != {"option_id"}
                or not isinstance(option_id, str)
                or option_id not in options
            ):
                raise ValueError("invalid SELECT_ONE response")
        elif mode == PackResponseMode.SELECT_MULTIPLE.value:
            option_ids = payload.get("option_ids")
            options = {
                str(row.get("option_id")) for row in item.get("content", {}).get("options", [])
            }
            if (
                set(payload) != {"option_ids"}
                or not isinstance(option_ids, list)
                or len(option_ids) != len(set(option_ids))
                or not set(option_ids) <= options
            ):
                raise ValueError("invalid SELECT_MULTIPLE response")
        elif mode in {PackResponseMode.TEXT.value, PackResponseMode.LONG_TEXT.value}:
            text = payload.get("text")
            if (
                set(payload) != {"text"}
                or not isinstance(text, str)
                or len(text) > (4000 if mode == PackResponseMode.TEXT.value else 20000)
            ):
                raise ValueError("invalid text response")
        elif mode == PackResponseMode.AUDIO.value:
            asset_id = payload.get("media_asset_id")
            if set(payload) != {"media_asset_id"} or not isinstance(asset_id, str):
                raise ValueError("invalid AUDIO response")

    @staticmethod
    def save_response(
        session: Session,
        context: TenantContext,
        attempt_id: uuid.UUID,
        binding_id: uuid.UUID,
        *,
        payload: dict[str, Any],
        client_operation_id: uuid.UUID,
        expected_revision: int,
        client_observed_at: Optional[datetime] = None,
        audio_storage: Any = None,
    ) -> CandidateResponse:
        attempt = SpeakingAudioService.lock_attempt(session, context, attempt_id)
        CandidateRuntimeService.expire_if_due(session, context, attempt)
        item, mode = CandidateRuntimeService._binding_payload(attempt, binding_id)
        CandidateRuntimeService._validate_payload(item, mode, payload)
        digest = _payload_checksum(payload)
        existing_operation = session.scalar(
            select(CandidateResponseRevision).where(
                CandidateResponseRevision.attempt_id == attempt.id,
                CandidateResponseRevision.client_operation_id == client_operation_id,
            )
        )
        if existing_operation:
            if (
                existing_operation.payload_checksum != digest
                or expected_revision != existing_operation.revision_number - 1
            ):
                raise RuntimeConflict("RUNTIME.IDEMPOTENCY_KEY_REUSED")
            response = session.get(CandidateResponse, existing_operation.candidate_response_id)
            if response is None or response.exam_version_item_binding_id != binding_id:
                raise RuntimeConflict("RUNTIME.IDEMPOTENCY_KEY_REUSED")
            if mode == PackResponseMode.AUDIO.value:
                from app.config import get_settings
                from app.knowledge.storage import StorageError, build_object_storage

                replay_asset = session.get(
                    CandidateMediaAsset, uuid.UUID(str(payload["media_asset_id"]))
                )
                if replay_asset is None:
                    raise RuntimeConflict("audio attachment history is unavailable")
                SpeakingAudioService.bind_to_response_revision(
                    session, revision=existing_operation, asset=replay_asset
                )
                replay_storage = SpeakingAudioService.durable_storage(
                    audio_storage or build_object_storage(get_settings())
                )
                if not SpeakingAudioService._object_matches(
                    replay_storage,
                    replay_asset.storage_key,
                    replay_asset.sha256,
                    replay_asset.byte_size,
                ):
                    raise StorageError("attached audio object is missing")
            return response
        if attempt.status is not CandidateAttemptStatus.IN_PROGRESS:
            raise RuntimeExpired("attempt is not writable")
        asset = None
        if mode == PackResponseMode.AUDIO.value:
            from app.config import get_settings
            from app.knowledge.storage import build_object_storage

            asset = session.get(CandidateMediaAsset, uuid.UUID(str(payload["media_asset_id"])))
            if asset is None:
                raise ValueError("audio media asset is unavailable")
            if (
                asset.organisation_id != context.organisation_id
                or asset.candidate_user_id != context.user_id
            ):
                raise PermissionError("audio asset ownership required")
            session.scalar(
                select(CandidateAudioRecordingSession)
                .where(CandidateAudioRecordingSession.id == asset.recording_session_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            SpeakingAudioService.validate_asset_identity(session, attempt, binding_id, asset)
            backend = SpeakingAudioService.durable_storage(
                audio_storage or build_object_storage(get_settings())
            )
            if not SpeakingAudioService._object_matches(
                backend, asset.storage_key, asset.sha256, asset.byte_size
            ):
                raise ValueError("audio object is missing; attachment was not committed")
        response = session.scalar(
            select(CandidateResponse)
            .where(
                CandidateResponse.attempt_id == attempt.id,
                CandidateResponse.exam_version_item_binding_id == binding_id,
            )
            .with_for_update()
        )
        if response is None:
            if expected_revision != 0:
                raise RuntimeConflict("stale response revision")
            response = CandidateResponse(
                attempt_id=attempt.id,
                exam_version_item_binding_id=binding_id,
                current_revision=0,
                response_schema_version="1",
                current_payload={},
            )
            session.add(response)
            session.flush()
        elif response.current_revision != expected_revision:
            raise RuntimeConflict("stale response revision")
        revision = CandidateResponseRevision(
            candidate_response_id=response.id,
            attempt_id=attempt.id,
            revision_number=response.current_revision + 1,
            client_operation_id=client_operation_id,
            payload=payload,
            payload_checksum=digest,
            received_at=_now(),
            client_observed_at=client_observed_at,
        )
        response.current_revision += 1
        response.current_payload = payload
        response.first_saved_at = response.first_saved_at or _now()
        response.last_saved_at = _now()
        session.add(revision)
        try:
            session.flush()
        except IntegrityError as exc:
            session.rollback()
            replay = session.scalar(
                select(CandidateResponseRevision).where(
                    CandidateResponseRevision.attempt_id == attempt.id,
                    CandidateResponseRevision.client_operation_id == client_operation_id,
                )
            )
            if replay is not None and replay.payload_checksum == digest:
                stored = session.get(CandidateResponse, replay.candidate_response_id)
                if stored is not None:
                    return stored
            raise RuntimeConflict("RUNTIME.IDEMPOTENCY_KEY_REUSED") from exc
        if mode == PackResponseMode.AUDIO.value:
            if asset is None:
                raise ValueError("audio media asset is not ready for this attempt")
            SpeakingAudioService.bind_to_response_revision(session, revision=revision, asset=asset)
            session.flush()
        return response

    @staticmethod
    def navigate(
        session: Session, context: TenantContext, attempt_id: uuid.UUID, binding_id: uuid.UUID
    ) -> CandidateExamSession:
        attempt = CandidateRuntimeService._owned_attempt(session, context, attempt_id)
        CandidateRuntimeService.expire_if_due(session, context, attempt)
        if attempt.status is not CandidateAttemptStatus.IN_PROGRESS or attempt.session is None:
            raise RuntimeExpired("attempt is not navigable")
        CandidateRuntimeService._binding_payload(attempt, binding_id)
        all_bindings: list[tuple[str, str]] = []
        for module in attempt.runtime_plan["modules"]:
            for section in module["sections"]:
                for task in section["tasks"]:
                    for item in task["items"]:
                        all_bindings.append((item["binding_id"], task["navigation_mode"]))
        target = str(binding_id)
        positions = {value: index for index, (value, _mode) in enumerate(all_bindings)}
        if target not in positions:
            raise ValueError("navigation target is not part of this attempt")
        previous = attempt.session.current_binding_id
        if previous and str(previous) in positions:
            mode = all_bindings[positions[str(previous)]][1]
            if mode in {"LINEAR", "NO_BACKTRACK"} and positions[target] < positions[str(previous)]:
                raise ValueError("navigation policy forbids moving backwards")
        attempt.session.current_binding_id = binding_id
        attempt.session.updated_at = _now()
        attempt.session.last_seen_at = _now()
        session.flush()
        return attempt.session

    @staticmethod
    def submit(
        session: Session, context: TenantContext, attempt_id: uuid.UUID
    ) -> CandidateExamAttempt:
        attempt = SpeakingAudioService.lock_attempt(session, context, attempt_id)
        CandidateRuntimeService.expire_if_due(session, context, attempt)
        if attempt.status is CandidateAttemptStatus.SUBMITTED:
            return attempt
        if attempt.status is not CandidateAttemptStatus.IN_PROGRESS:
            raise RuntimeExpired("attempt cannot be submitted")
        SpeakingAudioService.assert_submittable(session, attempt)
        attempt.status = CandidateAttemptStatus.SUBMITTED
        attempt.submitted_at = _now()
        if attempt.assignment:
            attempt.assignment.status = CandidateAssignmentStatus.COMPLETED
        for response in attempt.responses:
            response.finalized_at = attempt.submitted_at
        session.flush()
        CandidateRuntimeService._audit(
            session,
            context,
            "exam_attempt.submitted",
            "CANDIDATE_ATTEMPT",
            attempt.id,
            attempt.organisation_id,
        )
        return attempt

    @staticmethod
    def runtime_state(
        session: Session, context: TenantContext, attempt_id: uuid.UUID
    ) -> dict[str, Any]:
        attempt = CandidateRuntimeService._owned_attempt(session, context, attempt_id)
        CandidateRuntimeService.expire_if_due(session, context, attempt)
        return {
            "attempt_id": str(attempt.id),
            "status": attempt.status.value,
            "server_now": _now().isoformat(),
            "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
            "deadline_at": attempt.deadline_at.isoformat() if attempt.deadline_at else None,
            "runtime_plan_checksum": attempt.runtime_plan_checksum,
            "runtime_plan": attempt.runtime_plan,
            "session": {
                "current_binding_id": str(attempt.session.current_binding_id)
                if attempt.session and attempt.session.current_binding_id
                else None
            },
            "responses": [
                {
                    "binding_id": str(response.exam_version_item_binding_id),
                    "revision": response.current_revision,
                    "payload": response.current_payload,
                }
                for response in attempt.responses
            ],
        }
