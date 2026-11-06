"""Source, credential, and item pins for independent LiD editorial review."""

from __future__ import annotations

import unicodedata
import uuid
from datetime import date, datetime, timezone
from typing import Any, Literal, TypedDict
from zoneinfo import ZoneInfo

from sqlalchemy import case, select, text, tuple_
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import (
    AuthorizationError,
    AuthorizationService,
    TenantContext,
    build_tenant_context,
)
from app.lid_editorial.access import (
    editorial_preview_enabled,
    editorial_verified_identity,
    editorial_verified_reference,
)
from app.lid_editorial.content import S4_ENTRIES, editorial_source_version
from app.lid_editorial.review_contracts import (
    REVIEW_PERMISSIONS,
    REVIEW_PROTOCOL,
    REVIEW_TYPE,
    STAGES,
    SUBMIT_PERMISSION,
    ReviewDecision,
    ReviewRights,
    ReviewSubmission,
    Stage,
)
from app.lid_policy import LID_EXAM_FAMILY
from app.models.ai_orchestration import HumanReviewRequest
from app.models.enums import (
    AssessmentFamily,
    HumanReviewStatus,
    KnowledgeVersionStatus,
    PackMediaRequirement,
    PackResponseMode,
    QuestionBankLifecycleStatus,
    QuestionBankOriginType,
    QuestionBankRightsStatus,
    QuestionBankScoringPolicy,
    QuestionBankTaskType,
    StandardPackStatus,
    UserStatus,
)
from app.models.expert_evaluation import EvaluationExaminerQualification
from app.models.identity import User
from app.models.knowledge import KnowledgeDocumentVersion, KnowledgeSource
from app.models.lid_editorial import LidEditorialCollection, LidEditorialItemVersion
from app.models.lid_editorial_review import LidEditorialReviewReceipt
from app.models.question_bank import (
    QuestionBankItem,
    QuestionBankItemCefrLevel,
    QuestionBankItemVersion,
    QuestionBankStandardPackBinding,
)
from app.models.standard_pack import StandardPackTaskSpecification, StandardPackVersion
from app.question_bank.schemas import MultipleChoiceContent
from app.writing_assessment.service import canonical_checksum

Mode = Literal["CONTENT_EDITORIAL_PREVIEW", "PUBLISHED_PRACTICE"]
Status = QuestionBankLifecycleStatus
_TERMINAL_REVIEW = {Status.REJECTED, Status.RETIRED, Status.SUPERSEDED}
_GENUINE_STATUS = {Status.APPROVED, Status.PUBLISHED, Status.ACTIVE}
_PUBLISHED_STATUS = {Status.PUBLISHED, Status.ACTIVE}
_DRAFT_STATUS = {Status.DRAFT, Status.VALIDATION_REQUIRED}


class EditorialConflict(ValueError):
    """The exact content or operation pin can no longer be used."""


class ItemEvidence(TypedDict):
    eligible: bool
    preview_eligible: bool
    genuine_approved: bool
    test_approved: bool
    stage: str
    blockers: list[str]
    receipts: list[LidEditorialReviewReceipt]


def _utc(value: datetime) -> datetime:
    return (value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)).astimezone(
        timezone.utc
    )


def answer_key_checksum(content: dict[str, Any]) -> str:
    return canonical_checksum({"correct_option_ids": content["correct_option_ids"]})


def _normal_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def lock_editorial_source_roots(session: Session) -> None:
    """Acquire shared curated source locks in one order before a multi-item mutation."""
    document = KnowledgeDocumentVersion
    root_pins = [(entry.source_code, entry.version_label) for entry in S4_ENTRIES.values()]
    session.scalars(
        select(document.id)
        .join(document.source)
        .where(tuple_(KnowledgeSource.source_code, document.version_label).in_(root_pins))
        .order_by(document.id)
        .with_for_update(of=document)
    ).all()


def _item_metadata(
    session: Session,
    version: QuestionBankItemVersion,
    row: LidEditorialItemVersion,
    collection: LidEditorialCollection,
) -> None:
    from app.question_bank.service import validate_item_payload

    exact_provenance = {
        "collection_id": str(collection.id),
        "item_id": str(version.item_id),
        "item_version_id": str(version.id),
        "item_checksum": version.checksum,
        "scope": row.scope,
        "jurisdiction": row.jurisdiction,
        "ordinal": row.ordinal,
        "supersedes_version_id": (
            str(version.supersedes_version_id) if version.supersedes_version_id else None
        ),
        "authorship": "AI_ASSISTED_DRAFT",
    }
    if (
        row.item_id != version.item_id
        or version.item.organisation_id != collection.organisation_id
        or version.item.scope_type != "ORGANISATION_PRIVATE"
        or version.assessment_family is not AssessmentFamily.INTEGRATION_KNOWLEDGE
        or version.exam_family != LID_EXAM_FAMILY
        or version.language != "de"
        or version.origin_type is not QuestionBankOriginType.AI_PROPOSED
        or version.rights_status is not QuestionBankRightsStatus.UNKNOWN
        or version.task_type is not QuestionBankTaskType.MULTIPLE_CHOICE
        or version.response_mode is not PackResponseMode.SELECT_ONE
        or version.scoring_policy is not QuestionBankScoringPolicy.OBJECTIVE_KEYED
        or version.media_requirement is not PackMediaRequirement.NONE
        or version.max_points != 1
        or (row.scope, row.jurisdiction)
        not in {("GENERAL_DE", "DE"), ("BUNDESLAND_SPECIFIC", "DE-BE")}
        or any(row.provenance.get(key) != value for key, value in exact_provenance.items())
        or row.checksum != canonical_checksum(row.provenance)
    ):
        raise EditorialConflict("LID_EDITORIAL_ITEM_PROVENANCE_INVALID")
    levels = list(
        session.scalars(
            select(QuestionBankItemCefrLevel.cefr_level).where(
                QuestionBankItemCefrLevel.item_version_id == version.id
            )
        )
    )
    try:
        content = MultipleChoiceContent.model_validate(version.content)
    except ValueError as exc:
        raise EditorialConflict("LID_EDITORIAL_ANSWER_KEY_INVALID") from exc
    option_texts = {_normal_text(option.display_text) for option in content.options}
    if (
        levels
        or len(content.options) != 4
        or len(option_texts) != 4
        or "" in option_texts
        or {option.order for option in content.options} != {0, 1, 2, 3}
        or len(content.correct_option_ids) != 1
        or content.correct_option_ids[0] not in {option.option_id for option in content.options}
        or row.provenance.get("key_checksum") != answer_key_checksum(version.content)
    ):
        raise EditorialConflict("LID_EDITORIAL_ANSWER_KEY_INVALID")
    validation = validate_item_payload(
        assessment_family=version.assessment_family,
        exam_family=version.exam_family,
        language=version.language,
        cefr_levels=levels,
        skill=version.skill,
        task_type=version.task_type,
        response_mode=version.response_mode,
        media_requirement=version.media_requirement,
        content=version.content,
        scoring_policy=version.scoring_policy,
        max_points=version.max_points,
        scoring_metadata=version.scoring_metadata,
        rights_status=version.rights_status,
        licence_reference=version.licence_reference,
        rubric_reference=version.rubric_reference,
    )
    if validation.status != "PASS" or validation.checksum != version.checksum:
        raise EditorialConflict("LID_EDITORIAL_ITEM_CHECKSUM_MISMATCH")


def _source_rights(
    session: Session,
    row: LidEditorialItemVersion,
    *,
    require_reviewed: bool,
    lock_for_review: bool = True,
) -> str | None:
    try:
        pin = row.provenance["source"]
        claim = row.provenance["claim"]
        key = pin["source_key"]
        version_id = uuid.UUID(pin["version_id"])
        metadata_checksum = pin["metadata_checksum"]
        if (
            claim["source_key"] != key
            or not claim["fact"].strip()
            or not claim["locator"].strip()
            or not claim["reference_url"].startswith("https://")
            or not row.provenance["rationale"].strip()
        ):
            raise ValueError
        today = datetime.now(ZoneInfo("Europe/Berlin")).date()
        last_verified = date.fromisoformat(claim["last_verified"])
        deadline = date.fromisoformat(claim["recheck_after"])
        if (
            last_verified > today
            or deadline <= last_verified
            or any(
                claim.get(field) and date.fromisoformat(claim[field]) > today
                for field in (
                    "applicability_as_of",
                    "legal_effective_from",
                    "historical_event_date",
                )
            )
        ):
            raise EditorialConflict("LID_EDITORIAL_SOURCE_DATE_INVALID")
        if today >= deadline:
            raise EditorialConflict("LID_EDITORIAL_SOURCE_RECHECK_REQUIRED")
    except EditorialConflict:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise EditorialConflict("LID_EDITORIAL_SOURCE_EVIDENCE_INVALID") from exc
    try:
        document = editorial_source_version(
            session,
            key,
            version_id,
            metadata_checksum,
            require_reviewed=require_reviewed,
            lock_for_review=lock_for_review,
        )
    except ValueError as exc:
        raise EditorialConflict(str(exc)) from exc
    if document.status in {KnowledgeVersionStatus.RETIRED, KnowledgeVersionStatus.SUPERSEDED}:
        raise EditorialConflict("LID_EDITORIAL_SOURCE_WITHDRAWN")
    if not require_reviewed:
        return None
    legal = document.legal_metadata
    checksum = legal.decision_checksum if legal is not None else None
    if not checksum or (pin.get("rights_decision_checksum") not in {None, checksum}):
        raise EditorialConflict("LID_EDITORIAL_SOURCE_RIGHTS_CHANGED")
    return checksum


def _qualification(
    session: Session,
    collection: LidEditorialCollection,
    actor_id: uuid.UUID,
    stage: Stage,
    identifier: uuid.UUID,
    checksum: str,
    *,
    permit_test: bool,
) -> EvaluationExaminerQualification:
    row = session.get(EvaluationExaminerQualification, identifier, populate_existing=True)
    pack = session.get(StandardPackVersion, collection.pack_version_id)
    actor = session.get(User, actor_id, populate_existing=True)
    if row is None or pack is None or actor is None:
        raise AuthorizationError("LID_EDITORIAL_QUALIFICATION_REQUIRED")
    try:
        expires_at = datetime.fromisoformat(row.body["expires_at"].replace("Z", "+00:00"))
        matches = (
            row.body["kind"] == f"LID_{stage}"
            and row.body["language"] == "de"
            and row.body["examiner_id"] == str(actor_id)
            and row.body["organisation_id"] == str(collection.organisation_id)
            and row.body["standard_pack_version_id"] == str(pack.id)
            and row.body["standard_pack_checksum"] == pack.configuration_checksum
            and row.body["rubric_version_id"] is None
            and row.body["rubric_checksum"] is None
            and row.body["origin"] == row.evidence_class
            and expires_at.tzinfo is not None
            and expires_at > datetime.now(timezone.utc)
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        matches = False
    if (
        not matches
        or row.examiner_id != actor_id
        or row.organisation_id != collection.organisation_id
        or row.status != "APPROVED"
        or row.approved_by is None
        or row.approved_by in {actor_id, row.created_by}
        or row.approved_at is None
        or not row.approval_reference
        or row.checksum != checksum
        or canonical_checksum(row.body) != checksum
        or actor.status is not UserStatus.ACTIVE
    ):
        raise AuthorizationError("LID_EDITORIAL_QUALIFICATION_REQUIRED")
    if row.evidence_class == "SYNTHETIC_TEST":
        if not permit_test or not editorial_preview_enabled(session):
            raise AuthorizationError("LID_EDITORIAL_SYNTHETIC_REVIEW_DENIED")
    elif (
        row.evidence_class != "VERIFIED_CREDENTIAL"
        or not all(
            editorial_verified_identity(session, identity)
            for identity in (actor_id, row.created_by, row.approved_by)
        )
        or not editorial_verified_reference(str(row.body.get("credential_reference", "")))
        or not editorial_verified_reference(row.approval_reference)
    ):
        raise AuthorizationError("LID_EDITORIAL_VERIFIED_HUMAN_REQUIRED")
    actual = build_tenant_context(session, actor_id, collection.organisation_id)
    if actual.membership_id is None:
        raise AuthorizationError("LID_EDITORIAL_ACTIVE_MEMBERSHIP_REQUIRED")
    AuthorizationService.require_permission(
        actual, REVIEW_PERMISSIONS[stage], collection.organisation_id
    )
    return row


def receipt_summary(row: LidEditorialReviewReceipt) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "item_version_id": str(row.item_version_id),
        "review_request_id": str(row.review_request_id),
        "operation_id": str(row.operation_id),
        "actor_user_id": str(row.actor_user_id),
        "action": row.action,
        "decision": row.decision,
        "evidence_class": row.evidence_class,
        "authority": row.authority,
        "item_checksum": row.item_checksum,
        "provenance_checksum": row.provenance_checksum,
        "recorded_at": _utc(row.recorded_at).isoformat(),
        "checksum": row.checksum,
    }


def _receipt_checksum(row: LidEditorialReviewReceipt) -> str:
    public = receipt_summary(row)
    del public["checksum"]
    return canonical_checksum(
        {
            **public,
            "qualification_id": str(row.qualification_id) if row.qualification_id else None,
            "qualification_checksum": row.qualification_checksum,
            "source_rights_checksum": row.source_rights_checksum,
            "request_checksum": row.request_checksum,
            "reason": row.reason,
            "rights": row.rights,
        }
    )


def publication_review_pins_valid(session: Session, items: list[dict[str, Any]]) -> bool:
    """Recheck the immutable receipt rows carried by an already issued publication."""
    expected: dict[uuid.UUID, tuple[uuid.UUID, str, str, str, str, str | None]] = {}
    for item in items:
        version_id = uuid.UUID(item["item_version_id"])
        for action, pin in zip(("SUBMIT", *STAGES), item["receipts"], strict=True):
            receipt_id = uuid.UUID(pin["id"])
            if receipt_id in expected:
                return False
            expected[receipt_id] = (
                version_id,
                action,
                pin["checksum"],
                item["item_checksum"],
                item["provenance_checksum"],
                item["source_rights_checksum"] if action != "SUBMIT" else None,
            )
    receipts = list(
        session.scalars(
            select(LidEditorialReviewReceipt).where(LidEditorialReviewReceipt.id.in_(expected))
        )
    )
    return len(receipts) == len(expected) and all(
        (
            row.item_version_id,
            row.action,
            row.checksum,
            row.item_checksum,
            row.provenance_checksum,
            row.source_rights_checksum,
        )
        == expected[row.id]
        and row.checksum == _receipt_checksum(row)
        and (row.decision, row.evidence_class)
        == (
            ("SUBMIT", "AUTHENTICATED_SUBMISSION")
            if row.action == "SUBMIT"
            else ("APPROVE", "VERIFIED_CREDENTIAL")
        )
        for row in receipts
    )


def _receipts(session: Session, version_id: uuid.UUID) -> list[LidEditorialReviewReceipt]:
    return list(
        session.scalars(
            select(LidEditorialReviewReceipt)
            .where(LidEditorialReviewReceipt.item_version_id == version_id)
            .order_by(
                case(
                    {"SUBMIT": 0, "FACTUAL": 1, "ASSESSMENT": 2, "RIGHTS": 3},
                    value=LidEditorialReviewReceipt.action,
                )
            )
        )
    )


def _next_stage(rows: list[LidEditorialReviewReceipt]) -> Stage | None:
    if not rows or rows[0].action != "SUBMIT" or any(row.decision == "REJECT" for row in rows):
        return None
    return next((stage for stage in STAGES if all(row.action != stage for row in rows)), None)


def _stage(version: QuestionBankItemVersion, rows: list[LidEditorialReviewReceipt]) -> str:
    if version.status in _TERMINAL_REVIEW:
        return "WITHDRAWN" if version.status is Status.RETIRED else version.status.value
    if version.status in _PUBLISHED_STATUS:
        return "PUBLISHED_PRACTICE"
    if version.status is Status.APPROVED:
        return "APPROVED_ORIGINAL_PRACTICE"
    if not rows:
        return "AI_ASSISTED_DRAFT"
    return {
        "FACTUAL": "FACT_CHECK_REQUIRED",
        "ASSESSMENT": "EXPERT_REVIEW_REQUIRED",
        "RIGHTS": "RIGHTS_REVIEW_REQUIRED",
        None: "TEST_ONLY_REVIEW_COMPLETE",
    }[_next_stage(rows)]


def _latest(session: Session, version: QuestionBankItemVersion) -> bool:
    return (
        session.scalar(
            select(QuestionBankItemVersion.id)
            .where(
                QuestionBankItemVersion.item_id == version.item_id,
                QuestionBankItemVersion.supersedes_version_id == version.id,
            )
            .limit(1)
        )
        is None
    )


def _receipt_chain_valid(
    session: Session,
    collection: LidEditorialCollection,
    version: QuestionBankItemVersion,
    provenance: LidEditorialItemVersion,
    rows: list[LidEditorialReviewReceipt],
    source_rights_checksum: str | None,
    *,
    test_only: bool,
) -> bool:
    if (
        len(rows) != 4
        or source_rights_checksum is None
        or any(
            _utc(previous.recorded_at) > _utc(next_.recorded_at)
            for previous, next_ in zip(rows, rows[1:])
        )
    ):
        return False
    submitted, *decisions = rows
    request = session.get(HumanReviewRequest, submitted.review_request_id)
    if (
        submitted.action != "SUBMIT"
        or submitted.decision != "SUBMIT"
        or submitted.evidence_class != "AUTHENTICATED_SUBMISSION"
        or submitted.authority != SUBMIT_PERMISSION
        or request is None
        or request.entity_id != version.id
        or request.entity_type != "QUESTION_BANK_ITEM_VERSION"
        or request.review_type != REVIEW_TYPE
        or request.reviewed_checksum != version.checksum
        or request.requested_by != submitted.actor_user_id
        or request.status
        not in (
            {HumanReviewStatus.PENDING, HumanReviewStatus.IN_REVIEW}
            if test_only
            else {HumanReviewStatus.APPROVED}
        )
        or len({row.actor_user_id for row in rows}) != 4
        or version.created_by_user_id in {row.actor_user_id for row in decisions}
        or version.item.created_by_user_id in {row.actor_user_id for row in decisions}
    ):
        return False
    if not test_only and (
        request.decided_by != decisions[-1].actor_user_id
        or version.approved_by_user_id != decisions[-1].actor_user_id
        or version.approved_at is None
    ):
        return False
    if any(
        row.review_request_id != submitted.review_request_id
        or row.item_checksum != version.checksum
        or row.provenance_checksum != provenance.checksum
        or _receipt_checksum(row) != row.checksum
        for row in rows
    ):
        return False
    expected_class = "SYNTHETIC_TEST" if test_only else "VERIFIED_CREDENTIAL"
    for stage, row in zip(STAGES, decisions, strict=True):
        if (
            row.action != stage
            or row.decision != "APPROVE"
            or row.evidence_class != expected_class
            or row.authority != REVIEW_PERMISSIONS[stage]
            or row.source_rights_checksum != source_rights_checksum
            or row.qualification_id is None
            or row.qualification_checksum is None
        ):
            return False
        try:
            _qualification(
                session,
                collection,
                row.actor_user_id,
                stage,
                row.qualification_id,
                row.qualification_checksum,
                permit_test=test_only,
            )
        except AuthorizationError:
            return False
    try:
        ReviewRights.model_validate(decisions[-1].rights)
    except ValueError:
        return False
    return True


def current_item_evidence(
    session: Session,
    version: QuestionBankItemVersion,
    *,
    mode: Mode,
    require_published: bool = False,
    lock_for_review: bool = True,
) -> ItemEvidence:
    row = session.get(LidEditorialItemVersion, version.id)
    collection = session.get(LidEditorialCollection, row.collection_id) if row else None
    rows = _receipts(session, version.id) if row else []
    stage = _stage(version, rows)
    blockers: list[str] = []
    preview = False
    genuine = False
    test_only = False
    current = _latest(session, version) if row is not None else False
    preview_environment = editorial_preview_enabled(session)
    if row is None or collection is None:
        blockers.append("LID_EDITORIAL_PROVENANCE_REQUIRED")
    elif not current or version.status in _TERMINAL_REVIEW:
        blockers.append("LID_EDITORIAL_ITEM_NOT_CURRENT")
        if not current:
            stage = "SUPERSEDED"
    else:
        try:
            _item_metadata(session, version, row, collection)
            _source_rights(session, row, require_reviewed=False)
            preview = True
            rights = _source_rights(
                session, row, require_reviewed=True, lock_for_review=lock_for_review
            )
            genuine = version.status in _GENUINE_STATUS and _receipt_chain_valid(
                session, collection, version, row, rows, rights, test_only=False
            )
            test_only = (
                mode == "CONTENT_EDITORIAL_PREVIEW"
                and preview_environment
                and _receipt_chain_valid(
                    session, collection, version, row, rows, rights, test_only=True
                )
            )
        except EditorialConflict as exc:
            blockers.append(str(exc))
        if not genuine:
            blockers.append("LID_EDITORIAL_GENUINE_REVIEW_REQUIRED")
    if require_published and version.status not in _PUBLISHED_STATUS:
        blockers.append("LID_EDITORIAL_ITEM_NOT_PUBLISHED")
    return {
        "eligible": (
            preview and preview_environment
            if mode == "CONTENT_EDITORIAL_PREVIEW"
            else genuine and (not require_published or version.status in _PUBLISHED_STATUS)
        ),
        "preview_eligible": preview and preview_environment,
        "genuine_approved": genuine,
        "test_approved": test_only,
        "stage": stage,
        "blockers": blockers,
        "receipts": rows,
    }


def editorial_binding_scope(
    session: Session,
    version: QuestionBankItemVersion,
    pack_id: uuid.UUID,
    task_id: uuid.UUID | None,
) -> bool:
    """Require a registered item and its own collection's exact jurisdiction task."""
    row = session.get(LidEditorialItemVersion, version.id)
    collection = session.get(LidEditorialCollection, row.collection_id) if row else None
    task = session.get(StandardPackTaskSpecification, task_id) if task_id else None
    return bool(
        row is not None
        and collection is not None
        and collection.pack_version_id == pack_id
        and task is not None
        and task.code == row.scope
        and task.section.module.standard_pack_version_id == pack_id
    )


def _trusted(
    session: Session, context: TenantContext, collection: LidEditorialCollection, permission: str
) -> TenantContext:
    if context.organisation_id not in {None, collection.organisation_id}:
        raise AuthorizationError("LID_EDITORIAL_ORGANISATION_SCOPE_REQUIRED")
    actual = build_tenant_context(session, context.user_id, collection.organisation_id)
    if actual.membership_id is None:
        raise AuthorizationError("LID_EDITORIAL_ACTIVE_MEMBERSHIP_REQUIRED")
    AuthorizationService.require_permission(actual, permission, collection.organisation_id)
    return actual


def _lock_exact_item(
    session: Session, collection: LidEditorialCollection, version_id: uuid.UUID
) -> tuple[QuestionBankItemVersion, LidEditorialItemVersion]:
    session.scalar(
        select(LidEditorialCollection.id)
        .where(LidEditorialCollection.id == collection.id)
        .with_for_update()
    )
    row = session.get(LidEditorialItemVersion, version_id)
    if row is None or row.collection_id != collection.id:
        raise ValueError("LID_EDITORIAL_ITEM_NOT_FOUND")
    session.scalar(
        select(QuestionBankItem.id).where(QuestionBankItem.id == row.item_id).with_for_update()
    )
    version = session.scalar(
        select(QuestionBankItemVersion)
        .where(QuestionBankItemVersion.id == row.item_version_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if version is None:
        raise ValueError("LID_EDITORIAL_ITEM_NOT_FOUND")
    return version, row


def _exact_replay(
    session: Session,
    context: TenantContext,
    version: QuestionBankItemVersion,
    operation_id: uuid.UUID,
    request_checksum: str,
) -> LidEditorialReviewReceipt | None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"lid-editorial-review:{operation_id}"},
        )
    row = session.scalar(
        select(LidEditorialReviewReceipt).where(
            LidEditorialReviewReceipt.operation_id == operation_id
        )
    )
    if row is not None and (
        row.actor_user_id != context.user_id
        or row.item_version_id != version.id
        or row.request_checksum != request_checksum
        or _receipt_checksum(row) != row.checksum
    ):
        raise EditorialConflict("LID_EDITORIAL_OPERATION_CONFLICT")
    if (
        row is not None
        and row.evidence_class == "SYNTHETIC_TEST"
        and not editorial_preview_enabled(session)
    ):
        raise AuthorizationError("LID_EDITORIAL_SYNTHETIC_REVIEW_DENIED")
    return row


def _record(
    session: Session,
    collection: LidEditorialCollection,
    context: TenantContext,
    version: QuestionBankItemVersion,
    editorial: LidEditorialItemVersion,
    request: HumanReviewRequest,
    operation_id: uuid.UUID,
    request_checksum: str,
    *,
    action: str,
    authority: str,
    decision: str,
    qualification: EvaluationExaminerQualification | None = None,
    rights_checksum: str | None = None,
    reason: str | None = None,
    rights: dict[str, Any] | None = None,
) -> LidEditorialReviewReceipt:
    row = LidEditorialReviewReceipt(
        id=uuid.uuid4(),
        item_version_id=version.id,
        review_request_id=request.id,
        operation_id=operation_id,
        action=action,
        decision=decision,
        authority=authority,
        evidence_class=qualification.evidence_class
        if qualification
        else "AUTHENTICATED_SUBMISSION",
        actor_user_id=context.user_id,
        item_checksum=version.checksum,
        provenance_checksum=editorial.checksum,
        request_checksum=request_checksum,
        qualification_id=qualification.id if qualification else None,
        qualification_checksum=qualification.checksum if qualification else None,
        source_rights_checksum=rights_checksum,
        reason=reason,
        rights=rights,
        recorded_at=datetime.now(timezone.utc),
        checksum="",
    )
    row.checksum = _receipt_checksum(row)
    session.add(row)
    session.flush()
    AuditService.record(
        session,
        context,
        event_type="lid.editorial.review",
        entity_type="QUESTION_BANK_ITEM_VERSION",
        entity_id=str(version.id),
        organisation_id=collection.organisation_id,
        require_audit_permission=False,
        metadata={
            "item_id": str(version.item_id),
            "checksum": version.checksum,
            "operation_id": str(operation_id),
            "decision_category": action,
            "decision": decision,
            "evidence_class": row.evidence_class,
            "source_version_id": editorial.provenance["source"]["version_id"],
        },
    )
    return row


def submit_review(
    session: Session,
    collection: LidEditorialCollection,
    context: TenantContext,
    version_id: uuid.UUID,
    payload: ReviewSubmission,
) -> LidEditorialReviewReceipt:
    from app.question_bank.service import QuestionBankService

    actual = _trusted(session, context, collection, SUBMIT_PERMISSION)
    version, row = _lock_exact_item(session, collection, version_id)
    digest = canonical_checksum({"action": "SUBMIT", **payload.model_dump(mode="json")})
    previous = _exact_replay(session, actual, version, payload.operation_id, digest)
    if previous is not None:
        return previous
    if payload.expected_checksum != version.checksum:
        raise EditorialConflict("LID_EDITORIAL_ITEM_CHECKSUM_MISMATCH")
    if version.status not in _DRAFT_STATUS or not _latest(session, version):
        raise EditorialConflict("LID_EDITORIAL_ITEM_NOT_REVIEWABLE")
    _item_metadata(session, version, row, collection)
    _source_rights(session, row, require_reviewed=False)
    request = QuestionBankService._open_review(
        session, actual, version, REVIEW_TYPE, require_audit_permission=False
    )
    return _record(
        session,
        collection,
        actual,
        version,
        row,
        request,
        payload.operation_id,
        digest,
        action="SUBMIT",
        authority=SUBMIT_PERMISSION,
        decision="SUBMIT",
    )


def _reviewer_independent(
    version: QuestionBankItemVersion, rows: list[LidEditorialReviewReceipt], actor_id: uuid.UUID
) -> bool:
    return actor_id not in {
        version.created_by_user_id,
        version.item.created_by_user_id,
        *(row.actor_user_id for row in rows),
    }


def decide_review(
    session: Session,
    collection: LidEditorialCollection,
    context: TenantContext,
    version_id: uuid.UUID,
    payload: ReviewDecision,
) -> LidEditorialReviewReceipt:
    from app.question_bank.service import QuestionBankService

    permission = REVIEW_PERMISSIONS[payload.stage]
    actual = _trusted(session, context, collection, permission)
    version, editorial = _lock_exact_item(session, collection, version_id)
    digest = canonical_checksum({"action": "DECIDE", **payload.model_dump(mode="json")})
    previous = _exact_replay(session, actual, version, payload.operation_id, digest)
    if previous is not None:
        return previous
    if payload.expected_checksum != version.checksum:
        raise EditorialConflict("LID_EDITORIAL_ITEM_CHECKSUM_MISMATCH")
    rows = _receipts(session, version.id)
    if (
        version.status is not Status.HUMAN_REVIEW
        or not _latest(session, version)
        or _next_stage(rows) != payload.stage
    ):
        raise EditorialConflict("LID_EDITORIAL_REVIEW_STAGE_MISMATCH")
    if not _reviewer_independent(version, rows, actual.user_id):
        raise AuthorizationError("LID_EDITORIAL_INDEPENDENT_REVIEW_REQUIRED")
    request = session.scalar(
        select(HumanReviewRequest)
        .where(HumanReviewRequest.id == rows[0].review_request_id)
        .with_for_update()
    )
    if (
        request is None
        or request.entity_id != version.id
        or request.review_type != REVIEW_TYPE
        or request.entity_type != "QUESTION_BANK_ITEM_VERSION"
        or request.status not in {HumanReviewStatus.PENDING, HumanReviewStatus.IN_REVIEW}
        or request.reviewed_checksum != version.checksum
        or request.requested_by != rows[0].actor_user_id
    ):
        raise EditorialConflict("LID_EDITORIAL_REVIEW_REQUEST_MISMATCH")
    qualification = _qualification(
        session,
        collection,
        actual.user_id,
        payload.stage,
        payload.qualification_id,
        payload.qualification_checksum,
        permit_test=True,
    )
    if any(row.evidence_class != qualification.evidence_class for row in rows[1:]):
        raise EditorialConflict("LID_EDITORIAL_REVIEW_CLASS_MISMATCH")
    if (
        payload.rights
        and qualification.evidence_class == "VERIFIED_CREDENTIAL"
        and not editorial_verified_reference(payload.rights.basis_reference)
    ):
        raise EditorialConflict("LID_EDITORIAL_RIGHTS_EVIDENCE_REQUIRED")
    _item_metadata(session, version, editorial, collection)
    rights_checksum = None
    if payload.decision == "APPROVE":
        rights_checksum = _source_rights(session, editorial, require_reviewed=True)
        if any(row.source_rights_checksum != rights_checksum for row in rows[1:]):
            raise EditorialConflict("LID_EDITORIAL_SOURCE_RIGHTS_CHANGED")
    receipt = _record(
        session,
        collection,
        actual,
        version,
        editorial,
        request,
        payload.operation_id,
        digest,
        action=payload.stage,
        authority=permission,
        decision=payload.decision,
        qualification=qualification,
        rights_checksum=rights_checksum,
        reason=payload.reason,
        rights=payload.rights.model_dump(mode="json") if payload.rights else None,
    )
    if payload.decision == "REJECT":
        request.status = HumanReviewStatus.REJECTED
        target = Status.REJECTED
    elif payload.stage == "RIGHTS" and qualification.evidence_class == "VERIFIED_CREDENTIAL":
        request.status = HumanReviewStatus.APPROVED
        target = Status.APPROVED
    else:
        return receipt
    request.decided_by = actual.user_id
    request.decided_at = receipt.recorded_at
    request.decision = request.status.value
    QuestionBankService._apply_transition(
        session, actual, version, target, require_audit_permission=False
    )
    if (
        target is Status.APPROVED
        and not current_item_evidence(session, version, mode="PUBLISHED_PRACTICE")[
            "genuine_approved"
        ]
    ):
        raise EditorialConflict("LID_EDITORIAL_GENUINE_REVIEW_REQUIRED")
    return receipt


def review_actions(
    session: Session,
    collection: LidEditorialCollection,
    version: QuestionBankItemVersion,
    context: TenantContext,
) -> dict[str, Any]:
    rows = _receipts(session, version.id)
    actions: list[str] = []
    qualifications: list[dict[str, Any]] = []
    response = {
        "allowed_actions": actions,
        "qualifications": qualifications,
        "review_protocol": REVIEW_PROTOCOL,
    }
    if not _latest(session, version):
        return response
    if version.status in _DRAFT_STATUS and AuthorizationService.has_permission(
        context, SUBMIT_PERMISSION, collection.organisation_id
    ):
        actions.append("SUBMIT")
    stage = _next_stage(rows)
    if (
        version.status is Status.HUMAN_REVIEW
        and stage is not None
        and _reviewer_independent(version, rows, context.user_id)
        and AuthorizationService.has_permission(
            context, REVIEW_PERMISSIONS[stage], collection.organisation_id
        )
    ):
        for candidate in session.scalars(
            select(EvaluationExaminerQualification).where(
                EvaluationExaminerQualification.examiner_id == context.user_id,
                EvaluationExaminerQualification.organisation_id == collection.organisation_id,
                EvaluationExaminerQualification.status == "APPROVED",
            )
        ):
            try:
                checked = _qualification(
                    session,
                    collection,
                    context.user_id,
                    stage,
                    candidate.id,
                    candidate.checksum,
                    permit_test=True,
                )
            except AuthorizationError:
                continue
            if any(row.evidence_class != checked.evidence_class for row in rows[1:]):
                continue
            qualifications.append(
                {
                    "id": str(checked.id),
                    "checksum": checked.checksum,
                    "stage": stage,
                    "evidence_class": checked.evidence_class,
                }
            )
        if qualifications:
            actions.append(f"REVIEW_{stage}")
    return response


def publish_approved_item(
    session: Session,
    context: TenantContext,
    version: QuestionBankItemVersion,
    *,
    pack_version_id: uuid.UUID,
) -> QuestionBankItemVersion:
    from app.question_bank.service import QuestionBankService

    editorial = session.get(LidEditorialItemVersion, version.id)
    collection = session.get(LidEditorialCollection, editorial.collection_id) if editorial else None
    pack = session.get(StandardPackVersion, pack_version_id)
    if (
        collection is None
        or collection.pack_version_id != pack_version_id
        or pack is None
        or pack.status not in {StandardPackStatus.PUBLISHED, StandardPackStatus.ACTIVE}
    ):
        raise EditorialConflict("LID_EDITORIAL_EXACT_PUBLICATION_PACK_REQUIRED")
    actual = _trusted(session, context, collection, "question_bank.publish")
    AuthorizationService.require_permission(
        actual, "standard_pack.publish", collection.organisation_id
    )
    version, _ = _lock_exact_item(session, collection, version.id)
    evidence = current_item_evidence(session, version, mode="PUBLISHED_PRACTICE")
    if version.status is not Status.APPROVED or not evidence["eligible"]:
        raise EditorialConflict("LID_EDITORIAL_GENUINE_REVIEW_REQUIRED")
    if not editorial_verified_identity(session, actual.user_id):
        raise AuthorizationError("LID_EDITORIAL_VERIFIED_HUMAN_REQUIRED")
    if actual.user_id == collection.created_by_user_id or not _reviewer_independent(
        version, evidence["receipts"], actual.user_id
    ):
        raise AuthorizationError("LID_EDITORIAL_INDEPENDENT_PUBLISHER_REQUIRED")
    bindings = list(
        session.scalars(
            select(QuestionBankStandardPackBinding).where(
                QuestionBankStandardPackBinding.item_version_id == version.id
            )
        )
    )
    if (
        len(bindings) != 1
        or not editorial_binding_scope(
            session, version, bindings[0].standard_pack_version_id, bindings[0].task_spec_id
        )
        or bindings[0].validation_status != "PASS"
    ):
        raise EditorialConflict("LID_EDITORIAL_EXACT_ITEM_BINDING_REQUIRED")
    for target in (Status.PUBLISHED, Status.ACTIVE):
        QuestionBankService._apply_transition(
            session, actual, version, target, require_audit_permission=False
        )
    return version
