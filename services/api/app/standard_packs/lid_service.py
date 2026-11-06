"""Build and verify a sealed LiD draft on the existing Standard Pack model."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import TenantContext
from app.lid_policy import (
    LID_DRAFT_PACK_CODE,
    LID_DRAFT_SCHEMA,
    LID_EXAM_FAMILY,
    LID_NOTICE,
    LID_PUBLICATION_BLOCKERS,
    LID_STATES,
    LID_STRUCTURE,
)
from app.models import StandardPack, StandardPackVersion
from app.models.enums import (
    AssessmentFamily,
    PackNavigationMode,
    PackResponseMode,
    StandardPackBindingType,
    StandardPackStatus,
)
from app.models.standard_pack import (
    LidBlueprintProvenance,
    StandardPackModule,
    StandardPackSection,
    StandardPackSourceBinding,
    StandardPackTaskSpecification,
)
from app.standard_packs.lid_rules import create_lid_rules, evaluate_lid_preview, lid_rules_current
from app.standard_packs.lid_sources import lid_claims, lid_source_snapshots, resolve_lid_sources
from app.standard_packs.service import StandardPackService
from app.writing_assessment.service import canonical_checksum

LID_BLUEPRINT_VERSION = "S3 LiD practice blueprint 1"
LID_SELECTION_VERSION = "s3-lid-sha256-item-order-v1"
_LID_DRAFT_LOCK = 6010039
SourcePins = dict[str, tuple[uuid.UUID, str]]

_QUESTION_POOLS = [
    {
        "scope": "GENERAL_DE",
        "scope_label": "Federal questions",
        "eligible_catalogue_count": 300,
        "form_items": 30,
        "jurisdiction": "DE",
        "topic_categories": [
            "POLITICS_IN_DEMOCRACY",
            "HISTORY_AND_RESPONSIBILITY",
            "PEOPLE_AND_SOCIETY",
        ],
    },
    {
        "scope": "BUNDESLAND_SPECIFIC",
        "scope_label": "Questions for one selected Bundesland",
        "eligible_catalogue_count": 10,
        "form_items": 3,
        "jurisdiction": "EXPLICIT_BUNDESLAND",
        "topic_categories": ["STATE_SPECIFIC_KNOWLEDGE"],
    },
]


def build_lid_structure(version: StandardPackVersion, now: datetime) -> None:
    """Use the normal one-point objective tasks; both sections share the server timer."""
    module = StandardPackModule(
        code="INTEGRATION_KNOWLEDGE",
        name="Integration knowledge",
        sequence=1,
        required=True,
        time_limit_seconds=LID_STRUCTURE["time_limit_seconds"],
        navigation_mode=PackNavigationMode.FREE_WITHIN_MODULE,
        created_at=now,
    )
    version.modules.append(module)
    for sequence, pool in enumerate(_QUESTION_POOLS, start=1):
        scope, label, count = pool["scope"], pool["scope_label"], pool["form_items"]
        section = StandardPackSection(
            code=scope,
            name=label,
            sequence=sequence,
            navigation_mode=PackNavigationMode.FREE_WITHIN_MODULE,
            created_at=now,
        )
        module.sections.append(section)
        section.tasks.append(
            StandardPackTaskSpecification(
                code=scope,
                name=label,
                sequence=1,
                task_type="MULTIPLE_CHOICE",
                min_items=count,
                max_items=count,
                required_items=count,
                points_per_item=1,
                response_mode=PackResponseMode.SELECT_ONE,
                generation_allowed=False,
                selection_allowed=True,
                metadata_json={
                    "pool_scope": scope,
                    "options_per_item": LID_STRUCTURE["options_per_item"],
                    "correct_options_per_item": LID_STRUCTURE["correct_options_per_item"],
                    "cefr_level": None,
                },
                created_at=now,
            )
        )


def lid_version(session: Session, version_id: uuid.UUID) -> StandardPackVersion | None:
    return session.scalar(
        select(StandardPackVersion)
        .join(LidBlueprintProvenance)
        .where(
            StandardPackVersion.id == version_id,
            StandardPackVersion.configuration_schema_version == LID_DRAFT_SCHEMA,
        )
    )


def _pins(sources: dict[str, dict[str, Any]]) -> SourcePins:
    return {
        key: (uuid.UUID(source["document_version_id"]), source["source_metadata_checksum"])
        for key, source in sources.items()
    }


def lid_draft_integrity(session: Session, version: StandardPackVersion) -> str | None:
    provenance = version.lid_provenance
    pack = version.standard_pack
    if (
        provenance is None
        or pack.code != LID_DRAFT_PACK_CODE
        or pack.status is not StandardPackStatus.DRAFT
        or pack.assessment_family is not AssessmentFamily.INTEGRATION_KNOWLEDGE
        or pack.exam_family != LID_EXAM_FAMILY
        or pack.language != "de"
        or version.status is not StandardPackStatus.DRAFT
        or version.configuration_schema_version != LID_DRAFT_SCHEMA
        or version.supported_levels
        or version.allowed_classifications != ["PRACTICE"]
        or version.rule_bindings
        or canonical_checksum(provenance.manifest) != provenance.manifest_checksum
        or StandardPackService.checksum(version) != version.configuration_checksum
    ):
        return "LID_DRAFT_PROVENANCE_INVALID"
    try:
        sources = provenance.manifest["sources"]
        documents = resolve_lid_sources(session, _pins(sources))
        if lid_source_snapshots(documents) != sources:
            return "LID_SOURCE_RIGHTS_PIN_MISMATCH"
        bindings = {binding.document_version_id: binding for binding in version.source_bindings}
        if len(bindings) != len(version.source_bindings) or len(bindings) != len(documents):
            return "LID_SOURCE_BINDING_MISMATCH"
        for document in documents.values():
            binding = bindings.get(document.id)
            if (
                binding is None
                or binding.knowledge_source_id != document.source_id
                or binding.source_metadata_checksum != document.metadata_checksum
                or binding.document_checksum is not None
                or binding.binding_type is not StandardPackBindingType.SUPPORTING
                or not binding.required
            ):
                return "LID_SOURCE_BINDING_MISMATCH"
        if lid_claims(sources) != provenance.manifest["claim_ledger"]:
            return "LID_SOURCE_CLAIM_MISMATCH"
        if not lid_rules_current(
            session, version, provenance.manifest["rules"], documents=documents
        ):
            return "LID_DRAFT_RULE_UNAVAILABLE"
    except (AttributeError, KeyError, TypeError, ValueError):
        return "LID_SOURCE_OR_RULE_UNAVAILABLE"
    return None


def create_lid_blueprint(
    session: Session, context: TenantContext, pins: SourcePins
) -> StandardPackVersion:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": _LID_DRAFT_LOCK}
        )
    documents = resolve_lid_sources(session, pins)
    pack = session.scalar(select(StandardPack).where(StandardPack.code == LID_DRAFT_PACK_CODE))
    if pack is not None:
        existing = next(
            (v for v in pack.versions if v.version_label == LID_BLUEPRINT_VERSION), None
        )
        if existing is not None:
            if (
                existing.lid_provenance is None
                or _pins(existing.lid_provenance.manifest["sources"]) != pins
                or lid_draft_integrity(session, existing) is not None
            ):
                raise ValueError("LID_DRAFT_EXACT_REPLAY_CONFLICT")
            return existing
    else:
        pack = StandardPack(
            code=LID_DRAFT_PACK_CODE,
            name="Leben in Deutschland — practice blueprint (draft)",
            description=(
                "Source-backed technical structure; no official questions or provider approval."
            ),
            assessment_family=AssessmentFamily.INTEGRATION_KNOWLEDGE,
            exam_family=LID_EXAM_FAMILY,
            authority_id=documents["bamf-lid-catalogue"].source.authority_id,
            language="de",
            status=StandardPackStatus.DRAFT,
        )
    now = datetime.now(timezone.utc)
    version = StandardPackVersion(
        standard_pack=pack,
        version_label=LID_BLUEPRINT_VERSION,
        status=StandardPackStatus.DRAFT,
        created_at=now,
        created_by_user_id=context.user_id,
        notes=LID_NOTICE,
        configuration_schema_version=LID_DRAFT_SCHEMA,
        supported_levels=[],
        allowed_classifications=["PRACTICE"],
        scoring_configuration_ref="objective:select-one:one-point:v1",
    )
    session.add(version)
    for key, document in documents.items():
        version.source_bindings.append(
            StandardPackSourceBinding(
                knowledge_source_id=document.source_id,
                document_version_id=document.id,
                source_metadata_checksum=document.metadata_checksum,
                binding_type=StandardPackBindingType.SUPPORTING,
                purpose=f"Exact bibliographic LiD practice reference: {key}",
                required=True,
                created_at=now,
            )
        )
    build_lid_structure(version, now)
    session.flush()
    sources = lid_source_snapshots(documents)
    rules = create_lid_rules(session, context, version, documents)
    manifest = {
        "schema": LID_DRAFT_SCHEMA,
        "blueprint_version": LID_BLUEPRINT_VERSION,
        "selection_policy_version": LID_SELECTION_VERSION,
        "state_choice": "EXPLICIT_PRACTICE_SELECTION_NOT_VERIFIED_RESIDENCE",
        "jurisdictions": [{"code": code, "name": name} for code, name in LID_STATES.items()],
        "question_pools": _QUESTION_POOLS,
        "question_pool_schema": {
            "pinned_fields": [
                "catalogue_edition",
                "question_identity",
                "scope",
                "bundesland",
                "rights_status",
                "content_checksum_if_ingestible",
                "effective_from",
                "supersession",
            ],
            "official_content_ingested": False,
            "selected_state_catalogue_slots": 310,
            "nationwide_catalogue_slots": 460,
            "official_sampling_reproduced": False,
        },
        "structure": LID_STRUCTURE,
        "cefr_level": None,
        "classification": "PRACTICE",
        "scoring_mode": "OBJECTIVE_KEYED",
        "response_mode": "SELECT_ONE",
        "notice": LID_NOTICE,
        "content_maturity": "AUTHORITATIVE_METADATA_ONLY",
        "sources": sources,
        "claim_ledger": lid_claims(sources),
        "rules": rules,
        "publication_blockers": [
            {"code": code, "message": message} for code, message in LID_PUBLICATION_BLOCKERS
        ],
    }
    version.result_configuration_ref = "lid-draft-preview:" + canonical_checksum(rules)
    digest = canonical_checksum(manifest)
    version.configuration_checksum = StandardPackService.checksum(
        version, lid_manifest_checksum=digest
    )
    session.flush()
    version.lid_provenance = LidBlueprintProvenance(
        manifest=manifest,
        manifest_checksum=digest,
        recorded_at=now,
        recorded_by_user_id=context.user_id,
    )
    session.flush()
    if reason := lid_draft_integrity(session, version):
        raise ValueError(reason)
    AuditService.record_security(
        session,
        event_type="standards.lid_draft.created",
        entity_type="standard_pack",
        entity_id=str(version.id),
        actor_id=context.user_id,
        metadata={"pack_checksum": version.configuration_checksum, "manifest_checksum": digest},
    )
    return version


def lid_summary(version: StandardPackVersion) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "standard_pack_id": str(version.standard_pack_id),
        "code": version.standard_pack.code,
        "checksum": version.configuration_checksum,
        "status": version.status.value,
        "version_label": version.version_label,
        "assessment_family": version.standard_pack.assessment_family.value,
        "exam_family": version.standard_pack.exam_family,
        "language": version.standard_pack.language,
        "cefr_level": None,
        "supported_levels": [],
        "allowed_classifications": ["PRACTICE"],
        "review_status": "REVIEW_REQUIRED",
        "official_provider_authorization": False,
        "authoritative_validity": "NOT_ESTABLISHED",
    }


def lid_detail(session: Session, version: StandardPackVersion) -> dict[str, Any]:
    provenance = version.lid_provenance
    if provenance is None:
        raise ValueError("LID_DRAFT_PROVENANCE_INVALID")
    manifest = provenance.manifest
    integrity = lid_draft_integrity(session, version)
    blockers = list(manifest["publication_blockers"])
    if integrity:
        blockers.append(
            {
                "code": integrity,
                "message": "The exact draft source or rule pins are no longer current.",
            }
        )
    return {
        **lid_summary(version),
        **{
            key: manifest[key]
            for key in (
                "notice",
                "claim_ledger",
                "rules",
                "jurisdictions",
                "question_pools",
                "question_pool_schema",
                "structure",
                "state_choice",
                "selection_policy_version",
                "content_maturity",
            )
        },
        "manifest_checksum": provenance.manifest_checksum,
        "source_bindings": [
            {"key": key, **source, "available": integrity is None}
            for key, source in manifest["sources"].items()
        ],
        "publication_blockers": blockers,
        "structural_validation": "PASS" if integrity is None else "REVIEW_REQUIRED",
        "delivery_ready": False,
    }


def evaluate_lid_blueprint(
    session: Session,
    version: StandardPackVersion,
    *,
    raw_correct: int | None,
    answered_count: int,
    as_of: datetime,
    expected_pack_checksum: str | None = None,
) -> dict[str, Any]:
    reason = (
        "LID_PACK_CHECKSUM_MISMATCH"
        if expected_pack_checksum is not None
        and version.configuration_checksum != expected_pack_checksum
        else lid_draft_integrity(session, version)
    )
    provenance = version.lid_provenance
    complete = answered_count == LID_STRUCTURE["total_items"]
    result = evaluate_lid_preview(
        session,
        version,
        provenance.manifest["rules"] if provenance is not None and not reason else [],
        raw_correct_count=raw_correct if complete else None,
        total_item_count=LID_STRUCTURE["total_items"],
        as_of=as_of,
    )
    if reason or not complete:
        result["reason"] = reason or "LID_PRACTICE_INCOMPLETE"
    return {
        **result,
        "execution_class": "DRAFT_PREVIEW",
        "raw_correct": raw_correct,
        "answered_count": answered_count,
        "total_item_count": LID_STRUCTURE["total_items"],
        "official_outcome": "UNDETERMINED",
        "administrative_recognition": "NOT_ESTABLISHED",
        "official_result_created": False,
        "standard_pack_version_id": str(version.id),
        "standard_pack_checksum": version.configuration_checksum,
    }
