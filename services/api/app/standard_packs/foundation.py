"""A sealed B1 practice alignment draft, with bibliographic evidence only."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization import TenantContext
from app.knowledge.catalogue import ENTRIES, VERIFIED_ON
from app.models import (
    KnowledgeDocumentVersion,
    StandardPack,
    StandardPackModule,
    StandardPackSection,
    StandardPackSourceBinding,
    StandardPackTaskSpecification,
    StandardPackVersion,
)
from app.models.enums import (
    AssessmentClassification,
    AssessmentFamily,
    KnowledgeScope,
    KnowledgeVersionStatus,
    PackMediaRequirement,
    PackNavigationMode,
    PackResponseMode,
    StandardPackBindingType,
    StandardPackStatus,
)
from app.services.knowledge import source_legal_decision_valid, source_version_reference_checksum
from app.standard_packs.service import B1_FOUNDATION_CODE, B1_FOUNDATION_SCHEMA, StandardPackService

_CEFR_REFERENCE = ENTRIES["cefr-companion-2020"]
_DRAFT_LABEL = "S1 B1 practice foundation draft 1"
_NOTICE = "PrüfAI alignment draft · Synthetic practice content · Not official provider material"
_CONSTRUCTS = (
    (
        "READING",
        "Written reception",
        "Find practical information in an everyday written message.",
        "Der Fahrradtreff beginnt am Samstag um zehn Uhr am Bahnhof. "
        "Bei starkem Regen treffen wir uns am Sonntag. Wo treffen sich die Teilnehmenden?",
        PackResponseMode.TEXT,
        PackMediaRequirement.NONE,
    ),
    (
        "LISTENING",
        "Spoken reception",
        "Identify a plan and a change of plan in a short spoken message.",
        "Original script for future recording: Hallo, der Kochkurs beginnt heute später. "
        "Komm bitte erst um sieben. Wann beginnt der Kurs?",
        PackResponseMode.TEXT,
        PackMediaRequirement.AUDIO,
    ),
    (
        "WRITING",
        "Written interaction",
        "Communicate a practical request and its reason in writing.",
        "Schreiben Sie Ihrer Lerngruppe: Sie können morgen nicht kommen. "
        "Nennen Sie einen Grund und schlagen Sie einen neuen Termin vor.",
        PackResponseMode.LONG_TEXT,
        PackMediaRequirement.NONE,
    ),
    (
        "SPEAKING",
        "Spoken production",
        "Describe a familiar activity and explain a preference.",
        "Erzählen Sie von einem Ort, den Sie gern besuchen. "
        "Beschreiben Sie, was Sie dort machen und warum Sie den Ort mögen.",
        PackResponseMode.AUDIO,
        PackMediaRequirement.NONE,
    ),
)


def is_b1_foundation(version: StandardPackVersion) -> bool:
    return version.configuration_schema_version == B1_FOUNDATION_SCHEMA


def _source(
    session: Session, identifier: uuid.UUID, expected_metadata_checksum: str
) -> KnowledgeDocumentVersion:
    version = session.get(KnowledgeDocumentVersion, identifier, with_for_update=True)
    if version is None:
        raise ValueError("PACK_FOUNDATION_EXACT_SOURCE_REQUIRED")
    source = version.source
    authority = source.authority
    legal = version.legal_metadata
    verified_at = version.retrieved_at
    if verified_at is not None and verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)
    if (
        authority.code != _CEFR_REFERENCE.authority_code
        or authority.name != _CEFR_REFERENCE.publisher
        or authority.authority_type != _CEFR_REFERENCE.authority_type
        or authority.status != "ACTIVE"
        or source.source_code != _CEFR_REFERENCE.source_code
        or source.title != _CEFR_REFERENCE.title
        or source.canonical_url != _CEFR_REFERENCE.url
        or source.assessment_family != _CEFR_REFERENCE.family
        or source.document_type != _CEFR_REFERENCE.document_type
        or source.scope is not KnowledgeScope.PLATFORM_SHARED
        or source.status != "ACTIVE"
        or source.language != _CEFR_REFERENCE.language
        or source.exam_family is not None
        or source.provider_code is not None
        or source.cefr_level is not None
        or source.skill is not None
        or version.version_label != _CEFR_REFERENCE.version_label
        or version.source_url != (_CEFR_REFERENCE.edition_url or _CEFR_REFERENCE.url)
        or version.publication_date is not None
        or version.effective_from is not None
        or version.effective_until is not None
        or version.supersedes_document_version_id is not None
        or verified_at != VERIFIED_ON
        or version.content_mode != "METADATA_ONLY"
        or version.status is not KnowledgeVersionStatus.DRAFT
        or version.sha256 is not None
        or version.storage_key is not None
        or legal is None
        or legal.ingestion_allowed is not False
        or legal.usage_rights != "BIBLIOGRAPHIC_REFERENCE_ONLY"
        or source_version_reference_checksum(version) != expected_metadata_checksum
        or not source_legal_decision_valid(version)
    ):
        raise ValueError("PACK_FOUNDATION_EXACT_REVIEWED_METADATA_SOURCE_REQUIRED")
    return version


def create_b1_foundation(
    session: Session,
    context: TenantContext,
    *,
    source_version_id: uuid.UUID,
    expected_metadata_checksum: str,
) -> StandardPackVersion:
    document = _source(session, source_version_id, expected_metadata_checksum)
    pack = session.scalar(select(StandardPack).where(StandardPack.code == B1_FOUNDATION_CODE))
    if pack is not None:
        existing = session.scalar(
            select(StandardPackVersion).where(
                StandardPackVersion.standard_pack_id == pack.id,
                StandardPackVersion.version_label == _DRAFT_LABEL,
            )
        )
        if (
            existing is None
            or not is_b1_foundation(existing)
            or existing.status is not StandardPackStatus.DRAFT
            or existing.configuration_checksum != StandardPackService.checksum(existing)
            or len(existing.source_bindings) != 1
            or existing.source_bindings[0].document_version_id != document.id
            or existing.source_bindings[0].source_metadata_checksum != expected_metadata_checksum
        ):
            raise ValueError("PACK_FOUNDATION_DRAFT_PIN_CONFLICT")
        return existing

    now = datetime.now(timezone.utc)
    pack = StandardPack(
        code=B1_FOUNDATION_CODE,
        name="CEFR / GER B1 · German practice foundation",
        description=_NOTICE,
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        status=StandardPackStatus.DRAFT,
    )
    version = StandardPackVersion(
        standard_pack=pack,
        version_label=_DRAFT_LABEL,
        status=StandardPackStatus.DRAFT,
        configuration_schema_version=B1_FOUNDATION_SCHEMA,
        effective_from=now,
        created_at=now,
        created_by_user_id=context.user_id,
        supported_levels=["B1"],
        allowed_classifications=[AssessmentClassification.PRACTICE.value],
        notes=_NOTICE,
    )
    binding = StandardPackSourceBinding(
        pack_version=version,
        knowledge_source_id=document.source_id,
        document_version_id=document.id,
        source_metadata_checksum=expected_metadata_checksum,
        binding_type=StandardPackBindingType.SUPPORTING,
        purpose="Bibliographic CEFR reference. Provider alignment and content rights unverified.",
        required=True,
        created_at=now,
    )
    session.add_all((pack, version, binding))
    session.flush()
    for sequence, (skill, title, construct, example, mode, media) in enumerate(_CONSTRUCTS, 1):
        module = StandardPackModule(
            pack_version=version,
            code=skill,
            name=title,
            skill=skill,
            sequence=sequence,
            navigation_mode=PackNavigationMode.LINEAR,
            created_at=now,
        )
        section = StandardPackSection(
            module=module,
            code="PRACTICE_EXAMPLE",
            name="Original practice example",
            sequence=1,
            navigation_mode=PackNavigationMode.LINEAR,
            created_at=now,
        )
        session.add(
            StandardPackTaskSpecification(
                section=section,
                code=f"{skill}_EXAMPLE",
                name=f"{title}: draft illustration",
                sequence=1,
                task_type="PRACTICE_EXAMPLE",
                response_mode=mode,
                media_requirement=media,
                rubric_required=skill in {"WRITING", "SPEAKING"},
                metadata_json={
                    "construct": construct,
                    "synthetic_example": example,
                    "source_binding_id": str(binding.id),
                    "source_version_id": str(document.id),
                    "source_metadata_checksum": expected_metadata_checksum,
                    "review_status": "EXPERT_VALIDATION_NOT_RECORDED",
                    "notice": _NOTICE,
                },
                created_at=now,
            )
        )
    session.flush()
    session.expire(version, ("source_bindings", "modules", "effective_from", "effective_until"))
    version.configuration_checksum = StandardPackService.checksum(version)
    StandardPackService._audit(
        session,
        context,
        "standard_pack.cefr_b1_draft_created",
        version,
        {
            "checksum": version.configuration_checksum,
            "source_version_id": str(document.id),
            "source_metadata_checksum": expected_metadata_checksum,
        },
    )
    session.flush()
    return version


def b1_foundation_detail(session: Session, version: StandardPackVersion) -> dict[str, Any]:
    result = StandardPackService.validate(session, version)
    bindings: list[dict[str, Any]] = []
    for item in version.source_bindings:
        document = (
            session.get(KnowledgeDocumentVersion, item.document_version_id)
            if item.document_version_id
            else None
        )
        bindings.append(
            {
                "id": str(item.id),
                "source_id": str(item.knowledge_source_id),
                "document_version_id": str(item.document_version_id),
                "source_metadata_checksum": item.source_metadata_checksum,
                "document_checksum": item.document_checksum,
                "binding_type": item.binding_type.value,
                "purpose": item.purpose,
                "content_mode": document.content_mode if document else None,
                "source_status": document.status.value if document else None,
                "publisher": document.source.authority.name if document else None,
                "version_label": document.version_label if document else None,
                "source_url": document.source_url if document else None,
                "legal_review_status": document.legal_metadata.legal_review_status.value
                if document and document.legal_metadata
                else None,
            }
        )
    return {
        "foundation": True,
        "notice": _NOTICE,
        "assessment_family": version.standard_pack.assessment_family.value,
        "language": version.standard_pack.language,
        "source_bindings": bindings,
        "constructs": [
            {"skill": module.skill, "task_code": task.code, **task.metadata_json}
            for module in version.modules
            for section in module.sections
            for task in section.tasks
        ],
        "validation_status": result.status.value,
        "publication_blockers": [issue.code for issue in result.issues if issue.severity == "FAIL"],
    }
