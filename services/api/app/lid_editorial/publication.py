"""Separate genuine pack approval and publication; test evidence never qualifies."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.exams.service import ExamStudioService, exam_checksum
from app.lid_editorial import collections
from app.lid_editorial.access import editorial_verified_identity
from app.lid_editorial.reviews import (
    current_item_evidence,
    lock_editorial_source_roots,
    publish_approved_item,
)
from app.lid_policy import LID_ORIGINAL_NOTICE
from app.lid_practice import service as lid
from app.models import ExamVersion, StandardPackVersion
from app.models.enums import ExamVersionStatus, StandardPackStatus
from app.models.lid_editorial import LidEditorialCollection, LidEditorialPublication
from app.models.lid_practice import LidPracticeForm, LidPracticePoolVersion
from app.standard_packs.lid_service import lid_draft_integrity
from app.standard_packs.service import StandardPackService
from app.writing_assessment.service import canonical_checksum


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _operation(session: Session, operation_id: uuid.UUID) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 40))"),
            {"key": str(operation_id)},
        )


def _evidence(
    session: Session, collection: LidEditorialCollection, expected: str
) -> tuple[dict[str, Any], set[uuid.UUID]]:
    rows = collections.current_versions(session, collection)
    if collections.current_checksum(collection, rows) != expected:
        raise ValueError("LID_EDITORIAL_STALE_COLLECTION_CHECKSUM")
    collections.exact_preview_order(collection, rows)
    blueprint = session.get(StandardPackVersion, collection.blueprint_version_id)
    if (
        blueprint is None
        or blueprint.configuration_checksum != collection.manifest["blueprint_checksum"]
        or lid_draft_integrity(session, blueprint) is not None
    ):
        raise ValueError("LID_EDITORIAL_CURRENT_BLUEPRINT_REQUIRED")
    lock_editorial_source_roots(session)
    actors = {collection.created_by_user_id}
    pins = []
    for pin, item in rows:
        evidence = current_item_evidence(session, item, mode=collections.GENUINE)
        if not evidence["eligible"] or not evidence["genuine_approved"]:
            raise ValueError("PRACTICE_FORM_NOT_READY")
        if item.created_by_user_id:
            actors.add(item.created_by_user_id)
        actors.update(receipt.actor_user_id for receipt in evidence["receipts"])
        pins.append(
            {
                "item_id": str(item.item_id),
                "item_version_id": str(item.id),
                "item_checksum": item.checksum,
                "provenance_checksum": pin.checksum,
                "key_checksum": pin.provenance["key_checksum"],
                "scope": pin.scope,
                "jurisdiction": pin.jurisdiction,
                "source": pin.provenance["source"],
                "source_rights_checksum": evidence["receipts"][-1].source_rights_checksum,
                "receipts": [
                    {"id": str(receipt.id), "checksum": receipt.checksum}
                    for receipt in evidence["receipts"]
                ],
            }
        )
    return {
        "mode": collections.GENUINE,
        "collection_id": str(collection.id),
        "collection_checksum": expected,
        "standard_pack_version_id": str(collection.pack_version_id),
        "standard_pack_checksum": collection.manifest["pack_checksum"],
        "blueprint_version_id": str(blueprint.id),
        "blueprint_checksum": blueprint.configuration_checksum,
        "rule_pins": blueprint.lid_provenance.manifest["rules"] if blueprint.lid_provenance else [],
        "bundesland": collections.BERLIN,
        "selection_version": lid.SELECTION_VERSION,
        "duration_seconds": lid.DURATION_SECONDS,
        "items": pins,
    }, actors


def _build_form(
    session: Session,
    context: TenantContext,
    collection: LidEditorialCollection,
    pack: StandardPackVersion,
    seed: str,
) -> tuple[ExamVersion, LidPracticeForm]:
    blueprint = session.get(StandardPackVersion, collection.blueprint_version_id)
    if blueprint is None:
        raise ValueError("LID_EDITORIAL_CURRENT_BLUEPRINT_REQUIRED")
    rows = collections.current_versions(session, collection)
    for _, item in rows:
        publish_approved_item(session, context, item, pack_version_id=pack.id)
    exam = ExamStudioService.create_exam(
        session,
        context,
        code=pack.standard_pack.code,
        name=lid.TITLE,
        scope_type="ORGANISATION_PRIVATE",
        organisation_id=collection.organisation_id,
        description=LID_ORIGINAL_NOTICE,
    )
    version = ExamStudioService.create_version(
        session,
        context,
        exam,
        version_label="original-berlin-practice-v1",
        standard_pack_version_id=pack.id,
    )
    ExamStudioService.materialize_blueprint(session, context, version)
    pools = []
    for section, scope, state, count in zip(
        version.modules[0].sections,
        (lid.GENERAL, lid.STATE),
        ("DE", collections.BERLIN),
        (30, 3),
        strict=True,
    ):
        pool = LidPracticePoolVersion(
            id=uuid.uuid4(),
            organisation_id=collection.organisation_id,
            practice_pack_version_id=pack.id,
            scope=scope,
            jurisdiction=state,
            version_label="original-berlin-practice-v1",
            created_at=_now(),
        )
        pins = [
            lid.original_item_pin(session, item, collection.organisation_id)
            for extension, item in rows
            if extension.scope == scope and extension.jurisdiction == state
        ]
        pool.manifest = lid.pool_manifest(pool, blueprint, pins, mode=collections.GENUINE)
        pool.checksum = exam_checksum(pool.manifest)
        session.add(pool)
        session.flush()
        pools.append(pool)
        for slot, pin in enumerate(lid.select_items(pool, seed, count)):
            ExamStudioService.assign_item(
                session,
                context,
                version,
                task_id=section.tasks[0].id,
                item_version_id=uuid.UUID(pin["item_version_id"]),
                slot_index=slot,
            )
    general, state_pool = pools
    manifest = lid.form_manifest(session, version, pack, blueprint, general, state_pool, seed)
    form = LidPracticeForm(
        exam_version_id=version.id,
        blueprint_version_id=blueprint.id,
        general_pool_version_id=general.id,
        state_pool_version_id=state_pool.id,
        bundesland=collections.BERLIN,
        selection_version=lid.SELECTION_VERSION,
        selection_seed=seed,
        manifest=manifest,
        checksum=exam_checksum(manifest),
        created_at=_now(),
    )
    session.add(form)
    session.flush()
    return version, form


def decide_pack(
    session: Session,
    context: TenantContext,
    collection: LidEditorialCollection,
    *,
    action: str,
    expected_collection_checksum: str,
    operation_id: uuid.UUID,
) -> LidEditorialPublication:
    collection = collections.lock_collection(session, collection.id)
    if context.organisation_id != collection.organisation_id or context.membership_id is None:
        raise AuthorizationError("LID_EDITORIAL_ACTIVE_MEMBERSHIP_REQUIRED")
    if action not in {"APPROVED", "PUBLISHED"}:
        raise ValueError("LID_EDITORIAL_INVALID_PACK_ACTION")
    permission = "standard_pack.review" if action == "APPROVED" else "standard_pack.publish"
    AuthorizationService.require_permission(context, permission, collection.organisation_id)
    _operation(session, operation_id)
    existing = session.scalar(
        select(LidEditorialPublication).where(
            LidEditorialPublication.operation_id == operation_id,
        )
    )
    if existing is not None:
        if (
            existing.actor_user_id != context.user_id
            or existing.collection_id != collection.id
            or existing.action != action
            or existing.manifest["collection_checksum"] != expected_collection_checksum
        ):
            raise ValueError("LID_EDITORIAL_OPERATION_CONFLICT")
        if not editorial_verified_identity(session, context.user_id):
            raise AuthorizationError("LID_EDITORIAL_VERIFIED_HUMAN_REQUIRED")
        return existing
    manifest, actors = _evidence(session, collection, expected_collection_checksum)
    if not editorial_verified_identity(session, context.user_id):
        raise AuthorizationError("LID_EDITORIAL_VERIFIED_HUMAN_REQUIRED")
    evidence_checksum = canonical_checksum(manifest)
    pack = collections.verify_pack(session, collection)
    exam: ExamVersion | None = None
    if action == "APPROVED":
        if (
            context.user_id == collection.created_by_user_id
            or pack.status is not StandardPackStatus.DRAFT
        ):
            raise AuthorizationError("LID_EDITORIAL_INDEPENDENT_PACK_APPROVAL_REQUIRED")
        StandardPackService._apply_transition(
            session,
            pack,
            StandardPackStatus.HUMAN_REVIEW,
            actor_id=context.user_id,
            context=context,
        )
        StandardPackService._apply_transition(
            session, pack, StandardPackStatus.APPROVED, actor_id=context.user_id, context=context
        )
    else:
        approval = session.scalar(
            select(LidEditorialPublication).where(
                LidEditorialPublication.collection_id == collection.id,
                LidEditorialPublication.action == "APPROVED",
            )
        )
        if (
            approval is None
            or pack.status is not StandardPackStatus.APPROVED
            or approval.eligibility_checksum != evidence_checksum
            or approval.manifest != manifest
            or approval.checksum != canonical_checksum(approval.manifest)
        ):
            raise ValueError("LID_EDITORIAL_EXACT_PACK_APPROVAL_REQUIRED")
        if context.user_id in actors | {approval.actor_user_id}:
            raise AuthorizationError("LID_EDITORIAL_INDEPENDENT_PUBLISHER_REQUIRED")
        for perm in (
            "question_bank.publish",
            "exam.manage",
            "exam.review",
            "exam.publish",
            "exam.activate",
        ):
            AuthorizationService.require_permission(context, perm, collection.organisation_id)
        StandardPackService._apply_transition(
            session, pack, StandardPackStatus.PUBLISHED, actor_id=context.user_id, context=context
        )
        StandardPackService._apply_transition(
            session, pack, StandardPackStatus.ACTIVE, actor_id=context.user_id, context=context
        )
        pack.standard_pack.status = StandardPackStatus.ACTIVE
        session.flush()
        exam, form = _build_form(session, context, collection, pack, evidence_checksum)
        manifest = {
            **manifest,
            "pack_approval_id": str(approval.id),
            "pack_approval_checksum": approval.checksum,
            "exam_version_id": str(exam.id),
            "form_checksum": form.checksum,
            "general_pool_version_id": str(form.general_pool_version_id),
            "state_pool_version_id": str(form.state_pool_version_id),
        }
    decision = LidEditorialPublication(
        collection_id=collection.id,
        exam_version_id=exam.id if exam else None,
        operation_id=operation_id,
        action=action,
        actor_user_id=context.user_id,
        eligibility_checksum=evidence_checksum,
        manifest=manifest,
        checksum=canonical_checksum(manifest),
        created_at=_now(),
    )
    session.add(decision)
    session.flush()
    if exam is not None:
        ExamStudioService.request_review(session, context, exam)
        for target in (
            ExamVersionStatus.APPROVED,
            ExamVersionStatus.PUBLISHED,
            ExamVersionStatus.ACTIVE,
        ):
            ExamStudioService.transition(session, context, exam, target)
    collections._audit(
        session,
        context,
        f"pack_{action.lower()}",
        collection,
        {
            "decision_category": action,
            "operation_id": str(operation_id),
            "pack_version_id": str(pack.id),
            "evidence_checksum": evidence_checksum,
            "publication_state": pack.status.value,
        },
    )
    return decision
