from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationService, TenantContext
from app.models import (
    HumanReviewRequest,
    QuestionBankItem,
    QuestionBankItemCefrLevel,
    QuestionBankItemVersion,
    QuestionBankStandardPackBinding,
    StandardPackTaskSpecification,
    StandardPackVersion,
)
from app.models.enums import (
    AssessmentFamily,
    HumanReviewStatus,
    PackMediaRequirement,
    PackResponseMode,
    QuestionBankLifecycleStatus,
    QuestionBankOriginType,
    QuestionBankRightsStatus,
    QuestionBankScoringPolicy,
    QuestionBankSkill,
    QuestionBankTaskType,
)
from app.question_bank.schemas import parse_item_content
from app.rules.engine import EvaluationContext, RulesEngine

MAX_TAGS = 30
MAX_EVIDENCE_BINDINGS = 50
MAX_PACK_BINDINGS = 20
CEFR_LEVELS = {"A1", "A2", "B1", "B2", "C1", "C2"}
PUBLISHABLE_RIGHTS = {
    QuestionBankRightsStatus.PRUFAI_ORIGINAL,
    QuestionBankRightsStatus.LICENSED,
    QuestionBankRightsStatus.PUBLIC_DOMAIN,
}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: str
    origin: str
    path: str
    message: str
    evidence_ref: Optional[str] = None


@dataclass(frozen=True)
class ValidationResult:
    status: str
    issues: tuple[ValidationIssue, ...]
    checksum: Optional[str]


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def item_checksum(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def normalized_fingerprint(content: dict[str, Any]) -> str:
    text = str(content.get("prompt", "")).casefold().split()
    return hashlib.sha256(" ".join(text).encode()).hexdigest()


def delivery_projection(version: QuestionBankItemVersion) -> dict[str, Any]:
    """Candidate-safe shape; answer keys and internal governance are omitted."""
    content = dict(version.content)
    for protected_key in (
        "correct_option_ids",
        "answer_key",
        "solution",
        "scoring_key",
        "reviewer_notes",
        "ai_critique",
        "validation",
        "legal_notes",
        "private_object_url",
    ):
        content.pop(protected_key, None)
    return {
        "item_version_id": str(version.id),
        "version_label": version.version_label,
        "assessment_family": version.assessment_family.value,
        "language": version.language,
        "task_type": version.task_type.value,
        "response_mode": version.response_mode.value,
        "content": content,
        "media_requirement": version.media_requirement.value,
    }


def item_rule_facts(version: QuestionBankItemVersion, cefr_levels: list[str]) -> dict[str, Any]:
    """Narrow, typed facts exposed to RulesEngine; no ORM reflection."""
    return {
        "item": {
            "assessment_family": version.assessment_family.value,
            "exam_family": version.exam_family,
            "language": version.language,
            "cefr_levels": list(cefr_levels),
            "task_type": version.task_type.value,
            "response_mode": version.response_mode.value,
            "media_requirement": version.media_requirement.value,
            "scoring_policy": version.scoring_policy.value,
            "max_points": str(version.max_points) if version.max_points is not None else None,
            "option_count": len(version.content.get("options", [])),
            "answer_count": len(version.content.get("correct_option_ids", [])),
            "rights_status": version.rights_status.value,
        }
    }


def parse_safe_import(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a machine-readable item import without changing lifecycle state."""
    forbidden = {"python", "javascript", "sql", "shell", "template", "exec", "eval"}
    if forbidden.intersection(str(key).casefold() for key in payload):
        raise ValueError("executable item fields are not permitted")
    content = payload.get("content")
    if not isinstance(content, dict):
        raise ValueError("import content must be an object")
    parse_item_content(content)
    return {"content": content, "status": QuestionBankLifecycleStatus.DRAFT.value}


def canonical_payload(
    *,
    assessment_family: AssessmentFamily,
    exam_family: Optional[str],
    language: Optional[str],
    skill: Optional[QuestionBankSkill],
    task_type: QuestionBankTaskType,
    response_mode: PackResponseMode,
    media_requirement: PackMediaRequirement,
    content: dict[str, Any],
    scoring_policy: QuestionBankScoringPolicy,
    max_points: Optional[Decimal],
    scoring_metadata: dict[str, Any],
    accessibility: dict[str, Any],
    rights_status: QuestionBankRightsStatus,
    rights_holder: Optional[str],
    licence_reference: Optional[str],
    permitted_use: Optional[str],
    rubric_reference: Optional[str],
    cefr_levels: tuple[str, ...] = (),
    pack_bindings: tuple[str, ...] = (),
    evidence_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "accessibility": accessibility,
        "assessment_family": assessment_family.value,
        "cefr_levels": sorted(cefr_levels),
        "content": content,
        "exam_family": exam_family,
        "evidence_ids": sorted(evidence_ids),
        "language": language,
        "max_points": str(max_points) if max_points is not None else None,
        "media_requirement": media_requirement.value,
        "pack_bindings": sorted(pack_bindings),
        "permitted_use": permitted_use,
        "response_mode": response_mode.value,
        "rights_holder": rights_holder,
        "rights_status": rights_status.value,
        "licence_reference": licence_reference,
        "rubric_reference": rubric_reference,
        "scoring_metadata": scoring_metadata,
        "scoring_policy": scoring_policy.value,
        "skill": skill.value if skill else None,
        "task_type": task_type.value,
    }


def validate_item_payload(
    *,
    assessment_family: AssessmentFamily,
    exam_family: Optional[str],
    language: Optional[str],
    cefr_levels: list[str],
    skill: Optional[QuestionBankSkill],
    task_type: QuestionBankTaskType,
    response_mode: PackResponseMode,
    media_requirement: PackMediaRequirement,
    content: dict[str, Any],
    scoring_policy: QuestionBankScoringPolicy,
    max_points: Optional[Decimal],
    scoring_metadata: dict[str, Any],
    rights_status: QuestionBankRightsStatus,
    licence_reference: Optional[str],
    rubric_reference: Optional[str],
    pack_task: Optional[dict[str, Any]] = None,
    rule_result: Optional[dict[str, Any]] = None,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    try:
        parsed = parse_item_content(content)
    except Exception as exc:  # noqa: BLE001
        issues.append(
            ValidationIssue(
                "ITEM.INVALID_CONTENT_SCHEMA", "FAIL", "STRUCTURAL", "content", str(exc)
            )
        )
        parsed = None
    if language and language != "de" and assessment_family is AssessmentFamily.LANGUAGE:
        issues.append(
            ValidationIssue(
                "ITEM.UNSUPPORTED_LANGUAGE",
                "FAIL",
                "STRUCTURAL",
                "language",
                "current language scope is de",
            )
        )
    if assessment_family is AssessmentFamily.LANGUAGE and not language:
        issues.append(
            ValidationIssue(
                "ITEM.LANGUAGE_REQUIRED",
                "FAIL",
                "STRUCTURAL",
                "language",
                "language is required for language items",
            )
        )
    if assessment_family is AssessmentFamily.INTEGRATION_KNOWLEDGE and cefr_levels:
        issues.append(
            ValidationIssue(
                "ITEM.LID_CEFR_NOT_ALLOWED",
                "FAIL",
                "STRUCTURAL",
                "cefr_levels",
                "LiD items cannot carry CEFR levels",
            )
        )
    if any(level not in CEFR_LEVELS for level in cefr_levels):
        issues.append(
            ValidationIssue(
                "ITEM.INVALID_CEFR", "FAIL", "STRUCTURAL", "cefr_levels", "unsupported CEFR level"
            )
        )
    if max_points is not None and max_points < 0:
        issues.append(
            ValidationIssue(
                "ITEM.NEGATIVE_POINTS",
                "FAIL",
                "STRUCTURAL",
                "max_points",
                "points must be non-negative",
            )
        )
    if task_type is QuestionBankTaskType.MULTIPLE_CHOICE:
        if not isinstance(parsed, type(None)):
            correct = getattr(parsed, "correct_option_ids", [])
            option_ids = {option.option_id for option in getattr(parsed, "options", [])}
            if not set(correct) <= option_ids:
                issues.append(
                    ValidationIssue(
                        "ITEM.INVALID_ANSWER_KEY",
                        "FAIL",
                        "STRUCTURAL",
                        "content.correct_option_ids",
                        "answer key refers to an unknown option",
                    )
                )
            if response_mode is PackResponseMode.SELECT_ONE and len(correct) != 1:
                issues.append(
                    ValidationIssue(
                        "ITEM.INVALID_ANSWER_CARDINALITY",
                        "FAIL",
                        "STRUCTURAL",
                        "content.correct_option_ids",
                        "SELECT_ONE requires one keyed option",
                    )
                )
        if scoring_policy is not QuestionBankScoringPolicy.OBJECTIVE_KEYED:
            issues.append(
                ValidationIssue(
                    "ITEM.OBJECTIVE_SCORING_REQUIRED",
                    "FAIL",
                    "STRUCTURAL",
                    "scoring_policy",
                    "multiple-choice requires objective scoring",
                )
            )
    if task_type in {QuestionBankTaskType.EXTENDED_WRITING, QuestionBankTaskType.ORAL_RESPONSE}:
        if scoring_policy is QuestionBankScoringPolicy.OBJECTIVE_KEYED:
            issues.append(
                ValidationIssue(
                    "ITEM.CONSTRUCTED_KEY_NOT_ALLOWED",
                    "FAIL",
                    "STRUCTURAL",
                    "scoring_policy",
                    "constructed response requires rubric/manual scoring",
                )
            )
    if rights_status is QuestionBankRightsStatus.LICENSED and not licence_reference:
        issues.append(
            ValidationIssue(
                "ITEM.LICENSE_REFERENCE_REQUIRED",
                "FAIL",
                "RIGHTS",
                "licence_reference",
                "licensed content requires a license reference",
            )
        )
    if pack_task:
        if pack_task.get("task_type") and pack_task["task_type"] != task_type.value:
            issues.append(
                ValidationIssue(
                    "ITEM.PACK_TASK_TYPE_MISMATCH",
                    "FAIL",
                    "PACK_COMPATIBILITY",
                    "task_type",
                    "task type does not match the exact task specification",
                )
            )
        if pack_task.get("response_mode") and pack_task["response_mode"] != response_mode.value:
            issues.append(
                ValidationIssue(
                    "ITEM.PACK_RESPONSE_MODE_MISMATCH",
                    "FAIL",
                    "PACK_COMPATIBILITY",
                    "response_mode",
                    "response mode does not match the exact task specification",
                )
            )
        if (
            pack_task.get("media_requirement")
            and pack_task["media_requirement"] != media_requirement.value
        ):
            issues.append(
                ValidationIssue(
                    "ITEM.PACK_MEDIA_MISMATCH",
                    "FAIL",
                    "PACK_COMPATIBILITY",
                    "media_requirement",
                    "media requirement does not match the exact task specification",
                )
            )
    if rule_result and rule_result.get("status") in {"FAIL", "BLOCKED"}:
        issues.append(
            ValidationIssue(
                "ITEM.RULE_BLOCKING_FAILURE",
                "FAIL",
                "RULE",
                "rules",
                str(rule_result.get("message", "a deterministic rule blocked this item")),
                rule_result.get("rule_version_id"),
            )
        )
    status = "FAIL" if any(issue.severity == "FAIL" for issue in issues) else "PASS"
    payload = canonical_payload(
        assessment_family=assessment_family,
        exam_family=exam_family,
        language=language,
        skill=skill,
        task_type=task_type,
        response_mode=response_mode,
        media_requirement=media_requirement,
        content=content,
        scoring_policy=scoring_policy,
        max_points=max_points,
        scoring_metadata=scoring_metadata,
        accessibility={},
        rights_status=rights_status,
        rights_holder=None,
        licence_reference=licence_reference,
        permitted_use=None,
        rubric_reference=rubric_reference,
        cefr_levels=tuple(cefr_levels),
    )
    return ValidationResult(status, tuple(issues), item_checksum(payload))


_TRANSITIONS = {
    QuestionBankLifecycleStatus.DRAFT: {
        QuestionBankLifecycleStatus.VALIDATION_REQUIRED,
        QuestionBankLifecycleStatus.REJECTED,
    },
    QuestionBankLifecycleStatus.VALIDATION_REQUIRED: {
        QuestionBankLifecycleStatus.HUMAN_REVIEW,
        QuestionBankLifecycleStatus.REJECTED,
    },
    QuestionBankLifecycleStatus.HUMAN_REVIEW: {
        QuestionBankLifecycleStatus.APPROVED,
        QuestionBankLifecycleStatus.REJECTED,
    },
    QuestionBankLifecycleStatus.APPROVED: {
        QuestionBankLifecycleStatus.PUBLISHED,
        QuestionBankLifecycleStatus.REJECTED,
    },
    QuestionBankLifecycleStatus.PUBLISHED: {
        QuestionBankLifecycleStatus.ACTIVE,
        QuestionBankLifecycleStatus.RETIRED,
    },
    QuestionBankLifecycleStatus.ACTIVE: {
        QuestionBankLifecycleStatus.RETIRED,
        QuestionBankLifecycleStatus.SUPERSEDED,
    },
    QuestionBankLifecycleStatus.RETIRED: set(),
    QuestionBankLifecycleStatus.REJECTED: set(),
    QuestionBankLifecycleStatus.SUPERSEDED: set(),
}


class QuestionBankService:
    """Question-bank identity, validation, lifecycle and bounded search."""

    validate = staticmethod(validate_item_payload)

    @staticmethod
    def diff(left: QuestionBankItemVersion, right: QuestionBankItemVersion) -> dict[str, bool]:
        return {
            "content": left.content != right.content,
            "answer_key": left.content.get("correct_option_ids")
            != right.content.get("correct_option_ids"),
            "taxonomy": (
                left.assessment_family,
                left.language,
                left.skill,
                left.task_type,
                left.response_mode,
            )
            != (
                right.assessment_family,
                right.language,
                right.skill,
                right.task_type,
                right.response_mode,
            ),
            "scoring": (left.scoring_policy, left.max_points, left.scoring_metadata)
            != (right.scoring_policy, right.max_points, right.scoring_metadata),
            "rights": (
                left.rights_status,
                left.rights_holder,
                left.licence_reference,
                left.permitted_use,
            )
            != (
                right.rights_status,
                right.rights_holder,
                right.licence_reference,
                right.permitted_use,
            ),
            "checksum": left.checksum != right.checksum,
        }

    @staticmethod
    def eligible_for_exam_studio(version: QuestionBankItemVersion) -> bool:
        return (
            version.status is QuestionBankLifecycleStatus.ACTIVE
            and version.rights_status in PUBLISHABLE_RIGHTS
        )

    @staticmethod
    def resolve_as_of(
        session: Session, context: TenantContext, item_id: uuid.UUID, as_of: datetime
    ) -> Optional[QuestionBankItemVersion]:
        AuthorizationService.require_permission(
            context, "question_bank.read", context.organisation_id
        )
        query = (
            select(QuestionBankItemVersion)
            .join(QuestionBankItem)
            .where(
                QuestionBankItemVersion.item_id == item_id,
                QuestionBankItemVersion.status.in_(
                    {
                        QuestionBankLifecycleStatus.ACTIVE,
                        QuestionBankLifecycleStatus.RETIRED,
                        QuestionBankLifecycleStatus.SUPERSEDED,
                    }
                ),
                (QuestionBankItemVersion.effective_from.is_(None))
                | (QuestionBankItemVersion.effective_from <= as_of),
                (QuestionBankItemVersion.effective_until.is_(None))
                | (QuestionBankItemVersion.effective_until > as_of),
                (QuestionBankItem.scope_type == "PLATFORM_SHARED")
                | (QuestionBankItem.organisation_id == context.organisation_id),
            )
            .order_by(QuestionBankItemVersion.effective_from.desc().nullslast())
        )
        return session.scalar(query.limit(1))

    @staticmethod
    def resolve_current(
        session: Session,
        context: TenantContext,
        item_id: uuid.UUID,
        now: Optional[datetime] = None,
    ) -> Optional[QuestionBankItemVersion]:
        return QuestionBankService.resolve_as_of(
            session, context, item_id, now or datetime.now(timezone.utc)
        )

    @staticmethod
    def eligible_bank(
        session: Session,
        context: TenantContext,
        *,
        standard_pack_version_id: uuid.UUID,
        task_spec_id: Optional[uuid.UUID] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[QuestionBankItemVersion]:
        """Future Exam Studio contract: exact pack version, never latest."""
        candidates = QuestionBankService.search(
            session,
            context,
            status=QuestionBankLifecycleStatus.ACTIVE,
            offset=offset,
            limit=min(limit, 100),
        )
        eligible: list[QuestionBankItemVersion] = []
        now = datetime.now(timezone.utc)
        for version in candidates:
            if version.effective_from and version.effective_from > now:
                continue
            if version.effective_until and version.effective_until <= now:
                continue
            if version.rights_status not in PUBLISHABLE_RIGHTS:
                continue
            bound = session.scalar(
                select(QuestionBankStandardPackBinding).where(
                    QuestionBankStandardPackBinding.item_version_id == version.id,
                    QuestionBankStandardPackBinding.standard_pack_version_id
                    == standard_pack_version_id,
                    QuestionBankStandardPackBinding.task_spec_id == task_spec_id,
                    QuestionBankStandardPackBinding.validation_status == "PASS",
                )
            )
            if bound is None:
                continue
            if (
                QuestionBankService.validate_pack_compatibility(
                    session, version, standard_pack_version_id, task_spec_id
                ).status
                != "PASS"
            ):
                continue
            report = QuestionBankService.evaluate_bound_rules(
                session, version, standard_pack_version_id
            )
            if report.overall_status.value in {"FAIL", "ERROR"}:
                continue
            eligible.append(version)
        return eligible[: min(limit, 100)]

    @staticmethod
    def duplicate_inspection(
        session: Session, context: TenantContext, version: QuestionBankItemVersion
    ) -> int:
        """Return only a count within authorized scope, never foreign IDs/content."""
        AuthorizationService.require_permission(
            context, "question_bank.read", context.organisation_id
        )
        query = (
            select(QuestionBankItemVersion)
            .join(QuestionBankItem)
            .where(
                QuestionBankItemVersion.checksum == version.checksum,
                (QuestionBankItem.scope_type == "PLATFORM_SHARED")
                | (QuestionBankItem.organisation_id == context.organisation_id),
            )
        )
        return len(list(session.scalars(query.limit(100))))

    @staticmethod
    def validate_pack_compatibility(
        session: Session,
        version: QuestionBankItemVersion,
        standard_pack_version_id: uuid.UUID,
        task_spec_id: Optional[uuid.UUID] = None,
    ) -> ValidationResult:
        pack_version = session.get(StandardPackVersion, standard_pack_version_id)
        if pack_version is None:
            raise ValueError("standard pack version not found")
        pack = pack_version.standard_pack
        levels = list(
            session.scalars(
                select(QuestionBankItemCefrLevel.cefr_level).where(
                    QuestionBankItemCefrLevel.item_version_id == version.id
                )
            )
        )
        issues: list[ValidationIssue] = []
        if pack.assessment_family is not version.assessment_family:
            issues.append(
                ValidationIssue(
                    "ITEM.PACK.ASSESSMENT_FAMILY_MISMATCH",
                    "FAIL",
                    "PACK_COMPATIBILITY",
                    "assessment_family",
                    "item and pack families differ",
                )
            )
        if pack.exam_family and version.exam_family and pack.exam_family != version.exam_family:
            issues.append(
                ValidationIssue(
                    "ITEM.PACK.EXAM_FAMILY_MISMATCH",
                    "FAIL",
                    "PACK_COMPATIBILITY",
                    "exam_family",
                    "item and pack exam families differ",
                )
            )
        if pack.language and version.language != pack.language:
            issues.append(
                ValidationIssue(
                    "ITEM.PACK.LANGUAGE_MISMATCH",
                    "FAIL",
                    "PACK_COMPATIBILITY",
                    "language",
                    "item language differs from pack language",
                )
            )
        if (
            pack_version.supported_levels
            and levels
            and not set(levels).intersection(pack_version.supported_levels)
        ):
            issues.append(
                ValidationIssue(
                    "ITEM.PACK.CEFR_MISMATCH",
                    "FAIL",
                    "PACK_COMPATIBILITY",
                    "cefr_levels",
                    "item CEFR levels are not supported by the pack",
                )
            )
        task = session.get(StandardPackTaskSpecification, task_spec_id) if task_spec_id else None
        if task_spec_id and (
            task is None or task.section.module.pack_version.id != pack_version.id
        ):
            issues.append(
                ValidationIssue(
                    "ITEM.PACK.TASK_SPEC_NOT_IN_VERSION",
                    "FAIL",
                    "PACK_COMPATIBILITY",
                    "task_spec_id",
                    "task specification is not owned by the bound pack version",
                )
            )
        if task is not None and task.section.module.pack_version.id == pack_version.id:
            if task.task_type != version.task_type.value:
                issues.append(
                    ValidationIssue(
                        "ITEM.PACK.TASK_TYPE_MISMATCH",
                        "FAIL",
                        "PACK_COMPATIBILITY",
                        "task_type",
                        "task type differs from task specification",
                    )
                )
            if task.response_mode is not version.response_mode:
                issues.append(
                    ValidationIssue(
                        "ITEM.PACK.RESPONSE_MODE_MISMATCH",
                        "FAIL",
                        "PACK_COMPATIBILITY",
                        "response_mode",
                        "response mode differs from task specification",
                    )
                )
            if task.media_requirement is not version.media_requirement:
                issues.append(
                    ValidationIssue(
                        "ITEM.PACK.MEDIA_MISMATCH",
                        "FAIL",
                        "PACK_COMPATIBILITY",
                        "media_requirement",
                        "media requirement differs from task specification",
                    )
                )
        return ValidationResult("FAIL" if issues else "PASS", tuple(issues), version.checksum)

    @staticmethod
    def evaluate_bound_rules(
        session: Session, version: QuestionBankItemVersion, standard_pack_version_id: uuid.UUID
    ) -> Any:
        pack_version = session.get(StandardPackVersion, standard_pack_version_id)
        if pack_version is None:
            raise ValueError("standard pack version not found")
        levels = list(
            session.scalars(
                select(QuestionBankItemCefrLevel.cefr_level).where(
                    QuestionBankItemCefrLevel.item_version_id == version.id
                )
            )
        )
        facts = item_rule_facts(version, levels)
        facts["pack"] = {
            "assessment_family": pack_version.standard_pack.assessment_family.value,
            "version_id": str(pack_version.id),
        }
        rules = [
            (binding.rule_version.definition, binding.rule_version)
            for binding in pack_version.rule_bindings
            if binding.required
        ]
        return RulesEngine().evaluate(
            rules,
            EvaluationContext(
                facts=facts,
                standard_pack_version_id=str(pack_version.id),
                standard_pack_checksum=pack_version.configuration_checksum,
            ),
        )

    @staticmethod
    def bind_pack(
        session: Session,
        context: TenantContext,
        version: QuestionBankItemVersion,
        standard_pack_version_id: uuid.UUID,
        task_spec_id: Optional[uuid.UUID] = None,
        validation_status: str = "PASS",
    ) -> QuestionBankStandardPackBinding:
        AuthorizationService.require_permission(
            context, "question_bank.manage", version.item.organisation_id
        )
        compatibility = QuestionBankService.validate_pack_compatibility(
            session, version, standard_pack_version_id, task_spec_id
        )
        if compatibility.status != "PASS" or validation_status != "PASS":
            raise ValueError("only passing pack compatibility may be bound")
        binding = QuestionBankStandardPackBinding(
            item_version_id=version.id,
            standard_pack_version_id=standard_pack_version_id,
            task_spec_id=task_spec_id,
            validation_status=validation_status,
            validated_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        session.add(binding)
        session.flush()
        AuditService.record(
            session,
            context,
            event_type="question_bank.item.pack_bound",
            entity_type="question_bank_item_version",
            entity_id=str(version.id),
            organisation_id=version.item.organisation_id,
            metadata={
                "standard_pack_version_id": str(standard_pack_version_id),
                "task_spec_id": str(task_spec_id) if task_spec_id else None,
            },
        )
        return binding

    @staticmethod
    def create_item(
        session: Session,
        context: TenantContext,
        *,
        code: str,
        scope_type: str,
        organisation_id: Optional[uuid.UUID] = None,
    ) -> QuestionBankItem:
        if scope_type == "PLATFORM_SHARED":
            if not context.platform_scope:
                raise ValueError("platform-shared items require platform authority")
            AuthorizationService.require_permission(context, "question_bank.manage")
            organisation_id = None
        else:
            if organisation_id != context.organisation_id:
                raise ValueError("organisation scope mismatch")
            AuthorizationService.require_permission(
                context, "question_bank.manage", organisation_id
            )
        item = QuestionBankItem(
            code=code,
            scope_type=scope_type,
            organisation_id=organisation_id,
            status=QuestionBankLifecycleStatus.DRAFT,
            created_by_user_id=context.user_id,
        )
        session.add(item)
        session.flush()
        AuditService.record(
            session,
            context,
            event_type="question_bank.item.created",
            entity_type="question_bank_item",
            entity_id=str(item.id),
            organisation_id=organisation_id,
            metadata={"code": code},
        )
        return item

    @staticmethod
    def create_version(
        session: Session,
        context: TenantContext,
        item: QuestionBankItem,
        *,
        version_label: str,
        assessment_family: AssessmentFamily,
        task_type: QuestionBankTaskType,
        response_mode: PackResponseMode,
        content: dict[str, Any],
        language: Optional[str] = None,
        exam_family: Optional[str] = None,
        cefr_levels: Optional[list[str]] = None,
        skill: Optional[QuestionBankSkill] = None,
        media_requirement: PackMediaRequirement = PackMediaRequirement.NONE,
        scoring_policy: QuestionBankScoringPolicy = QuestionBankScoringPolicy.OBJECTIVE_KEYED,
        max_points: Optional[Decimal] = None,
        scoring_metadata: Optional[dict[str, Any]] = None,
        rights_status: QuestionBankRightsStatus = QuestionBankRightsStatus.UNKNOWN,
        rights_holder: Optional[str] = None,
        licence_reference: Optional[str] = None,
        rubric_reference: Optional[str] = None,
        origin_type: QuestionBankOriginType = QuestionBankOriginType.HUMAN_AUTHORED,
        proposed_by_ai_run_id: Optional[uuid.UUID] = None,
        proposed_by_ai_step_id: Optional[uuid.UUID] = None,
    ) -> QuestionBankItemVersion:
        AuthorizationService.require_permission(
            context, "question_bank.manage", item.organisation_id
        )
        levels = cefr_levels or []
        result = validate_item_payload(
            assessment_family=assessment_family,
            exam_family=exam_family,
            language=language,
            cefr_levels=levels,
            skill=skill,
            task_type=task_type,
            response_mode=response_mode,
            media_requirement=media_requirement,
            content=content,
            scoring_policy=scoring_policy,
            max_points=max_points,
            scoring_metadata=scoring_metadata or {},
            rights_status=rights_status,
            licence_reference=licence_reference,
            rubric_reference=rubric_reference,
        )
        version = QuestionBankItemVersion(
            item_id=item.id,
            version_label=version_label,
            status=QuestionBankLifecycleStatus.DRAFT
            if origin_type is not QuestionBankOriginType.AI_PROPOSED
            else QuestionBankLifecycleStatus.VALIDATION_REQUIRED,
            assessment_family=assessment_family,
            exam_family=exam_family,
            language=language,
            skill=skill,
            task_type=task_type,
            response_mode=response_mode,
            media_requirement=media_requirement,
            content=content,
            scoring_policy=scoring_policy,
            max_points=max_points,
            scoring_metadata=scoring_metadata or {},
            accessibility={},
            rights_status=rights_status,
            rights_holder=rights_holder,
            licence_reference=licence_reference,
            rubric_reference=rubric_reference,
            origin_type=origin_type,
            proposed_by_ai_run_id=proposed_by_ai_run_id,
            proposed_by_ai_step_id=proposed_by_ai_step_id,
            created_at=datetime.now(timezone.utc),
            created_by_user_id=context.user_id,
            checksum=result.checksum or "",
        )
        session.add(version)
        session.flush()
        for level in levels:
            session.add(QuestionBankItemCefrLevel(item_version_id=version.id, cefr_level=level))
        session.flush()
        AuditService.record(
            session,
            context,
            event_type="question_bank.item.version.created",
            entity_type="question_bank_item_version",
            entity_id=str(version.id),
            organisation_id=item.organisation_id,
            metadata={"checksum": version.checksum, "origin": origin_type.value},
        )
        return version

    @staticmethod
    def transition(
        session: Session,
        context: TenantContext,
        version: QuestionBankItemVersion,
        target: QuestionBankLifecycleStatus,
        validation: Optional[ValidationResult] = None,
        checksum: Optional[str] = None,
    ) -> QuestionBankItemVersion:
        item = session.get(QuestionBankItem, version.item_id)
        if item is None:
            raise ValueError("question bank review permission denied")
        required_permission = (
            "question_bank.publish"
            if target is QuestionBankLifecycleStatus.PUBLISHED
            else "question_bank.review"
        )
        AuthorizationService.require_permission(context, required_permission, item.organisation_id)
        if target not in _TRANSITIONS.get(version.status, set()):
            raise ValueError(
                f"invalid item lifecycle transition: {version.status.value} -> {target.value}"
            )
        if target in {QuestionBankLifecycleStatus.APPROVED, QuestionBankLifecycleStatus.PUBLISHED}:
            if checksum and checksum != version.checksum:
                raise ValueError("stale item review checksum")
            if validation is None or validation.status != "PASS":
                raise ValueError("passing deterministic validation is required")
            if version.rights_status not in PUBLISHABLE_RIGHTS:
                raise ValueError("item rights are not publishable")
            if target is QuestionBankLifecycleStatus.APPROVED:
                review = session.scalar(
                    select(HumanReviewRequest).where(
                        HumanReviewRequest.entity_type == "QUESTION_BANK_ITEM_VERSION",
                        HumanReviewRequest.entity_id == version.id,
                        HumanReviewRequest.status.in_(
                            {HumanReviewStatus.PENDING, HumanReviewStatus.IN_REVIEW}
                        ),
                    )
                )
                if review is None or review.reviewed_checksum != version.checksum:
                    raise ValueError("a current HumanReviewRequest checksum is required")
            if target is QuestionBankLifecycleStatus.PUBLISHED:
                bindings = list(
                    session.scalars(
                        select(QuestionBankStandardPackBinding).where(
                            QuestionBankStandardPackBinding.item_version_id == version.id
                        )
                    )
                )
                if not bindings:
                    raise ValueError("published item requires an exact Standard Pack binding")
                for binding in bindings:
                    pack_result = QuestionBankService.validate_pack_compatibility(
                        session, version, binding.standard_pack_version_id, binding.task_spec_id
                    )
                    if pack_result.status != "PASS":
                        raise ValueError("pack compatibility blocks publication")
                    report = QuestionBankService.evaluate_bound_rules(
                        session, version, binding.standard_pack_version_id
                    )
                    if report.overall_status.value in {"FAIL", "ERROR"}:
                        raise ValueError("bound RulesEngine result blocks publication")
        if (
            target is QuestionBankLifecycleStatus.ACTIVE
            and version.status is not QuestionBankLifecycleStatus.PUBLISHED
        ):
            raise ValueError("only published items can activate")
        version.status = target
        now = datetime.now(timezone.utc)
        if target is QuestionBankLifecycleStatus.APPROVED:
            version.approved_at, version.approved_by_user_id = now, context.user_id
        elif target is QuestionBankLifecycleStatus.PUBLISHED:
            version.published_at, version.published_by_user_id = now, context.user_id
        elif target is QuestionBankLifecycleStatus.ACTIVE:
            version.activated_at = now
        elif target is QuestionBankLifecycleStatus.RETIRED:
            version.retired_at = now
        session.flush()
        AuditService.record(
            session,
            context,
            event_type=f"question_bank.item.{target.value.lower()}",
            entity_type="question_bank_item_version",
            entity_id=str(version.id),
            organisation_id=item.organisation_id,
            metadata={"checksum": version.checksum},
        )
        return version

    @staticmethod
    def request_review(
        session: Session,
        context: TenantContext,
        version: QuestionBankItemVersion,
    ) -> HumanReviewRequest:
        item = session.get(QuestionBankItem, version.item_id)
        if item is None:
            raise ValueError("item not found")
        AuthorizationService.require_permission(
            context, "question_bank.review", item.organisation_id
        )
        if version.status not in {
            QuestionBankLifecycleStatus.DRAFT,
            QuestionBankLifecycleStatus.VALIDATION_REQUIRED,
        }:
            raise ValueError("item version is not reviewable")
        version.status = QuestionBankLifecycleStatus.HUMAN_REVIEW
        review = HumanReviewRequest(
            review_type="QUESTION_BANK_ITEM",
            entity_type="QUESTION_BANK_ITEM_VERSION",
            entity_id=version.id,
            status=HumanReviewStatus.PENDING,
            requested_by=context.user_id,
            requested_at=datetime.now(timezone.utc),
            reviewed_checksum=version.checksum,
        )
        session.add(review)
        session.flush()
        AuditService.record(
            session,
            context,
            event_type="question_bank.item.review_requested",
            entity_type="question_bank_item_version",
            entity_id=str(version.id),
            organisation_id=item.organisation_id,
            metadata={"review_id": str(review.id), "checksum": version.checksum},
        )
        return review

    @staticmethod
    def decide_review(
        session: Session,
        context: TenantContext,
        review: HumanReviewRequest,
        decision: HumanReviewStatus,
    ) -> QuestionBankItemVersion:
        if review.entity_type != "QUESTION_BANK_ITEM_VERSION":
            raise ValueError("review is not a Question Bank review")
        version = session.get(QuestionBankItemVersion, review.entity_id)
        if version is None:
            raise ValueError("item version not found")
        item = session.get(QuestionBankItem, version.item_id)
        if item is None:
            raise ValueError("item not found")
        AuthorizationService.require_permission(
            context, "question_bank.review", item.organisation_id
        )
        if review.status not in {HumanReviewStatus.PENDING, HumanReviewStatus.IN_REVIEW}:
            raise ValueError("review already decided")
        if review.reviewed_checksum != version.checksum:
            raise ValueError("stale review artifact")
        if decision is HumanReviewStatus.APPROVED:
            raise ValueError(
                "approval requires a passing validation snapshot and explicit transition"
            )
        review.status = decision
        review.decided_by = context.user_id
        review.decided_at = datetime.now(timezone.utc)
        review.decision = decision.value
        version.status = (
            QuestionBankLifecycleStatus.REJECTED
            if decision is HumanReviewStatus.REJECTED
            else QuestionBankLifecycleStatus.DRAFT
        )
        session.flush()
        AuditService.record(
            session,
            context,
            event_type="question_bank.item.rejected"
            if decision is HumanReviewStatus.REJECTED
            else "question_bank.item.changes_requested",
            entity_type="question_bank_item_version",
            entity_id=str(version.id),
            organisation_id=item.organisation_id,
            metadata={"checksum": version.checksum},
        )
        return version

    @staticmethod
    def approve_review(
        session: Session,
        context: TenantContext,
        review: HumanReviewRequest,
        validation: ValidationResult,
    ) -> QuestionBankItemVersion:
        if review.entity_type != "QUESTION_BANK_ITEM_VERSION":
            raise ValueError("review is not a Question Bank review")
        version = session.get(QuestionBankItemVersion, review.entity_id)
        if version is None or review.status not in {
            HumanReviewStatus.PENDING,
            HumanReviewStatus.IN_REVIEW,
        }:
            raise ValueError("review is not approvable")
        if review.reviewed_checksum != version.checksum:
            raise ValueError("stale review artifact")
        approved = QuestionBankService.transition(
            session,
            context,
            version,
            QuestionBankLifecycleStatus.APPROVED,
            validation,
            review.reviewed_checksum,
        )
        review.status = HumanReviewStatus.APPROVED
        review.decided_by = context.user_id
        review.decided_at = datetime.now(timezone.utc)
        review.decision = HumanReviewStatus.APPROVED.value
        session.flush()
        return approved

    @staticmethod
    def search(
        session: Session,
        context: TenantContext,
        *,
        status: Optional[QuestionBankLifecycleStatus] = None,
        assessment_family: Optional[AssessmentFamily] = None,
        language: Optional[str] = None,
        cefr_level: Optional[str] = None,
        task_type: Optional[QuestionBankTaskType] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[QuestionBankItemVersion]:
        AuthorizationService.require_permission(
            context, "question_bank.read", context.organisation_id
        )
        query = (
            select(QuestionBankItemVersion)
            .join(QuestionBankItem)
            .where(
                (QuestionBankItem.scope_type == "PLATFORM_SHARED")
                | (QuestionBankItem.organisation_id == context.organisation_id)
            )
        )
        if status:
            query = query.where(QuestionBankItemVersion.status == status)
        if assessment_family:
            query = query.where(QuestionBankItemVersion.assessment_family == assessment_family)
        if language:
            query = query.where(QuestionBankItemVersion.language == language)
        if task_type:
            query = query.where(QuestionBankItemVersion.task_type == task_type)
        if cefr_level:
            query = query.join(QuestionBankItemCefrLevel).where(
                QuestionBankItemCefrLevel.cefr_level == cefr_level
            )
        return list(
            session.scalars(
                query.order_by(QuestionBankItemVersion.created_at.desc())
                .offset(offset)
                .limit(min(limit, 100))
            )
        )

    @staticmethod
    def export_safe(
        version: QuestionBankItemVersion,
        *,
        cefr_levels: list[str],
        pack_binding_ids: list[str],
        evidence_ids: list[str],
    ) -> dict[str, Any]:
        return {
            "item_id": str(version.item_id),
            "version_id": str(version.id),
            "version_label": version.version_label,
            "assessment_family": version.assessment_family.value,
            "language": version.language,
            "content": version.content,
            "scoring_policy": version.scoring_policy.value,
            "max_points": str(version.max_points) if version.max_points is not None else None,
            "rights_status": version.rights_status.value,
            "cefr_levels": cefr_levels,
            "pack_binding_ids": pack_binding_ids,
            "evidence_ids": evidence_ids,
            "checksum": version.checksum,
            "content_schema_version": version.content_schema_version,
        }
