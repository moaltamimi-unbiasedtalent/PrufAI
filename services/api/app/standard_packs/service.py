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
from app.models import (
    KnowledgeDocumentVersion,
    StandardPack,
    StandardPackSourceBinding,
    StandardPackVersion,
)
from app.models.enums import (
    AssessmentClassification,
    AssessmentFamily,
    KnowledgeVersionStatus,
    PackValidationStatus,
    StandardPackBindingType,
    StandardPackStatus,
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: str
    path: str
    message: str
    evidence_ref: Optional[str] = None


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
            if document is None:
                raise ValueError("document version not found")
            if (
                binding_type is StandardPackBindingType.NORMATIVE
                and document.status not in _ELIGIBLE_NORMATIVE_STATUSES
            ):
                raise ValueError("normative evidence must be approved or historically approved")
            if knowledge_source_id is not None and document.source_id != knowledge_source_id:
                raise ValueError("document version does not belong to the selected source")
            knowledge_source_id = document.source_id
        binding = StandardPackSourceBinding(
            standard_pack_version_id=version.id,
            knowledge_source_id=knowledge_source_id,
            document_version_id=document_version_id,
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
                elif (
                    binding.binding_type is StandardPackBindingType.NORMATIVE
                    and document.status not in _ELIGIBLE_NORMATIVE_STATUSES
                ):
                    issues.append(
                        ValidationIssue(
                            "PACK.NORMATIVE_SOURCE_NOT_APPROVED",
                            "FAIL",
                            f"source_bindings[{index}]",
                            "normative evidence must be approved or historically approved",
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
        if version.status in {
            StandardPackStatus.PUBLISHED,
            StandardPackStatus.ACTIVE,
            StandardPackStatus.RETIRED,
            StandardPackStatus.SUPERSEDED,
        }:
            raise ValueError("published Standard Pack versions are immutable; create a new version")

    _ensure_mutable = ensure_mutable

    @staticmethod
    def canonical(version: StandardPackVersion) -> dict[str, Any]:
        return {
            "schema_version": version.configuration_schema_version,
            "standard_pack_id": str(version.standard_pack_id),
            "version_label": version.version_label,
            "effective_from": version.effective_from.isoformat()
            if version.effective_from
            else None,
            "effective_until": version.effective_until.isoformat()
            if version.effective_until
            else None,
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

    @staticmethod
    def canonical_json(version: StandardPackVersion) -> str:
        return json.dumps(
            StandardPackService.canonical(version),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @staticmethod
    def checksum(version: StandardPackVersion) -> str:
        return hashlib.sha256(
            StandardPackService.canonical_json(version).encode("utf-8")
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
