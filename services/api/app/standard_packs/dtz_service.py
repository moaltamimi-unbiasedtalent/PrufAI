"""Seal and inspect a versioned DTZ practice draft with exact source provenance."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import TenantContext
from app.dtz_policy import (
    DTZ_EXAM_FAMILY,
    DTZ_PACK_CODE,
    DTZ_PUBLICATION_BLOCKERS,
    DTZ_SCHEMA,
)
from app.models import KnowledgeDocumentVersion, StandardPack, StandardPackVersion
from app.models.enums import AssessmentFamily, StandardPackBindingType, StandardPackStatus
from app.models.standard_pack import DtzBlueprintProvenance, StandardPackSourceBinding
from app.standard_packs.dtz_blueprint import (
    ORIGINAL,
    UNSUPPORTED,
    build_dtz_structure,
    dtz_sections,
    verify_dtz_structure,
)
from app.standard_packs.dtz_governance import (
    create_dtz_review_artifacts,
    dtz_review_artifacts_current,
)
from app.standard_packs.dtz_sources import DTZ_ENTRIES, dtz_claims, dtz_sources, resolve_dtz_sources
from app.standard_packs.service import StandardPackService
from app.writing_assessment.service import canonical_checksum

NOTICE = "DTZ-informed practice draft. No official DTZ examination, result, or certificate."
_LABEL = "S2 DTZ A2–B1 practice preview 1"
_DRAFT_LOCK = 6010038
SourcePins = dict[str, tuple[uuid.UUID, str]]


def dtz_version(session: Session, version_id: uuid.UUID) -> StandardPackVersion | None:
    return session.scalar(
        select(StandardPackVersion)
        .join(DtzBlueprintProvenance)
        .where(
            StandardPackVersion.id == version_id,
            StandardPackVersion.configuration_schema_version == DTZ_SCHEMA,
        )
    )


def _bind_sources(
    version: StandardPackVersion,
    documents: dict[str, KnowledgeDocumentVersion],
    now: datetime,
) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    for key, document in documents.items():
        legal = document.legal_metadata
        entry = DTZ_ENTRIES[key]
        assert legal is not None and document.metadata_checksum is not None
        binding = StandardPackSourceBinding(
            id=uuid.uuid4(),
            knowledge_source_id=document.source_id,
            document_version_id=document.id,
            source_metadata_checksum=document.metadata_checksum,
            binding_type=StandardPackBindingType.SUPPORTING,
            purpose=f"Exact bibliographic DTZ practice reference: {key}",
            required=True,
            created_at=now,
        )
        version.source_bindings.append(binding)
        snapshots[key] = {
            "id": str(binding.id),
            "key": key,
            "document_version_id": str(document.id),
            "source_metadata_checksum": document.metadata_checksum,
            "source_url": document.source_url,
            "canonical_url": entry.url,
            "publisher": entry.publisher,
            "title": entry.title,
            "version_label": document.version_label,
            "verified_on": document.retrieved_at.date().isoformat()
            if document.retrieved_at
            else None,
            "effective_from": document.effective_from.isoformat()
            if document.effective_from
            else None,
            "legal_review_status": legal.legal_review_status.value,
            "rights_status": "BIBLIOGRAPHIC_REFERENCE_ONLY",
            "rights_review_actor_id": str(legal.legal_reviewed_by_user_id),
            "rights_decision_checksum": legal.decision_checksum,
            "ingestion_allowed": False,
            "derivative_use_allowed": False,
            "redistribution_allowed": False,
            "retention_policy": legal.retention_policy,
        }
    return snapshots


def _reference_url(source: dict[str, Any], locator: str) -> str:
    key = source["key"]
    provider_anchor = re.search(r"#c[0-9]+", locator)
    if provider_anchor and key.startswith("gast-dtz-"):
        return str(source["canonical_url"]) + provider_anchor.group()
    provision = re.search(r"Consolidated Int(Test)?V § ([0-9]+)", locator, re.IGNORECASE)
    if provision and key in {"inttestv-2026-306", "intv-2026-218"}:
        ordinance = "inttestv" if key == "inttestv-2026-306" else "intv"
        return f"https://www.gesetze-im-internet.de/{ordinance}/__{provision[2]}.html"
    if key == "gast-dtz-specimen-2024":
        page = re.search(r"printed pages? ([0-9]+)", locator)
        if page:
            return f"{source['source_url']}#page={page[1]}"
    return str(source["source_url"])


def _ledger(snapshots: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    ledger = dtz_claims()
    for comparison in ledger:
        for observation in comparison["claims"]:
            source = snapshots[observation["source_key"]]
            observation.update(
                source_binding_id=source["id"],
                source_version_id=source["document_version_id"],
                source_metadata_checksum=source["source_metadata_checksum"],
                source_url=source["source_url"],
                reference_url=_reference_url(source, observation["locator"]),
                verified_on=source["verified_on"],
            )
    return ledger


def _source_pins(manifest: dict[str, Any]) -> SourcePins:
    return {
        key: (uuid.UUID(record["document_version_id"]), record["source_metadata_checksum"])
        for key, record in manifest["sources"].items()
    }


def dtz_draft_integrity(
    session: Session, version: StandardPackVersion, *, check_current_sources: bool = True
) -> str | None:
    """Return a safe failure code; exact provenance is checked before preview."""
    provenance = version.dtz_provenance
    if (
        provenance is None
        or version.configuration_schema_version != DTZ_SCHEMA
        or version.standard_pack.code != DTZ_PACK_CODE
        or version.standard_pack.assessment_family is not AssessmentFamily.LANGUAGE
        or version.standard_pack.exam_family != DTZ_EXAM_FAMILY
        or version.standard_pack.language != "de"
        or version.supported_levels != ["A2", "B1"]
        or version.allowed_classifications != ["PRACTICE"]
        or version.status is not StandardPackStatus.DRAFT
        or version.standard_pack.status is not StandardPackStatus.DRAFT
        or version.rule_bindings
        or canonical_checksum(provenance.manifest) != provenance.manifest_checksum
        or version.configuration_checksum != StandardPackService.checksum(version)
    ):
        return "DTZ_DRAFT_PROVENANCE_INVALID"
    try:
        verify_dtz_structure(version)
        pins = _source_pins(provenance.manifest)
        if check_current_sources:
            current = resolve_dtz_sources(session, pins)
            for key, source in current.items():
                rights = source.legal_metadata
                snapshot = provenance.manifest["sources"][key]
                if (
                    rights is None
                    or rights.decision_checksum != snapshot["rights_decision_checksum"]
                ):
                    return "DTZ_SOURCE_RIGHTS_STALE"
        actual = {
            (binding.document_version_id, binding.source_metadata_checksum)
            for binding in version.source_bindings
            if binding.binding_type is StandardPackBindingType.SUPPORTING
            and binding.document_checksum is None
        }
        if len(version.source_bindings) != len(pins) or actual != set(pins.values()):
            return "DTZ_SOURCE_BINDING_INVALID"
        if not dtz_review_artifacts_current(session, version, provenance.manifest["artifacts"]):
            return "DTZ_REVIEW_ARTIFACTS_STALE"
    except (KeyError, TypeError, ValueError) as exc:
        if str(exc) == "DTZ_SOURCE_SUCCESSOR_REVIEWED":
            return "DTZ_SOURCE_SUCCESSOR_REVIEWED"
        if str(exc) in {
            "DTZ_EXACT_SOURCE_PIN_MISMATCH",
            "DTZ_REVIEWED_METADATA_SOURCE_REQUIRED",
        }:
            return "DTZ_SOURCE_RIGHTS_OR_VERSION_STALE"
        return "DTZ_DRAFT_PROVENANCE_INVALID"
    return None


def create_dtz_blueprint(
    session: Session, context: TenantContext, pins: SourcePins
) -> StandardPackVersion:
    """Build once per sealed edition; exact concurrent replay returns the same row."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": _DRAFT_LOCK})
    documents = resolve_dtz_sources(session, pins)
    pack = session.scalar(select(StandardPack).where(StandardPack.code == DTZ_PACK_CODE))
    if pack is not None:
        existing = next(
            (version for version in pack.versions if version.version_label == _LABEL), None
        )
        if (
            existing is None
            or existing.dtz_provenance is None
            or _source_pins(existing.dtz_provenance.manifest) != pins
            or dtz_draft_integrity(session, existing) is not None
        ):
            raise ValueError("DTZ_DRAFT_EXACT_REPLAY_CONFLICT")
        return existing

    now = datetime.now(timezone.utc)
    pack = StandardPack(
        id=uuid.uuid4(),
        code=DTZ_PACK_CODE,
        name="Deutsch-Test für Zuwanderer A2–B1 · Practice foundation",
        description=NOTICE,
        assessment_family=AssessmentFamily.LANGUAGE,
        authority_id=documents["gast-dtz-information"].source.authority_id,
        exam_family=DTZ_EXAM_FAMILY,
        language="de",
        status=StandardPackStatus.DRAFT,
    )
    version = StandardPackVersion(
        id=uuid.uuid4(),
        standard_pack=pack,
        version_label=_LABEL,
        status=StandardPackStatus.DRAFT,
        created_at=now,
        created_by_user_id=context.user_id,
        notes=NOTICE,
        configuration_schema_version=DTZ_SCHEMA,
        supported_levels=["A2", "B1"],
        allowed_classifications=["PRACTICE"],
    )
    session.add(version)
    snapshots = _bind_sources(version, documents, now)
    writing_task, speaking_task = build_dtz_structure(version, now)
    session.flush()
    artifacts = create_dtz_review_artifacts(
        session, context, version, documents, writing_task, speaking_task
    )
    rule_refs = {item["code"]: item for item in artifacts["rules"]}
    result_rule = rule_refs["PRUFAI_DTZ_S2_OVERALL_B1"]
    objective_rule = next(item for item in artifacts["rules"] if item["id"] != result_rule["id"])
    version.scoring_configuration_ref = (
        f"dtz-preview:{objective_rule['id']}:{objective_rule['checksum']}"
    )
    version.result_configuration_ref = f"dtz-preview:{result_rule['id']}:{result_rule['checksum']}"
    version.rubric_set_ref = "dtz-preview:" + canonical_checksum(artifacts["rubrics"])
    session.flush()
    ledger = _ledger(snapshots)
    codes = {comparison["code"] for comparison in ledger}
    if not {f"DTZ_{module.code}_STRUCTURE" for module in version.modules} <= codes:
        raise ValueError("DTZ_BLUEPRINT_STRUCTURE_CLAIM_REQUIRED")
    manifest = {
        "schema": DTZ_SCHEMA,
        "content_maturity": ORIGINAL,
        "provider_alignment": "REFERENCE_ONLY",
        "delivery_ready": False,
        "speaking_fidelity": UNSUPPORTED,
        "written_nominal_minutes": 100,
        "regulatory_candidate_effective_from": "2026-11-01",
        "review_status": "REVIEW_REQUIRED",
        "notice": NOTICE,
        "sources": snapshots,
        "claim_ledger": ledger,
        "artifacts": artifacts,
        "publication_blockers": [
            {"code": code, "message": message} for code, message in DTZ_PUBLICATION_BLOCKERS
        ],
    }
    digest = canonical_checksum(manifest)
    version.configuration_checksum = StandardPackService.checksum(
        version, dtz_manifest_checksum=digest
    )
    session.flush()
    session.add(
        DtzBlueprintProvenance(
            pack_version=version,
            manifest=manifest,
            manifest_checksum=digest,
            recorded_at=now,
            recorded_by_user_id=context.user_id,
        )
    )
    session.flush()
    if dtz_draft_integrity(session, version) is not None:
        raise ValueError("DTZ_DRAFT_SEAL_INVALID")
    AuditService.record_security(
        session,
        event_type="standards.dtz_draft.created",
        entity_type="standard_pack",
        entity_id=str(version.id),
        actor_id=context.user_id,
        metadata={"pack_checksum": version.configuration_checksum, "manifest_checksum": digest},
    )
    return version


def dtz_summary(version: StandardPackVersion) -> dict[str, Any]:
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
        "supported_levels": version.supported_levels,
        "allowed_classifications": version.allowed_classifications,
        "effective_from": version.effective_from,
        "effective_until": version.effective_until,
        "review_status": "REVIEW_REQUIRED",
        "official_provider_authorization": False,
        "authoritative_validity": "NOT_ESTABLISHED",
    }


def dtz_detail(session: Session, version: StandardPackVersion) -> dict[str, Any]:
    provenance = version.dtz_provenance
    if provenance is None:
        raise ValueError("DTZ_DRAFT_PROVENANCE_INVALID")
    manifest = provenance.manifest
    current = {row["key"]: row["registered"] for row in dtz_sources(session)}
    bindings = []
    for key, source in manifest["sources"].items():
        live = current.get(key) or {}
        available = (
            live.get("id") == source["document_version_id"]
            and live.get("metadata_checksum") == source["source_metadata_checksum"]
            and live.get("reviewed") is True
        )
        bindings.append({**source, "available": available})
    integrity = dtz_draft_integrity(session, version)
    blockers = list(manifest["publication_blockers"])
    if integrity:
        blockers.append(
            {
                "code": integrity,
                "message": "The exact draft or source pins are no longer available.",
            }
        )
    return {
        **dtz_summary(version),
        "notice": manifest["notice"],
        "manifest_checksum": provenance.manifest_checksum,
        "sections": dtz_sections(version),
        "source_bindings": bindings,
        "claim_ledger": manifest["claim_ledger"],
        "rules": manifest["artifacts"]["rules"],
        "rubrics": manifest["artifacts"]["rubrics"],
        "publication_blockers": blockers,
        "speaking_fidelity": manifest["speaking_fidelity"],
        "content_maturity": manifest["content_maturity"],
        "delivery_ready": False,
        "structural_validation": "PASS" if not integrity else "REVIEW_REQUIRED",
        "regulatory_candidate_effective_from": manifest["regulatory_candidate_effective_from"],
    }
