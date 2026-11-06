"""Scoped operator projections; only an exact item view exposes its proposed key."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization import AuthorizationService, TenantContext
from app.lid_editorial.access import editorial_preview_enabled
from app.lid_editorial.collections import (
    GENUINE,
    MODE,
    current_checksum,
    current_versions,
    source_revision_required,
    validate_question,
    verify_pack,
)
from app.lid_editorial.content import editorial_source_snapshot, editorial_source_version
from app.lid_editorial.reviews import current_item_evidence, receipt_summary, review_actions
from app.models import QuestionBankItemVersion
from app.models.enums import QuestionBankLifecycleStatus, StandardPackStatus
from app.models.lid_editorial import (
    LidEditorialCollection,
    LidEditorialItemVersion,
    LidEditorialPublication,
)


def item_summary(
    session: Session, extension: LidEditorialItemVersion, version: QuestionBankItemVersion
) -> dict[str, Any]:
    evidence = current_item_evidence(session, version, mode=MODE, lock_for_review=False)
    return {
        "item_id": str(version.item_id),
        "item_version_id": str(version.id),
        "version_label": version.version_label,
        "checksum": version.checksum,
        "provenance_checksum": extension.checksum,
        "code": version.item.code,
        "scope": extension.scope,
        "jurisdiction": extension.jurisdiction,
        "ordinal": extension.ordinal,
        "authorship": extension.provenance["authorship"],
        "maturity": evidence["stage"],
        "status": version.status.value,
        "genuine_approved": evidence["genuine_approved"],
        "test_approved": evidence["test_approved"],
        "blockers": evidence["blockers"],
        "test_review_decisions": sum(
            r.evidence_class == "SYNTHETIC_TEST" for r in evidence["receipts"]
        ),
        "supersedes_version_id": str(version.supersedes_version_id)
        if version.supersedes_version_id
        else None,
    }


def item_detail(
    session: Session,
    collection: LidEditorialCollection,
    extension: LidEditorialItemVersion,
    version: QuestionBankItemVersion,
    context: TenantContext,
    *,
    include_history: bool = True,
) -> dict[str, Any]:
    if (
        extension.collection_id != collection.id
        or version.item.organisation_id != collection.organisation_id
    ):
        raise ValueError("LID_EDITORIAL_ITEM_NOT_FOUND")
    evidence = current_item_evidence(session, version, mode=MODE, lock_for_review=False)
    parsed = validate_question(version.content)
    source = extension.provenance["source"]
    try:
        current = editorial_source_version(
            session,
            source["source_key"],
            uuid.UUID(source["version_id"]),
            source["metadata_checksum"],
        )
        source = editorial_source_snapshot(source["source_key"], current)
    except ValueError:
        source = {**source, "current": False}
    history = []
    if include_history:
        rows = session.execute(
            select(LidEditorialItemVersion, QuestionBankItemVersion)
            .join(
                QuestionBankItemVersion,
                QuestionBankItemVersion.id == LidEditorialItemVersion.item_version_id,
            )
            .where(
                LidEditorialItemVersion.collection_id == collection.id,
                LidEditorialItemVersion.item_id == extension.item_id,
            )
            .order_by(QuestionBankItemVersion.created_at, QuestionBankItemVersion.id)
        ).tuples()
        history = [item_summary(session, ext, row) for ext, row in rows]
    actions = review_actions(session, collection, version, context)
    if (
        (
            version.status is QuestionBankLifecycleStatus.REJECTED
            or source_revision_required(session, version)
        )
        and verify_pack(session, collection).status is StandardPackStatus.DRAFT
        and any(row.id == version.id for _, row in current_versions(session, collection))
        and AuthorizationService.has_permission(
            context, "question_bank.manage", collection.organisation_id
        )
    ):
        actions["allowed_actions"].append("REVISE")
    return {
        **item_summary(session, extension, version),
        "prompt": parsed.prompt,
        "options": [
            {"option_id": opt.option_id, "display_text": opt.display_text}
            for opt in sorted(parsed.options, key=lambda v: v.order)
        ],
        "correct_option_id": parsed.correct_option_ids[0],
        "rationale": extension.provenance["rationale"],
        "claim": extension.provenance["claim"],
        "source": source,
        "key_checksum": extension.provenance["key_checksum"],
        "rights_status": "ORIGINAL_PRACTICE_RIGHTS_APPROVED"
        if evidence["genuine_approved"]
        else "RIGHTS_REVIEW_REQUIRED",
        "history": history,
        "review_receipts": [receipt_summary(row) for row in evidence["receipts"]],
        **actions,
    }


def collection_detail(
    session: Session,
    collection: LidEditorialCollection,
    context: TenantContext,
    *,
    include_items: bool = True,
) -> dict[str, Any]:
    pack = verify_pack(session, collection)
    rows = current_versions(session, collection)
    items = [item_summary(session, extension, version) for extension, version in rows]
    checksum = current_checksum(collection, rows)
    genuine = [item for item in items if item["genuine_approved"]]
    published = [
        item
        for item in genuine
        if item["status"]
        in {QuestionBankLifecycleStatus.PUBLISHED.value, QuestionBankLifecycleStatus.ACTIVE.value}
    ]
    ready = len(genuine) == len(items) == 33
    participants = {collection.created_by_user_id}
    if ready:
        for _, version in rows:
            if version.created_by_user_id:
                participants.add(version.created_by_user_id)
            participants.update(
                receipt.actor_user_id
                for receipt in current_item_evidence(
                    session, version, mode=GENUINE, lock_for_review=False
                )["receipts"]
            )
    approval = session.scalar(
        select(LidEditorialPublication).where(
            LidEditorialPublication.collection_id == collection.id,
            LidEditorialPublication.action == "APPROVED",
        )
    )
    publication = session.scalar(
        select(LidEditorialPublication).where(
            LidEditorialPublication.collection_id == collection.id,
            LidEditorialPublication.action == "PUBLISHED",
        )
    )
    blockers = [] if ready else ["PRACTICE_FORM_NOT_READY", "INDEPENDENT_ITEM_APPROVAL_REQUIRED"]
    if publication is None:
        blockers.append("PRACTICE_PACK_NOT_PUBLISHED")
    return {
        "id": str(collection.id),
        "organisation_id": str(collection.organisation_id),
        "code": collection.code,
        "checksum": checksum,
        "blueprint_version_id": str(collection.blueprint_version_id),
        "bundesland": "DE-BE",
        "cefr_level": None,
        "classification": "PRACTICE",
        "pack": {
            "id": str(pack.id),
            "code": pack.standard_pack.code,
            "checksum": pack.configuration_checksum,
            "status": pack.status.value,
        },
        "counts": {
            "drafted": len(items),
            "fact_check_pending": sum(
                item["maturity"] in {"AI_ASSISTED_DRAFT", "FACT_CHECK_REQUIRED"} for item in items
            ),
            "expert_review_pending": sum(
                item["maturity"] == "EXPERT_REVIEW_REQUIRED" for item in items
            ),
            "rights_pending": sum(item["maturity"] == "RIGHTS_REVIEW_REQUIRED" for item in items),
            "approved": len(genuine),
            "rejected": sum(item["status"] == "REJECTED" for item in items),
            "withdrawn": sum(item["status"] in {"RETIRED", "SUPERSEDED"} for item in items),
            "published": len(published),
            "general_pool_eligible": sum(item["scope"] == "GENERAL_DE" for item in genuine),
            "berlin_pool_eligible": sum(item["scope"] == "BUNDESLAND_SPECIFIC" for item in genuine),
            "test_review_decisions": sum(item["test_review_decisions"] for item in items),
        },
        "blockers": blockers,
        "allowed_actions": {
            "preview": editorial_preview_enabled(session)
            and AuthorizationService.has_permission(
                context, "question_bank.review", collection.organisation_id
            ),
            "approve": ready
            and pack.status is StandardPackStatus.DRAFT
            and context.user_id != collection.created_by_user_id
            and AuthorizationService.has_permission(
                context, "standard_pack.review", collection.organisation_id
            ),
            "publish": ready
            and pack.status is StandardPackStatus.APPROVED
            and approval is not None
            and context.user_id not in participants | {approval.actor_user_id}
            and all(
                AuthorizationService.has_permission(context, permission, collection.organisation_id)
                for permission in (
                    "standard_pack.publish",
                    "question_bank.publish",
                    "exam.manage",
                    "exam.review",
                    "exam.publish",
                    "exam.activate",
                )
            ),
        },
        "published_exam_version_id": str(publication.exam_version_id) if publication else None,
        "items": items if include_items else [],
        "review_mode": MODE,
        "official_certification": False,
    }
