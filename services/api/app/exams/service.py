from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationService, TenantContext
from app.models import (
    Exam,
    ExamVersion,
    ExamVersionItemBinding,
    ExamVersionModule,
    ExamVersionSection,
    ExamVersionTask,
    HumanReviewRequest,
    QuestionBankItemVersion,
    StandardPackModule,
    StandardPackTaskSpecification,
    StandardPackVersion,
)
from app.models.enums import (
    AssessmentClassification,
    ExamOriginType,
    ExamVersionStatus,
    HumanReviewStatus,
    QuestionBankLifecycleStatus,
)
from app.question_bank.service import (
    PUBLISHABLE_RIGHTS,
    QuestionBankService,
    ValidationIssue,
    ValidationResult,
    delivery_projection,
)
from app.rules.engine import EvaluationContext, RulesEngine

MUTABLE_STATUSES = {
    ExamVersionStatus.DRAFT,
    ExamVersionStatus.VALIDATION_REQUIRED,
    ExamVersionStatus.HUMAN_REVIEW,
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def exam_checksum(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExamStudioService:
    @staticmethod
    def _require_scope(
        context: TenantContext, scope_type: str, organisation_id: Optional[uuid.UUID]
    ) -> None:
        if scope_type == "PLATFORM_SHARED":
            if organisation_id is not None or not context.platform_scope:
                raise ValueError("platform-shared exams require platform scope")
            AuthorizationService.require_permission(context, "exam.manage")
        elif scope_type == "ORGANISATION_PRIVATE":
            if organisation_id is None:
                raise ValueError("organisation-private exams require an organisation")
            AuthorizationService.require_permission(context, "exam.manage", organisation_id)
        else:
            raise ValueError("invalid exam scope")

    @staticmethod
    def _require_visible(context: TenantContext, exam: Exam, permission: str = "exam.read") -> None:
        if exam.scope_type == "PLATFORM_SHARED":
            AuthorizationService.require_permission(context, permission)
        else:
            AuthorizationService.require_permission(context, permission, exam.organisation_id)

    @staticmethod
    def create_exam(
        session: Session,
        context: TenantContext,
        *,
        code: str,
        name: str,
        scope_type: str,
        organisation_id: Optional[uuid.UUID] = None,
        description: Optional[str] = None,
    ) -> Exam:
        ExamStudioService._require_scope(context, scope_type, organisation_id)
        exam = Exam(
            code=code,
            name=name,
            description=description,
            scope_type=scope_type,
            organisation_id=organisation_id,
            created_at=_now(),
            created_by_user_id=context.user_id,
        )
        session.add(exam)
        session.flush()
        if AuthorizationService.has_permission(context, "audit.read", exam.organisation_id):
            AuditService.record(
                session,
                context,
                event_type="exam.created",
                entity_type="EXAM",
                entity_id=str(exam.id),
                organisation_id=exam.organisation_id,
                metadata={"scope_type": exam.scope_type, "code": exam.code},
            )
        return exam

    @staticmethod
    def create_version(
        session: Session,
        context: TenantContext,
        exam: Exam,
        *,
        version_label: str,
        standard_pack_version_id: uuid.UUID,
        classification: AssessmentClassification = AssessmentClassification.PRACTICE,
        supersedes_version_id: Optional[uuid.UUID] = None,
        origin_type: ExamOriginType = ExamOriginType.HUMAN_AUTHORED,
        proposed_by_ai_run_id: Optional[uuid.UUID] = None,
        proposed_by_ai_step_id: Optional[uuid.UUID] = None,
    ) -> ExamVersion:
        ExamStudioService._require_visible(context, exam, "exam.manage")
        pack = session.get(StandardPackVersion, standard_pack_version_id)
        if pack is None:
            raise ValueError("standard pack version not found")
        if supersedes_version_id is not None:
            superseded = session.get(ExamVersion, supersedes_version_id)
            if superseded is None or superseded.exam_id != exam.id:
                raise ValueError("superseded version must belong to the same Exam")
        if classification.value not in pack.allowed_classifications:
            raise ValueError("classification is not allowed by the Standard Pack")
        version = ExamVersion(
            exam=exam,
            version_label=version_label,
            status=ExamVersionStatus.DRAFT,
            standard_pack_version_id=pack.id,
            classification=classification,
            origin_type=origin_type,
            proposed_by_ai_run_id=proposed_by_ai_run_id,
            proposed_by_ai_step_id=proposed_by_ai_step_id,
            supersedes_version_id=supersedes_version_id,
            timing_override={},
            navigation_override={},
            created_at=_now(),
            created_by_user_id=context.user_id,
            checksum=exam_checksum(
                {
                    "standard_pack_version_id": str(pack.id),
                    "classification": classification.value,
                    "modules": [],
                }
            ),
        )
        session.add(version)
        session.flush()
        ExamStudioService._audit(session, context, "exam.version.created", version, exam)
        if supersedes_version_id is not None:
            ExamStudioService._audit(session, context, "exam.version.superseded", version, exam)
        return version

    @staticmethod
    def _audit(
        session: Session, context: TenantContext, event_type: str, version: ExamVersion, exam: Exam
    ) -> None:
        if AuthorizationService.has_permission(context, "audit.read", exam.organisation_id):
            AuditService.record(
                session,
                context,
                event_type=event_type,
                entity_type="EXAM_VERSION",
                entity_id=str(version.id),
                entity_version=version.version_label,
                organisation_id=exam.organisation_id,
                metadata={
                    "exam_id": str(exam.id),
                    "checksum": version.checksum,
                    "status": version.status.value,
                },
            )

    @staticmethod
    def _get_version(session: Session, version_id: uuid.UUID) -> ExamVersion:
        version = session.get(ExamVersion, version_id)
        if version is None:
            raise ValueError("exam version not found")
        return version

    @staticmethod
    def _assert_mutable(version: ExamVersion) -> None:
        if version.status not in MUTABLE_STATUSES:
            raise ValueError("published or retired ExamVersions are immutable")

    @staticmethod
    def materialize_blueprint(
        session: Session, context: TenantContext, version: ExamVersion
    ) -> ExamVersion:
        ExamStudioService._require_visible(context, version.exam, "exam.manage")
        ExamStudioService._assert_mutable(version)
        pack = session.get(StandardPackVersion, version.standard_pack_version_id)
        if pack is None:
            raise ValueError("standard pack version not found")
        for module in sorted(pack.modules, key=lambda row: row.sequence):
            concrete_module = session.scalar(
                select(ExamVersionModule).where(
                    ExamVersionModule.exam_version_id == version.id,
                    ExamVersionModule.standard_pack_module_id == module.id,
                )
            )
            if concrete_module is None:
                concrete_module = ExamVersionModule(
                    exam_version=version, standard_pack_module_id=module.id, ordinal=module.sequence
                )
                session.add(concrete_module)
                session.flush()
            for section in sorted(module.sections, key=lambda row: row.sequence):
                concrete_section = session.scalar(
                    select(ExamVersionSection).where(
                        ExamVersionSection.exam_version_module_id == concrete_module.id,
                        ExamVersionSection.standard_pack_section_id == section.id,
                    )
                )
                if concrete_section is None:
                    concrete_section = ExamVersionSection(
                        module=concrete_module,
                        standard_pack_section_id=section.id,
                        ordinal=section.sequence,
                    )
                    session.add(concrete_section)
                    session.flush()
                for task in sorted(section.tasks, key=lambda row: row.sequence):
                    if (
                        session.scalar(
                            select(ExamVersionTask).where(
                                ExamVersionTask.exam_version_section_id == concrete_section.id,
                                ExamVersionTask.standard_pack_task_spec_id == task.id,
                            )
                        )
                        is None
                    ):
                        session.add(
                            ExamVersionTask(
                                section=concrete_section,
                                standard_pack_task_spec_id=task.id,
                                ordinal=task.sequence,
                            )
                        )
        session.flush()
        ExamStudioService._refresh_checksum(session, version)
        ExamStudioService._audit(
            session, context, "exam.blueprint.materialized", version, version.exam
        )
        return version

    @staticmethod
    def _task_for_version(
        session: Session, version: ExamVersion, task_id: uuid.UUID
    ) -> ExamVersionTask:
        task = session.get(ExamVersionTask, task_id)
        if task is None or task.section.module.exam_version_id != version.id:
            raise ValueError("task is not part of this ExamVersion")
        return task

    @staticmethod
    def assign_item(
        session: Session,
        context: TenantContext,
        version: ExamVersion,
        *,
        task_id: uuid.UUID,
        item_version_id: uuid.UUID,
        slot_index: int,
        origin_type: ExamOriginType = ExamOriginType.HUMAN_AUTHORED,
        proposed_by_ai_run_id: Optional[uuid.UUID] = None,
        proposed_by_ai_step_id: Optional[uuid.UUID] = None,
    ) -> ExamVersionItemBinding:
        ExamStudioService._require_visible(context, version.exam, "exam.manage")
        ExamStudioService._assert_mutable(version)
        if slot_index < 0:
            raise ValueError("slot index must be non-negative")
        task = ExamStudioService._task_for_version(session, version, task_id)
        spec = session.get(StandardPackTaskSpecification, task.standard_pack_task_spec_id)
        if spec is None or slot_index >= spec.max_items:
            raise ValueError("slot exceeds task cardinality")
        if (
            session.scalar(
                select(ExamVersionItemBinding).where(
                    ExamVersionItemBinding.exam_version_task_id == task.id,
                    ExamVersionItemBinding.slot_index == slot_index,
                )
            )
            is not None
        ):
            raise ValueError("item slot is already assigned")
        candidate = QuestionBankService.eligible_bank(
            session,
            context,
            standard_pack_version_id=version.standard_pack_version_id,
            task_spec_id=spec.id,
            limit=100,
        )
        item = next((row for row in candidate if row.id == item_version_id), None)
        if item is None:
            raise ValueError("item version is not eligible for this exact task")
        binding = ExamVersionItemBinding(
            task=task,
            slot_index=slot_index,
            question_bank_item_version_id=item.id,
            origin_type=origin_type,
            proposed_by_ai_run_id=proposed_by_ai_run_id,
            proposed_by_ai_step_id=proposed_by_ai_step_id,
            created_at=_now(),
        )
        session.add(binding)
        session.flush()
        ExamStudioService._refresh_checksum(session, version)
        ExamStudioService._audit(session, context, "exam.item.assigned", version, version.exam)
        return binding

    @staticmethod
    def remove_item(
        session: Session, context: TenantContext, version: ExamVersion, binding_id: uuid.UUID
    ) -> None:
        ExamStudioService._require_visible(context, version.exam, "exam.manage")
        ExamStudioService._assert_mutable(version)
        binding = session.get(ExamVersionItemBinding, binding_id)
        if binding is None or binding.task.section.module.exam_version_id != version.id:
            raise ValueError("item binding is not part of this ExamVersion")
        session.delete(binding)
        session.flush()
        ExamStudioService._refresh_checksum(session, version)
        ExamStudioService._audit(session, context, "exam.item.removed", version, version.exam)

    @staticmethod
    def _payload(session: Session, version: ExamVersion) -> dict[str, Any]:
        modules: list[dict[str, Any]] = []
        for module in sorted(version.modules, key=lambda row: row.ordinal):
            sections: list[dict[str, Any]] = []
            for section in sorted(module.sections, key=lambda row: row.ordinal):
                tasks: list[dict[str, Any]] = []
                for task in sorted(section.tasks, key=lambda row: row.ordinal):
                    tasks.append(
                        {
                            "id": str(task.standard_pack_task_spec_id),
                            "ordinal": task.ordinal,
                            "items": [
                                {
                                    "slot": binding.slot_index,
                                    "item_version_id": str(binding.question_bank_item_version_id),
                                }
                                for binding in sorted(
                                    task.item_bindings, key=lambda row: row.slot_index
                                )
                            ],
                        }
                    )
                sections.append(
                    {
                        "id": str(section.standard_pack_section_id),
                        "ordinal": section.ordinal,
                        "tasks": tasks,
                    }
                )
            modules.append(
                {
                    "id": str(module.standard_pack_module_id),
                    "ordinal": module.ordinal,
                    "sections": sections,
                }
            )
        return {
            "standard_pack_version_id": str(version.standard_pack_version_id),
            "classification": version.classification.value,
            "timing_override": version.timing_override,
            "navigation_override": version.navigation_override,
            "modules": modules,
        }

    @staticmethod
    def _refresh_checksum(session: Session, version: ExamVersion) -> str:
        version.checksum = exam_checksum(ExamStudioService._payload(session, version))
        return version.checksum

    @staticmethod
    def validate(
        session: Session, context: TenantContext, version: ExamVersion
    ) -> ValidationResult:
        ExamStudioService._require_visible(context, version.exam, "exam.read")
        issues: list[ValidationIssue] = []
        pack = session.get(StandardPackVersion, version.standard_pack_version_id)
        if pack is None:
            issues.append(
                ValidationIssue(
                    "EXAM.PACK.MISSING",
                    "FAIL",
                    "BLUEPRINT",
                    "standard_pack_version_id",
                    "pinned Standard Pack version is missing",
                )
            )
            return ValidationResult("FAIL", tuple(issues), version.checksum)
        if version.classification.value not in pack.allowed_classifications:
            issues.append(
                ValidationIssue(
                    "EXAM.CLASSIFICATION.NOT_ALLOWED",
                    "FAIL",
                    "CLASSIFICATION",
                    "classification",
                    "classification is not allowed by the Standard Pack",
                )
            )
        if version.classification is AssessmentClassification.OFFICIAL_CERTIFICATION:
            issues.append(
                ValidationIssue(
                    "EXAM.CLASSIFICATION.CERTIFICATION_GUARD",
                    "FAIL",
                    "CLASSIFICATION",
                    "classification",
                    "official certification authorization is not available",
                )
            )
        if version.timing_override:
            issues.append(
                ValidationIssue(
                    "EXAM.TIMING.OVERRIDE_NOT_AUTHORIZED",
                    "FAIL",
                    "STRUCTURAL",
                    "timing_override",
                    "the current Standard Pack does not authorize timing overrides",
                )
            )
        if version.navigation_override:
            issues.append(
                ValidationIssue(
                    "EXAM.NAVIGATION.OVERRIDE_NOT_AUTHORIZED",
                    "FAIL",
                    "STRUCTURAL",
                    "navigation_override",
                    "the current Standard Pack does not authorize navigation overrides",
                )
            )
        expected_modules = {module.id for module in pack.modules if module.required}
        actual_modules = {module.standard_pack_module_id for module in version.modules}
        if expected_modules != actual_modules:
            issues.append(
                ValidationIssue(
                    "EXAM.BLUEPRINT.MODULE_COMPLETENESS",
                    "FAIL",
                    "BLUEPRINT",
                    "modules",
                    "materialized modules do not match the required pack blueprint",
                )
            )
        seen: set[uuid.UUID] = set()
        for module in version.modules:
            pack_module = session.get(StandardPackModule, module.standard_pack_module_id)
            if pack_module is None or pack_module.pack_version.id != pack.id:
                issues.append(
                    ValidationIssue(
                        "EXAM.BLUEPRINT.MODULE_OWNERSHIP",
                        "FAIL",
                        "BLUEPRINT",
                        f"modules.{module.ordinal}",
                        "module does not belong to the pinned pack",
                    )
                )
                continue
            for section in module.sections:
                if (
                    section.module.id != module.id
                    or section.module.standard_pack_module_id != pack_module.id
                ):
                    issues.append(
                        ValidationIssue(
                            "EXAM.BLUEPRINT.SECTION_OWNERSHIP",
                            "FAIL",
                            "BLUEPRINT",
                            f"modules.{module.ordinal}.sections.{section.ordinal}",
                            "section ownership mismatch",
                        )
                    )
                for task in section.tasks:
                    spec = session.get(
                        StandardPackTaskSpecification, task.standard_pack_task_spec_id
                    )
                    if spec is None or spec.section.id != section.standard_pack_section_id:
                        issues.append(
                            ValidationIssue(
                                "EXAM.BLUEPRINT.TASK_OWNERSHIP",
                                "FAIL",
                                "BLUEPRINT",
                                f"tasks.{task.ordinal}",
                                "task specification ownership mismatch",
                            )
                        )
                        continue
                    count = len(task.item_bindings)
                    if count < spec.min_items or count > spec.max_items:
                        issues.append(
                            ValidationIssue(
                                "EXAM.COMPLETENESS.ITEM_CARDINALITY",
                                "FAIL",
                                "STRUCTURAL",
                                f"tasks.{task.ordinal}.items",
                                "required item slots are incomplete or exceed the task maximum",
                            )
                        )
                    for binding in task.item_bindings:
                        if binding.question_bank_item_version_id in seen:
                            issues.append(
                                ValidationIssue(
                                    "EXAM.ITEM.DUPLICATE",
                                    "FAIL",
                                    "STRUCTURAL",
                                    f"tasks.{task.ordinal}.items.{binding.slot_index}",
                                    "the same ItemVersion is reused in this exam",
                                )
                            )
                        seen.add(binding.question_bank_item_version_id)
                        item = session.get(
                            QuestionBankItemVersion, binding.question_bank_item_version_id
                        )
                        if item is None or item.status is not QuestionBankLifecycleStatus.ACTIVE:
                            issues.append(
                                ValidationIssue(
                                    "EXAM.ITEM.NOT_ACTIVE",
                                    "FAIL",
                                    "QUESTION_BANK",
                                    f"tasks.{task.ordinal}.items.{binding.slot_index}",
                                    "bound item is not ACTIVE",
                                )
                            )
                            continue
                        if item.rights_status not in PUBLISHABLE_RIGHTS or (
                            item.rights_expires_at and item.rights_expires_at <= _now()
                        ):
                            issues.append(
                                ValidationIssue(
                                    "EXAM.RIGHTS.BLOCKED",
                                    "FAIL",
                                    "RIGHTS",
                                    f"tasks.{task.ordinal}.items.{binding.slot_index}",
                                    "item rights do not permit current delivery",
                                )
                            )
                        compatibility = QuestionBankService.validate_pack_compatibility(
                            session, item, pack.id, spec.id
                        )
                        if compatibility.status != "PASS":
                            issues.extend(compatibility.issues)
        rules = [
            (binding.rule_version.definition, binding.rule_version)
            for binding in pack.rule_bindings
            if binding.required
        ]
        report = RulesEngine().evaluate(
            rules,
            EvaluationContext(
                facts={
                    "exam": {
                        "classification": version.classification.value,
                        "module_count": len(version.modules),
                        "section_count": sum(len(module.sections) for module in version.modules),
                        "task_count": sum(
                            len(section.tasks)
                            for module in version.modules
                            for section in module.sections
                        ),
                        "item_count": len(seen),
                    },
                    "pack": {"requested_classification": version.classification.value},
                },
                standard_pack_version_id=str(pack.id),
                standard_pack_checksum=pack.configuration_checksum,
            ),
        )
        if report.overall_status.value in {"FAIL", "ERROR"}:
            issues.append(
                ValidationIssue(
                    "EXAM.RULE.BLOCKED",
                    "FAIL",
                    "RULE",
                    "rules",
                    f"bound Standard Pack rules evaluated {report.overall_status.value}",
                    report.evaluation_id,
                )
            )
        if version.modules and not any(
            task.item_bindings
            for module in version.modules
            for section in module.sections
            for task in section.tasks
        ):
            issues.append(
                ValidationIssue(
                    "EXAM.COMPLETENESS.NO_ITEMS",
                    "FAIL",
                    "STRUCTURAL",
                    "items",
                    "exam has no assigned item slots",
                )
            )
        status = (
            "FAIL"
            if any(issue.severity in {"FAIL", "BLOCKING", "ERROR"} for issue in issues)
            else "PASS"
        )
        ExamStudioService._refresh_checksum(session, version)
        ExamStudioService._audit(
            session, context, "exam.validation.completed", version, version.exam
        )
        return ValidationResult(status, tuple(issues), version.checksum)

    @staticmethod
    def request_review(
        session: Session, context: TenantContext, version: ExamVersion
    ) -> HumanReviewRequest:
        ExamStudioService._require_visible(context, version.exam, "exam.review")
        if version.status not in {ExamVersionStatus.DRAFT, ExamVersionStatus.VALIDATION_REQUIRED}:
            raise ValueError("only mutable draft ExamVersions may request review")
        validation = ExamStudioService.validate(session, context, version)
        if validation.status != "PASS":
            raise ValueError("ExamVersion must validate before review")
        version.status = ExamVersionStatus.HUMAN_REVIEW
        review = HumanReviewRequest(
            review_type="EXAM_VERSION",
            entity_type="EXAM_VERSION",
            entity_id=version.id,
            status=HumanReviewStatus.PENDING,
            requested_by=context.user_id,
            requested_at=_now(),
            reviewed_checksum=version.checksum,
        )
        session.add(review)
        session.flush()
        ExamStudioService._audit(session, context, "exam.review_requested", version, version.exam)
        return review

    @staticmethod
    def transition(
        session: Session, context: TenantContext, version: ExamVersion, target: ExamVersionStatus
    ) -> ExamVersion:
        permission = (
            "exam.publish"
            if target is ExamVersionStatus.PUBLISHED
            else "exam.activate"
            if target is ExamVersionStatus.ACTIVE
            else "exam.review"
        )
        ExamStudioService._require_visible(context, version.exam, permission)
        allowed_transitions = {
            ExamVersionStatus.DRAFT: {
                ExamVersionStatus.VALIDATION_REQUIRED,
                ExamVersionStatus.HUMAN_REVIEW,
            },
            ExamVersionStatus.VALIDATION_REQUIRED: {
                ExamVersionStatus.DRAFT,
                ExamVersionStatus.HUMAN_REVIEW,
            },
            ExamVersionStatus.HUMAN_REVIEW: {
                ExamVersionStatus.DRAFT,
                ExamVersionStatus.REJECTED,
                ExamVersionStatus.APPROVED,
            },
            ExamVersionStatus.APPROVED: {ExamVersionStatus.PUBLISHED, ExamVersionStatus.REJECTED},
            ExamVersionStatus.PUBLISHED: {ExamVersionStatus.ACTIVE, ExamVersionStatus.RETIRED},
            ExamVersionStatus.ACTIVE: {ExamVersionStatus.RETIRED},
        }
        if target not in allowed_transitions.get(version.status, set()):
            raise ValueError(
                f"invalid ExamVersion transition {version.status.value} -> {target.value}"
            )
        if (
            target is ExamVersionStatus.PUBLISHED
            and version.status is not ExamVersionStatus.APPROVED
        ):
            raise ValueError("only approved exams may be published")
        if target is ExamVersionStatus.ACTIVE and version.status is not ExamVersionStatus.PUBLISHED:
            raise ValueError("only published exams may be activated")
        if target in {
            ExamVersionStatus.APPROVED,
            ExamVersionStatus.PUBLISHED,
            ExamVersionStatus.ACTIVE,
        }:
            validation = ExamStudioService.validate(session, context, version)
            if validation.status != "PASS":
                raise ValueError("deterministic validation blocks lifecycle transition")
            if target is ExamVersionStatus.APPROVED:
                review = session.scalar(
                    select(HumanReviewRequest).where(
                        HumanReviewRequest.entity_type == "EXAM_VERSION",
                        HumanReviewRequest.entity_id == version.id,
                        HumanReviewRequest.status.in_(
                            [HumanReviewStatus.PENDING, HumanReviewStatus.IN_REVIEW]
                        ),
                    )
                )
                if review is None or review.reviewed_checksum != version.checksum:
                    raise ValueError("current human review with matching checksum is required")
                review.status = HumanReviewStatus.APPROVED
                review.decided_by = context.user_id
                review.decided_at = _now()
            if target is ExamVersionStatus.PUBLISHED:
                version.published_at = _now()
                version.published_by_user_id = context.user_id
            if target is ExamVersionStatus.ACTIVE:
                version.activated_at = _now()
        version.status = target
        session.flush()
        ExamStudioService._audit(
            session, context, f"exam.{target.value.casefold()}", version, version.exam
        )
        return version

    @staticmethod
    def resolve_as_of(
        session: Session, context: TenantContext, exam_id: uuid.UUID, as_of: datetime
    ) -> Optional[ExamVersion]:
        exam = session.get(Exam, exam_id)
        if exam is None:
            return None
        ExamStudioService._require_visible(context, exam)
        versions = list(
            session.scalars(
                select(ExamVersion)
                .where(
                    ExamVersion.exam_id == exam.id,
                    ExamVersion.status.in_(
                        [
                            ExamVersionStatus.ACTIVE,
                            ExamVersionStatus.PUBLISHED,
                            ExamVersionStatus.RETIRED,
                            ExamVersionStatus.SUPERSEDED,
                        ]
                    ),
                    (ExamVersion.effective_from.is_(None) | (ExamVersion.effective_from <= as_of)),
                    (ExamVersion.effective_until.is_(None) | (ExamVersion.effective_until > as_of)),
                )
                .order_by(
                    ExamVersion.effective_from.desc().nullslast(), ExamVersion.created_at.desc()
                )
            )
        )
        return versions[0] if versions else None

    @staticmethod
    def resolve_current(
        session: Session, context: TenantContext, exam_id: uuid.UUID, now: Optional[datetime] = None
    ) -> Optional[ExamVersion]:
        exam = session.get(Exam, exam_id)
        if exam is None:
            return None
        ExamStudioService._require_visible(context, exam)
        point = now or _now()
        return session.scalar(
            select(ExamVersion)
            .where(
                ExamVersion.exam_id == exam.id,
                ExamVersion.status == ExamVersionStatus.ACTIVE,
                (ExamVersion.effective_from.is_(None) | (ExamVersion.effective_from <= point)),
                (ExamVersion.effective_until.is_(None) | (ExamVersion.effective_until > point)),
            )
            .order_by(ExamVersion.effective_from.desc().nullslast(), ExamVersion.created_at.desc())
            .limit(1)
        )

    @staticmethod
    def admin_manifest(
        session: Session, context: TenantContext, version: ExamVersion
    ) -> dict[str, Any]:
        ExamStudioService._require_visible(context, version.exam, "exam.export")
        items = []
        for binding in _bindings(version):
            item = session.get(QuestionBankItemVersion, binding.question_bank_item_version_id)
            if item is None:
                raise ValueError("bound item not found")
            items.append(
                {
                    "binding_id": str(binding.id),
                    "item_version_id": str(binding.question_bank_item_version_id),
                    "content": item.content,
                }
            )
        ExamStudioService._audit(session, context, "exam.exported", version, version.exam)
        return {
            "exam_version_id": str(version.id),
            "checksum": version.checksum,
            "status": version.status.value,
            "standard_pack_version_id": str(version.standard_pack_version_id),
            "classification": version.classification.value,
            "structure": ExamStudioService._payload(session, version),
            "items": items,
        }

    @staticmethod
    def delivery_manifest(
        session: Session, context: TenantContext, version: ExamVersion
    ) -> dict[str, Any]:
        ExamStudioService._require_visible(context, version.exam, "exam.read")
        if version.status not in {ExamVersionStatus.PUBLISHED, ExamVersionStatus.ACTIVE}:
            raise ValueError("delivery preview requires a published ExamVersion")
        structure = []
        for module in version.modules:
            sections = []
            for section in module.sections:
                tasks = []
                for task in section.tasks:
                    spec = session.get(
                        StandardPackTaskSpecification, task.standard_pack_task_spec_id
                    )
                    if spec is None:
                        raise ValueError("task specification not found")
                    safe_items = []
                    for binding in task.item_bindings:
                        item = session.get(
                            QuestionBankItemVersion, binding.question_bank_item_version_id
                        )
                        if item is None:
                            raise ValueError("bound item not found")
                        safe_items.append(delivery_projection(item))
                    tasks.append(
                        {
                            "task_id": str(task.standard_pack_task_spec_id),
                            "response_mode": spec.response_mode.value,
                            "items": safe_items,
                        }
                    )
                sections.append(
                    {"section_id": str(section.standard_pack_section_id), "tasks": tasks}
                )
            structure.append(
                {"module_id": str(module.standard_pack_module_id), "sections": sections}
            )
        return {
            "exam_version_id": str(version.id),
            "checksum": version.checksum,
            "title": version.exam.name,
            "standard_pack_version_id": str(version.standard_pack_version_id),
            "classification": version.classification.value,
            "structure": structure,
        }

    @staticmethod
    def diff(
        session: Session, context: TenantContext, left: ExamVersion, right: ExamVersion
    ) -> dict[str, Any]:
        ExamStudioService._require_visible(context, left.exam, "exam.read")
        ExamStudioService._require_visible(context, right.exam, "exam.read")
        left_payload, right_payload = (
            ExamStudioService._payload(session, left),
            ExamStudioService._payload(session, right),
        )
        return {
            "pack_changed": left.standard_pack_version_id != right.standard_pack_version_id,
            "composition_changed": left_payload != right_payload,
            "checksum_changed": left.checksum != right.checksum,
            "left_checksum": left.checksum,
            "right_checksum": right.checksum,
        }

    @staticmethod
    def assembly_gaps(session: Session, version: ExamVersion) -> list[dict[str, Any]]:
        """Return deterministic missing-slot facts for a draft assembly."""
        gaps: list[dict[str, Any]] = []
        for module in version.modules:
            for section in module.sections:
                for task in section.tasks:
                    spec = session.get(
                        StandardPackTaskSpecification, task.standard_pack_task_spec_id
                    )
                    if spec is None:
                        continue
                    for slot in range(len(task.item_bindings), spec.min_items):
                        gaps.append(
                            {
                                "code": "MISSING_ELIGIBLE_ITEM",
                                "pack_version_id": str(version.standard_pack_version_id),
                                "task_spec_id": str(spec.id),
                                "slot_index": slot,
                            }
                        )
        return gaps

    @staticmethod
    def propose_ai_assembly(
        session: Session,
        context: TenantContext,
        version: ExamVersion,
        proposals: list[dict[str, Any]],
        *,
        ai_run_id: Optional[uuid.UUID],
        ai_step_id: Optional[uuid.UUID],
    ) -> dict[str, Any]:
        """Accept only exact, server-revalidated DRAFT item proposals."""
        ExamStudioService._require_visible(context, version.exam, "exam.manage")
        ExamStudioService._assert_mutable(version)
        if version.status is not ExamVersionStatus.DRAFT:
            raise ValueError("AI assembly proposals are draft-only")
        assigned: list[str] = []
        for proposal in proposals:
            binding = ExamStudioService.assign_item(
                session,
                context,
                version,
                task_id=uuid.UUID(str(proposal["task_id"])),
                item_version_id=uuid.UUID(str(proposal["item_version_id"])),
                slot_index=int(proposal["slot_index"]),
                origin_type=ExamOriginType.AI_PROPOSED,
                proposed_by_ai_run_id=ai_run_id,
                proposed_by_ai_step_id=ai_step_id,
            )
            assigned.append(str(binding.id))
        ExamStudioService._audit(
            session, context, "exam.ai_proposal.created", version, version.exam
        )
        return {
            "status": version.status.value,
            "assigned_binding_ids": assigned,
            "gaps": ExamStudioService.assembly_gaps(session, version),
        }


def _bindings(version: ExamVersion) -> list[ExamVersionItemBinding]:
    return [
        binding
        for module in version.modules
        for section in module.sections
        for task in section.tasks
        for binding in task.item_bindings
    ]
