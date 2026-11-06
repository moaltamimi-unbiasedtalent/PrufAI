from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import TenantContext
from app.dtz_policy import (
    DTZ_PACK_CODE,
    DTZ_PUBLICATION_BLOCKED,
    DTZ_PUBLICATION_BLOCKERS,
    DTZ_SCHEMA,
    is_dtz_family,
    is_dtz_pack,
)
from app.lid_policy import (
    LID_DRAFT_PUBLICATION_BLOCKED,
    LID_DRAFT_SCHEMA,
    LID_PRACTICE_SCHEMA,
    LID_PUBLICATION_BLOCKERS,
    is_lid_pack,
    reserved_lid_code,
)
from app.models import (
    KnowledgeDocumentVersion,
    KnowledgeSource,
    StandardPack,
    StandardPackSourceBinding,
    StandardPackVersion,
)
from app.models.enums import (
    AssessmentClassification,
    AssessmentFamily,
    KnowledgeScope,
    KnowledgeVersionStatus,
    PackValidationStatus,
    RuleEvaluationStatus,
    RuleStatus,
    StandardPackBindingType,
    StandardPackStatus,
)
from app.services.knowledge import source_legal_decision_valid, source_version_reference_checksum

B1_FOUNDATION_CODE = "PRUFAI_CEFR_DE_B1_FOUNDATION"
B1_FOUNDATION_SCHEMA = "s1-cefr-b1-v1"
B1_PUBLICATION_BLOCKED = "PACK_FOUNDATION_PUBLICATION_BLOCKED"
B1_PUBLICATION_REQUIREMENTS = (
    ("PACK_SOURCE_CONTENT_RIGHTS_REQUIRED", "Source metadata is not licensed content evidence."),
    ("PACK_INDEPENDENT_EXPERT_REVIEW_REQUIRED", "Independent expert validation is not recorded."),
    ("PACK_VALIDATED_TASK_RUBRIC_REQUIRED", "The draft task and rubric mapping is not validated."),
    ("PACK_GOVERNED_RULES_REQUIRED", "No approved assessment rules are bound to this draft."),
    (
        "PACK_APPROVED_VERSION_PROVENANCE_REQUIRED",
        "The version lacks approved assessment provenance.",
    ),
    ("PACK_HUMAN_PUBLICATION_AUTHORIZATION_REQUIRED", "Human publication authorization is absent."),
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: str
    path: str
    message: str
    evidence_ref: Optional[str] = None
    origin: str = "STRUCTURAL"


@dataclass(frozen=True)
class PackValidationResult:
    status: PackValidationStatus
    issues: tuple[ValidationIssue, ...]
    checksum: Optional[str]


_ELIGIBLE_NORMATIVE_STATUSES = {
    KnowledgeVersionStatus.APPROVED,
    KnowledgeVersionStatus.SUPERSEDED,
    KnowledgeVersionStatus.RETIRED,
}


def normative_source_is_eligible(document: KnowledgeDocumentVersion) -> bool:
    """S1 rights authorize bibliographic references or internal test evidence only."""
    return (
        document.source.scope is KnowledgeScope.PLATFORM_SHARED
        and document.content_mode is None
        and document.metadata_checksum is None
        and document.status in _ELIGIBLE_NORMATIVE_STATUSES
    )


_ALLOWED_TRANSITIONS: dict[StandardPackStatus, set[StandardPackStatus]] = {
    StandardPackStatus.DRAFT: {
        StandardPackStatus.VALIDATION_REQUIRED,
        StandardPackStatus.HUMAN_REVIEW,
        StandardPackStatus.REJECTED,
    },
    StandardPackStatus.VALIDATION_REQUIRED: {
        StandardPackStatus.HUMAN_REVIEW,
        StandardPackStatus.REJECTED,
    },
    StandardPackStatus.HUMAN_REVIEW: {StandardPackStatus.APPROVED, StandardPackStatus.REJECTED},
    StandardPackStatus.APPROVED: {StandardPackStatus.PUBLISHED, StandardPackStatus.REJECTED},
    StandardPackStatus.PUBLISHED: {StandardPackStatus.ACTIVE, StandardPackStatus.RETIRED},
    StandardPackStatus.ACTIVE: {StandardPackStatus.RETIRED, StandardPackStatus.SUPERSEDED},
    StandardPackStatus.RETIRED: set(),
    StandardPackStatus.REJECTED: set(),
    StandardPackStatus.SUPERSEDED: set(),
}


class StandardPackService:
    """Governed Standard Pack identity, versioning, validation, and export."""

    @staticmethod
    def create_pack(
        session: Session,
        *,
        code: str,
        name: str,
        assessment_family: AssessmentFamily,
        authority_id: Optional[uuid.UUID] = None,
        exam_family: Optional[str] = None,
        language: Optional[str] = None,
        description: Optional[str] = None,
        context: Optional[TenantContext] = None,
    ) -> StandardPack:
        if code.strip().upper() == DTZ_PACK_CODE or is_dtz_family(exam_family):
            raise ValueError("DTZ uses its exact-source draft workflow")
        if reserved_lid_code(code):
            raise ValueError("LiD uses its exact-source draft workflow")
        if code.strip().upper() == B1_FOUNDATION_CODE:
            raise ValueError("the B1 foundation uses its exact-source creation workflow")
        pack = StandardPack(
            code=code,
            name=name,
            assessment_family=assessment_family,
            authority_id=authority_id,
            exam_family=exam_family,
            language=language,
            description=description,
            status=StandardPackStatus.DRAFT,
        )
        session.add(pack)
        session.flush()
        StandardPackService._audit(session, context, "standard_pack.created", pack, {})
        return pack

    @staticmethod
    def create_version(
        session: Session,
        pack: StandardPack,
        *,
        version_label: str,
        created_by_user_id: Optional[uuid.UUID] = None,
        effective_from: Optional[datetime] = None,
        effective_until: Optional[datetime] = None,
        supersedes_version_id: Optional[uuid.UUID] = None,
        supported_levels: Optional[list[str]] = None,
        notes: Optional[str] = None,
        context: Optional[TenantContext] = None,
    ) -> StandardPackVersion:
        if pack.code.strip().upper() == DTZ_PACK_CODE or is_dtz_family(pack.exam_family):
            raise ValueError("DTZ uses its exact-source draft workflow")
        if reserved_lid_code(pack.code):
            raise ValueError("LiD uses its exact-source draft workflow")
        if pack.code == B1_FOUNDATION_CODE:
            raise ValueError("the B1 foundation uses its exact-source creation workflow")
        version = StandardPackVersion(
            standard_pack_id=pack.id,
            version_label=version_label,
            status=StandardPackStatus.DRAFT,
            effective_from=effective_from,
            effective_until=effective_until,
            created_at=datetime.now(timezone.utc),
            created_by_user_id=created_by_user_id,
            supersedes_version_id=supersedes_version_id,
            notes=notes,
            allowed_classifications=[
                AssessmentClassification.PRACTICE.value,
                AssessmentClassification.MOCK.value,
                AssessmentClassification.PLACEMENT_PROGRESS.value,
                AssessmentClassification.INSTITUTIONAL.value,
            ],
            supported_levels=supported_levels or [],
        )
        session.add(version)
        session.flush()
        StandardPackService._audit(
            session, context, "standard_pack.version.created", version, {"checksum": None}
        )
        return version

    @staticmethod
    def bind_source(
        session: Session,
        version: StandardPackVersion,
        *,
        binding_type: StandardPackBindingType,
        knowledge_source_id: Optional[uuid.UUID] = None,
        document_version_id: Optional[uuid.UUID] = None,
        purpose: Optional[str] = None,
        required: bool = True,
        notes: Optional[str] = None,
        context: Optional[TenantContext] = None,
    ) -> StandardPackSourceBinding:
        StandardPackService._ensure_mutable(version)
        if knowledge_source_id is None and document_version_id is None:
            raise ValueError("a source or exact document version is required")
        if binding_type is StandardPackBindingType.NORMATIVE and document_version_id is None:
            raise ValueError("normative bindings must pin an exact document version")
        if document_version_id is not None:
            document = session.get(KnowledgeDocumentVersion, document_version_id)
            if document is None or document.source.scope is not KnowledgeScope.PLATFORM_SHARED:
                raise ValueError("document version not found")
            if (
                binding_type is StandardPackBindingType.NORMATIVE
                and not normative_source_is_eligible(document)
            ):
                raise ValueError("source version is not eligible as normative evidence")
            if knowledge_source_id is not None and document.source_id != knowledge_source_id:
                raise ValueError("document version does not belong to the selected source")
            knowledge_source_id = document.source_id
            document_checksum = document.sha256
        else:
            source = (
                session.get(KnowledgeSource, knowledge_source_id) if knowledge_source_id else None
            )
            if source is None or source.scope is not KnowledgeScope.PLATFORM_SHARED:
                raise ValueError("source not found")
            document_checksum = None
        binding = StandardPackSourceBinding(
            standard_pack_version_id=version.id,
            knowledge_source_id=knowledge_source_id,
            document_version_id=document_version_id,
            document_checksum=document_checksum,
            binding_type=binding_type,
            purpose=purpose,
            required=required,
            notes=notes,
            created_at=datetime.now(timezone.utc),
        )
        session.add(binding)
        session.flush()
        StandardPackService._audit(
            session,
            context,
            "standard_pack.source_bound",
            version,
            {
                "binding_id": str(binding.id),
                "document_version_id": str(document_version_id) if document_version_id else None,
            },
        )
        return binding

    @staticmethod
    def validate(session: Session, version: StandardPackVersion) -> PackValidationResult:
        pack = session.scalar(
            select(StandardPack).where(StandardPack.id == version.standard_pack_id)
        )
        if pack is None:
            return PackValidationResult(
                PackValidationStatus.FAIL,
                (
                    ValidationIssue(
                        "PACK.MISSING_IDENTITY", "FAIL", "standard_pack_id", "pack not found"
                    ),
                ),
                None,
            )
        issues: list[ValidationIssue] = []
        if is_dtz_pack(version):
            for code, message in DTZ_PUBLICATION_BLOCKERS:
                issues.append(ValidationIssue(code, "FAIL", "publication", message))
        if is_lid_pack(version):
            for code, message in LID_PUBLICATION_BLOCKERS:
                issues.append(ValidationIssue(code, "FAIL", "publication", message))
        if (
            pack.code == B1_FOUNDATION_CODE
            or version.configuration_schema_version == B1_FOUNDATION_SCHEMA
        ):
            for code, message in B1_PUBLICATION_REQUIREMENTS:
                issues.append(ValidationIssue(code, "FAIL", "publication", message))
        if not pack.code or not pack.name:
            issues.append(
                ValidationIssue(
                    "PACK.MISSING_METADATA", "FAIL", "pack", "code and name are required"
                )
            )
        if (
            pack.assessment_family is AssessmentFamily.INTEGRATION_KNOWLEDGE
            and pack.language not in (None, "de")
        ):
            issues.append(
                ValidationIssue(
                    "PACK.INVALID_LANGUAGE",
                    "FAIL",
                    "supported_levels",
                    "LiD-style packs must use German or omit language",
                )
            )
        if (
            pack.assessment_family is AssessmentFamily.INTEGRATION_KNOWLEDGE
            and version.allowed_classifications is None
        ):
            issues.append(
                ValidationIssue(
                    "PACK.MISSING_CLASSIFICATION_POLICY",
                    "FAIL",
                    "allowed_classifications",
                    "classification policy is required",
                )
            )
        valid_levels = {"A1", "A2", "B1", "B2", "C1", "C2"}
        if (
            pack.assessment_family is AssessmentFamily.INTEGRATION_KNOWLEDGE
            and version.supported_levels
        ):
            issues.append(
                ValidationIssue(
                    "PACK.LID_CEFR_NOT_ALLOWED",
                    "FAIL",
                    "language",
                    "LiD must not be represented as CEFR",
                )
            )
        if pack.assessment_family is AssessmentFamily.LANGUAGE and any(
            level not in valid_levels for level in version.supported_levels
        ):
            issues.append(
                ValidationIssue(
                    "PACK.INVALID_CEFR_LEVEL",
                    "FAIL",
                    "supported_levels",
                    "language levels must be A1 through C2",
                )
            )
        if pack.assessment_family is AssessmentFamily.LANGUAGE and pack.language not in (
            None,
            "de",
        ):
            issues.append(
                ValidationIssue(
                    "PACK.INVALID_LANGUAGE",
                    "WARN",
                    "language",
                    "language is outside the Germany-first baseline",
                )
            )
        if AssessmentClassification.OFFICIAL_CERTIFICATION.value in (
            version.allowed_classifications or []
        ):
            issues.append(
                ValidationIssue(
                    "PACK.OFFICIAL_CLASSIFICATION_NOT_AUTHORIZED",
                    "FAIL",
                    "allowed_classifications",
                    "official certification requires explicit future authorization",
                )
            )
        bindings = list(version.source_bindings)
        normative = [
            item for item in bindings if item.binding_type is StandardPackBindingType.NORMATIVE
        ]
        if not normative:
            issues.append(
                ValidationIssue(
                    "PACK.MISSING_NORMATIVE_SOURCE",
                    "FAIL",
                    "source_bindings",
                    "at least one normative binding is required",
                )
            )
        for index, binding in enumerate(bindings):
            if (
                binding.binding_type is StandardPackBindingType.NORMATIVE
                and binding.document_version_id is None
            ):
                issues.append(
                    ValidationIssue(
                        "PACK.NORMATIVE_VERSION_NOT_PINNED",
                        "FAIL",
                        f"source_bindings[{index}]",
                        "normative evidence must pin a document version",
                    )
                )
            if binding.document_version_id is not None:
                document = session.get(KnowledgeDocumentVersion, binding.document_version_id)
                if document is None:
                    issues.append(
                        ValidationIssue(
                            "PACK.SOURCE_VERSION_NOT_FOUND",
                            "FAIL",
                            f"source_bindings[{index}]",
                            "document version not found",
                        )
                    )
                elif binding.source_metadata_checksum is not None and (
                    binding.knowledge_source_id != document.source_id
                    or binding.source_metadata_checksum
                    != source_version_reference_checksum(document)
                    or binding.document_checksum is not None
                    or document.content_mode != "METADATA_ONLY"
                ):
                    issues.append(
                        ValidationIssue(
                            "PACK.METADATA_SOURCE_PIN_MISMATCH",
                            "FAIL",
                            f"source_bindings[{index}]",
                            "The exact metadata source reference no longer matches.",
                            str(document.id),
                        )
                    )
                elif binding.source_metadata_checksum is not None and (
                    document.status is not KnowledgeVersionStatus.DRAFT
                    or not source_legal_decision_valid(document)
                ):
                    issues.append(
                        ValidationIssue(
                            "PACK.METADATA_SOURCE_REVIEW_UNAVAILABLE",
                            "FAIL",
                            f"source_bindings[{index}]",
                            "The exact bibliographic rights review is no longer current.",
                            str(document.id),
                        )
                    )
                if document is not None and (
                    binding.binding_type is StandardPackBindingType.NORMATIVE
                    and not normative_source_is_eligible(document)
                ):
                    issues.append(
                        ValidationIssue(
                            "PACK.NORMATIVE_SOURCE_NOT_APPROVED",
                            "FAIL",
                            f"source_bindings[{index}]",
                            "The exact source version is not eligible as normative evidence.",
                            str(document.id),
                        )
                    )
        seen_modules: set[str] = set()
        for module in version.modules:
            if module.code in seen_modules:
                issues.append(
                    ValidationIssue(
                        "PACK.DUPLICATE_MODULE_CODE",
                        "FAIL",
                        f"modules.{module.code}",
                        "module codes must be unique",
                    )
                )
            seen_modules.add(module.code)
            if module.sequence < 0:
                issues.append(
                    ValidationIssue(
                        "PACK.INVALID_SEQUENCE",
                        "FAIL",
                        f"modules.{module.code}.sequence",
                        "sequence must be non-negative",
                    )
                )
            for section in module.sections:
                if section.time_limit_seconds is not None and section.time_limit_seconds < 0:
                    issues.append(
                        ValidationIssue(
                            "PACK.INVALID_TIMING",
                            "FAIL",
                            f"sections.{section.code}.time_limit_seconds",
                            "timing must be non-negative",
                        )
                    )
                for task in section.tasks:
                    if task.min_items < 0 or task.max_items < task.min_items:
                        issues.append(
                            ValidationIssue(
                                "PACK.INVALID_TASK_ITEM_RANGE",
                                "FAIL",
                                f"tasks.{task.code}",
                                "min_items must not exceed max_items",
                            )
                        )
                    if task.rubric_required and not task.rubric_reference:
                        issues.append(
                            ValidationIssue(
                                "PACK.MISSING_RUBRIC_REFERENCE",
                                "WARN",
                                f"tasks.{task.code}",
                                "rubric reference is recommended when a rubric is required",
                            )
                        )
        if version.rule_bindings:
            from app.rules.service import RulesService

            for binding in version.rule_bindings:
                if binding.rule_version.status not in {
                    RuleStatus.APPROVED,
                    RuleStatus.PUBLISHED,
                    RuleStatus.ACTIVE,
                }:
                    issues.append(
                        ValidationIssue(
                            "PACK.RULE_VERSION_NOT_APPROVED",
                            "FAIL" if binding.required else "WARN",
                            f"rule_bindings.{binding.id}",
                            "bound rule version is not approved or published",
                            str(binding.rule_version_id),
                            "RULE",
                        )
                    )
            report = RulesService.evaluate_pack(session, version)
            for result in report.results:
                if result.status is RuleEvaluationStatus.PASS:
                    continue
                rule_binding = next(
                    (
                        item
                        for item in version.rule_bindings
                        if item.rule_version_id == uuid.UUID(result.rule_version_id)
                    ),
                    None,
                )
                required = rule_binding.required if rule_binding is not None else True
                severity = (
                    "FAIL"
                    if required
                    and (
                        result.status is RuleEvaluationStatus.ERROR or result.severity == "BLOCKING"
                    )
                    else "WARN"
                )
                issues.append(
                    ValidationIssue(
                        result.failure_code or "PACK.RULE_EVALUATION_ERROR",
                        severity,
                        result.path or f"rule_bindings.{result.rule_code}",
                        result.message,
                        result.rule_version_id,
                        "RULE",
                    )
                )
        checksum = StandardPackService.checksum(version)
        status = (
            PackValidationStatus.FAIL
            if any(item.severity == "FAIL" for item in issues)
            else (PackValidationStatus.WARN if issues else PackValidationStatus.PASS)
        )
        if status is not PackValidationStatus.FAIL:
            version.configuration_checksum = checksum
        return PackValidationResult(status, tuple(issues), checksum)

    @staticmethod
    def transition(
        session: Session,
        version: StandardPackVersion,
        target: StandardPackStatus,
        *,
        actor_id: Optional[uuid.UUID] = None,
        context: Optional[TenantContext] = None,
    ) -> PackValidationResult:
        if is_dtz_pack(version):
            raise ValueError(DTZ_PUBLICATION_BLOCKED)
        if is_lid_pack(version):
            raise ValueError(LID_DRAFT_PUBLICATION_BLOCKED)
        if (
            version.configuration_schema_version == B1_FOUNDATION_SCHEMA
            or version.standard_pack.code == B1_FOUNDATION_CODE
        ) and target in {
            StandardPackStatus.APPROVED,
            StandardPackStatus.PUBLISHED,
            StandardPackStatus.ACTIVE,
        }:
            raise ValueError(B1_PUBLICATION_BLOCKED)
        current = version.status
        if target not in _ALLOWED_TRANSITIONS.get(current, set()):
            raise ValueError(f"invalid Standard Pack transition: {current.value} -> {target.value}")
        result = StandardPackService.validate(session, version)
        if (
            target
            in {
                StandardPackStatus.HUMAN_REVIEW,
                StandardPackStatus.APPROVED,
                StandardPackStatus.PUBLISHED,
            }
            and result.status is PackValidationStatus.FAIL
        ):
            raise ValueError("pack validation failed; transition is blocked")
        now = datetime.now(timezone.utc)
        version.status = target
        if target is StandardPackStatus.HUMAN_REVIEW:
            version.reviewed_at = now
            version.reviewed_by_user_id = actor_id
        elif target is StandardPackStatus.APPROVED:
            version.approved_at = now
            version.approved_by_user_id = actor_id
        elif target is StandardPackStatus.PUBLISHED:
            version.published_at = now
            version.published_by_user_id = actor_id
        session.flush()
        event_by_target = {
            StandardPackStatus.VALIDATION_REQUIRED: "standard_pack.validation.completed",
            StandardPackStatus.HUMAN_REVIEW: "standard_pack.review_requested",
            StandardPackStatus.APPROVED: "standard_pack.approved",
            StandardPackStatus.PUBLISHED: "standard_pack.published",
            StandardPackStatus.ACTIVE: "standard_pack.activated",
            StandardPackStatus.RETIRED: "standard_pack.retired",
            StandardPackStatus.REJECTED: "standard_pack.rejected",
            StandardPackStatus.SUPERSEDED: "standard_pack.version.superseded",
        }
        StandardPackService._audit(
            session,
            context,
            event_by_target[target],
            version,
            {"checksum": version.configuration_checksum},
        )
        return result

    @staticmethod
    def ensure_mutable(version: StandardPackVersion) -> None:
        if is_dtz_pack(version):
            raise ValueError("the DTZ draft is sealed to its exact evidence")
        if version.configuration_schema_version in {LID_DRAFT_SCHEMA, LID_PRACTICE_SCHEMA}:
            raise ValueError("the LiD version is sealed to its exact evidence")
        if version.configuration_schema_version == B1_FOUNDATION_SCHEMA:
            raise ValueError("the B1 foundation draft is sealed to its exact source mapping")
        if version.status in {
            StandardPackStatus.PUBLISHED,
            StandardPackStatus.ACTIVE,
            StandardPackStatus.RETIRED,
            StandardPackStatus.SUPERSEDED,
        }:
            raise ValueError("published Standard Pack versions are immutable; create a new version")

    _ensure_mutable = ensure_mutable

    @staticmethod
    def canonical(
        version: StandardPackVersion,
        *,
        dtz_manifest_checksum: str | None = None,
        lid_manifest_checksum: str | None = None,
    ) -> dict[str, Any]:
        def effective_date(value: datetime | None) -> str | None:
            if value is None:
                return None
            if version.configuration_schema_version in {
                B1_FOUNDATION_SCHEMA,
                DTZ_SCHEMA,
                LID_DRAFT_SCHEMA,
                LID_PRACTICE_SCHEMA,
            }:
                value = (
                    value.replace(tzinfo=timezone.utc)
                    if value.tzinfo is None
                    else value.astimezone(timezone.utc)
                )
            return value.isoformat()

        payload: dict[str, Any] = {
            "schema_version": version.configuration_schema_version,
            "standard_pack_id": str(version.standard_pack_id),
            "version_label": version.version_label,
            "effective_from": effective_date(version.effective_from),
            "effective_until": effective_date(version.effective_until),
            "supersedes_version_id": str(version.supersedes_version_id)
            if version.supersedes_version_id
            else None,
            "allowed_classifications": sorted(version.allowed_classifications or []),
            "supported_levels": sorted(version.supported_levels or []),
            "scoring_configuration_ref": version.scoring_configuration_ref,
            "result_configuration_ref": version.result_configuration_ref,
            "rubric_set_ref": version.rubric_set_ref,
            "source_bindings": [
                {
                    "id": str(item.id),
                    "source_id": str(item.knowledge_source_id)
                    if item.knowledge_source_id
                    else None,
                    "document_version_id": str(item.document_version_id)
                    if item.document_version_id
                    else None,
                    "document_checksum": item.document_checksum,
                    **(
                        {"source_metadata_checksum": item.source_metadata_checksum}
                        if item.source_metadata_checksum is not None
                        else {}
                    ),
                    "binding_type": item.binding_type.value,
                    "purpose": item.purpose,
                    "required": item.required,
                }
                for item in sorted(version.source_bindings, key=lambda value: str(value.id))
            ],
            "modules": [
                {
                    "id": str(module.id),
                    "code": module.code,
                    "name": module.name,
                    "skill": module.skill,
                    "sequence": module.sequence,
                    "required": module.required,
                    "time_limit_seconds": module.time_limit_seconds,
                    "preparation_time_seconds": module.preparation_time_seconds,
                    "navigation_mode": module.navigation_mode.value,
                    "sections": [
                        {
                            "id": str(section.id),
                            "code": section.code,
                            "name": section.name,
                            "sequence": section.sequence,
                            "instructions_ref": section.instructions_ref,
                            "time_limit_seconds": section.time_limit_seconds,
                            "navigation_mode": section.navigation_mode.value,
                            "tasks": [
                                {
                                    "id": str(task.id),
                                    "code": task.code,
                                    "name": task.name,
                                    "sequence": task.sequence,
                                    "task_type": task.task_type,
                                    "min_items": task.min_items,
                                    "max_items": task.max_items,
                                    "required_items": task.required_items,
                                    "points_per_item": task.points_per_item,
                                    "response_mode": task.response_mode.value,
                                    "media_requirement": task.media_requirement.value,
                                    "generation_allowed": task.generation_allowed,
                                    "selection_allowed": task.selection_allowed,
                                    "rubric_required": task.rubric_required,
                                    "rubric_reference": task.rubric_reference,
                                    "metadata": task.metadata_json,
                                }
                                for task in sorted(section.tasks, key=lambda value: value.sequence)
                            ],
                        }
                        for section in sorted(module.sections, key=lambda value: value.sequence)
                    ],
                }
                for module in sorted(version.modules, key=lambda value: value.sequence)
            ],
        }

        if version.configuration_schema_version == DTZ_SCHEMA:
            provenance = version.dtz_provenance if dtz_manifest_checksum is None else None
            payload["dtz_manifest_checksum"] = (
                provenance.manifest_checksum if provenance else dtz_manifest_checksum
            )
            payload["dtz_identity"] = {
                "code": version.standard_pack.code,
                "exam_family": version.standard_pack.exam_family,
                "assessment_family": version.standard_pack.assessment_family.value,
                "language": version.standard_pack.language,
                "authority_id": str(version.standard_pack.authority_id),
            }
        if version.configuration_schema_version in {LID_DRAFT_SCHEMA, LID_PRACTICE_SCHEMA}:
            payload["lid_identity"] = {
                "code": version.standard_pack.code,
                "exam_family": version.standard_pack.exam_family,
                "assessment_family": version.standard_pack.assessment_family.value,
                "language": version.standard_pack.language,
                "authority_id": str(version.standard_pack.authority_id),
            }
        if version.configuration_schema_version == LID_DRAFT_SCHEMA:
            lid_provenance = version.lid_provenance if lid_manifest_checksum is None else None
            payload["lid_manifest_checksum"] = (
                lid_provenance.manifest_checksum if lid_provenance else lid_manifest_checksum
            )
        return payload

    @staticmethod
    def canonical_json(
        version: StandardPackVersion,
        *,
        dtz_manifest_checksum: str | None = None,
        lid_manifest_checksum: str | None = None,
    ) -> str:
        return json.dumps(
            StandardPackService.canonical(
                version,
                dtz_manifest_checksum=dtz_manifest_checksum,
                lid_manifest_checksum=lid_manifest_checksum,
            ),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @staticmethod
    def checksum(
        version: StandardPackVersion,
        *,
        dtz_manifest_checksum: str | None = None,
        lid_manifest_checksum: str | None = None,
    ) -> str:
        return hashlib.sha256(
            StandardPackService.canonical_json(
                version,
                dtz_manifest_checksum=dtz_manifest_checksum,
                lid_manifest_checksum=lid_manifest_checksum,
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def export(version: StandardPackVersion) -> dict[str, Any]:
        payload = StandardPackService.canonical(version)
        payload["pack_checksum"] = version.configuration_checksum or StandardPackService.checksum(
            version
        )
        payload["status"] = version.status.value
        return payload

    @staticmethod
    def import_payload(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("pack import must be a JSON object")
        serialized = json.dumps(payload, sort_keys=True)
        if any(
            token in serialized.lower()
            for token in ("__import__", "eval(", "exec(", "javascript:", "<script")
        ):
            raise ValueError("executable content is prohibited in pack configuration")
        required = {"schema_version", "version_label", "modules", "source_bindings"}
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError(f"missing pack fields: {', '.join(missing)}")
        return payload

    @staticmethod
    def diff(left: StandardPackVersion, right: StandardPackVersion) -> dict[str, Any]:
        first = StandardPackService.canonical(left)
        second = StandardPackService.canonical(right)
        return {
            "metadata_changed": {
                key: (first[key], second[key])
                for key in (
                    "schema_version",
                    "version_label",
                    "effective_from",
                    "effective_until",
                    "allowed_classifications",
                )
                if first[key] != second[key]
            },
            "source_bindings_changed": first["source_bindings"] != second["source_bindings"],
            "modules_changed": first["modules"] != second["modules"],
            "timing_changed": StandardPackService._timing(first)
            != StandardPackService._timing(second),
            "classification_policy_changed": first["allowed_classifications"]
            != second["allowed_classifications"],
        }

    @staticmethod
    def _timing(payload: dict[str, Any]) -> list[Any]:
        return [
            (
                module["code"],
                module["time_limit_seconds"],
                section["code"],
                section["time_limit_seconds"],
            )
            for module in payload["modules"]
            for section in module["sections"]
        ]

    @staticmethod
    def resolve(
        session: Session, pack_id: uuid.UUID, *, as_of: Optional[datetime] = None
    ) -> Optional[StandardPackVersion]:
        query = select(StandardPackVersion).where(StandardPackVersion.standard_pack_id == pack_id)
        if as_of is None:
            query = query.where(StandardPackVersion.status == StandardPackStatus.ACTIVE).order_by(
                StandardPackVersion.effective_from.desc()
            )
        else:
            query = (
                query.where(StandardPackVersion.effective_from <= as_of)
                .where(
                    (StandardPackVersion.effective_until.is_(None))
                    | (StandardPackVersion.effective_until > as_of)
                )
                .where(
                    StandardPackVersion.status.in_(
                        [
                            StandardPackStatus.ACTIVE,
                            StandardPackStatus.PUBLISHED,
                            StandardPackStatus.RETIRED,
                            StandardPackStatus.SUPERSEDED,
                        ]
                    )
                )
                .order_by(StandardPackVersion.effective_from.desc())
            )
        return session.scalars(query.limit(1)).first()

    @staticmethod
    def _audit(
        session: Session,
        context: Optional[TenantContext],
        event: str,
        entity: Any,
        metadata: dict[str, Any],
    ) -> None:
        if context is None:
            return
        AuditService.record(
            session,
            context,
            event_type=event,
            entity_type="standard_pack",
            entity_id=str(entity.id),
            entity_version=getattr(entity, "version_label", None),
            metadata=metadata,
        )
