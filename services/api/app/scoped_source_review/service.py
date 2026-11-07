from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.auth.api import Principal
from app.auth.security import utcnow
from app.authorization import AuthorizationError, build_tenant_context
from app.initial_review_authority.runtime import require_exact_initial_grant
from app.initial_review_authority.trust import RecoveryError
from app.models import (
    AuditEvent,
    AuthSession,
    KnowledgeAuthority,
    KnowledgeDocumentVersion,
    KnowledgeSource,
    Organisation,
    PlatformRoleAssignment,
    ScopedSourceReviewGrant,
    ScopedSourceReviewOperation,
    User,
)
from app.models.enums import (
    AuthSessionStatus,
    KnowledgeScope,
    KnowledgeVersionStatus,
    OrganisationStatus,
    PlatformAssignmentStatus,
    UserStatus,
)
from app.scoped_source_review.schemas import (
    CreateGrant,
    Operation,
    ResourcePin,
    ReviewDecision,
    grant_scope_checksum,
    timestamp,
)
from app.services.knowledge import (
    KnowledgeRegistryService,
    SourceConflict,
    source_metadata_checksum,
)
from app.writing_assessment.service import canonical_checksum

Grant = ScopedSourceReviewGrant
Receipt = ScopedSourceReviewOperation
OPEN_STATUSES = ("APPROVED", "ACTIVE")


class ScopedReviewError(Exception):
    def __init__(self, code: str, status: int = 403) -> None:
        self.code = code
        self.status = status


def audit(
    session: Session,
    actor_id: UUID,
    event: str,
    *,
    grant: Grant | None = None,
    grant_id: UUID | None = None,
    version_id: UUID | None = None,
    pin: str | None = None,
    operation_id: UUID | None = None,
    reason: str | None = None,
    operation: str | None = None,
) -> None:
    identifier = grant.id if grant is not None else grant_id
    AuditService.record_security(
        session,
        actor_id=actor_id,
        event_type=f"standards.scoped_review.{event}",
        entity_type="scoped_source_review_grant",
        entity_id=str(identifier) if identifier else None,
        metadata={
            "outcome": "DENIED" if reason else "RECORDED",
            **({"organisation_id": str(grant.organisation_id)} if grant is not None else {}),
            **({"source_version_id": str(version_id)} if version_id else {}),
            **({"metadata_checksum": pin} if pin else {}),
            **({"operation_id": str(operation_id)} if operation_id else {}),
            **({"operation": operation} if operation else {}),
            **({"reason_code": reason} if reason else {}),
        },
    )


def _active_actor(session: Session, principal: Principal) -> UUID:
    account = session.get(
        User, principal.user.id, with_for_update={"read": True}, populate_existing=True
    )
    auth = session.get(
        AuthSession, principal.session.id, with_for_update=True, populate_existing=True
    )
    now = utcnow()
    if (
        account is None
        or account.status is not UserStatus.ACTIVE
        or auth is None
        or auth.user_id != account.id
        or auth.status is not AuthSessionStatus.ACTIVE
        or auth.expires_at <= now
        or auth.refresh_token_expires_at <= now
    ):
        raise ScopedReviewError("SESSION_OR_ACCOUNT_UNAVAILABLE")
    return account.id


def _platform_roles(session: Session, user_id: UUID) -> Sequence[UUID]:
    return session.scalars(
        select(PlatformRoleAssignment.id)
        .where(
            PlatformRoleAssignment.user_id == user_id,
            PlatformRoleAssignment.status == PlatformAssignmentStatus.ACTIVE,
        )
        .order_by(PlatformRoleAssignment.id)
        .with_for_update(read=True)
    ).all()


def _issuer(session: Session, actor: UUID) -> None:
    _platform_roles(session, actor)
    try:
        context = build_tenant_context(session, actor)
    except AuthorizationError as exc:
        raise ScopedReviewError("GRANT_AUTHORITY_REQUIRED") from exc
    if "platform.role.assign" not in context.platform_permissions:
        raise ScopedReviewError("GRANT_AUTHORITY_REQUIRED")


def _organisation(session: Session, organisation_id: UUID, *, issue: bool = False) -> None:
    organisation = session.get(
        Organisation,
        organisation_id,
        with_for_update=True if issue else {"read": True},
        populate_existing=True,
    )
    if organisation is None or organisation.status is not OrganisationStatus.ACTIVE:
        raise ScopedReviewError("ORGANISATION_UNAVAILABLE")


def _observe(session: Session, grant: Grant, actor: UUID, now: datetime) -> bool:
    if grant.status not in OPEN_STATUSES:
        return False
    if now >= grant.expires_at:
        grant.status = "EXPIRED"
        audit(session, actor, "expired", grant=grant)
        return False
    if now < grant.starts_at:
        return False
    if grant.status == "APPROVED":
        grant.status = "ACTIVE"
        audit(session, actor, "activated", grant=grant)
    return True


def _owned_grant(session: Session, actor: UUID, organisation_id: UUID, grant_id: UUID) -> Grant:
    grant = session.scalar(
        select(Grant)
        .where(
            Grant.id == grant_id,
            Grant.organisation_id == organisation_id,
            Grant.reviewer_user_id == actor,
        )
        .with_for_update()
    )
    if grant is None:
        raise ScopedReviewError("GRANT_UNAVAILABLE")
    _organisation(session, organisation_id)
    if _platform_roles(session, actor):
        raise ScopedReviewError("SCOPED_ONLY_ACCOUNT_REQUIRED")
    if not _observe(session, grant, actor, utcnow()):
        raise ScopedReviewError("GRANT_UNAVAILABLE")
    return grant


def _pinned_version(
    session: Session, pin: ResourcePin, reviewer: UUID, *, write: bool = False
) -> KnowledgeDocumentVersion:
    version = session.get(
        KnowledgeDocumentVersion,
        pin.version_id,
        with_for_update=True if write else {"read": True},
        populate_existing=True,
    )
    if (
        version is None
        or version.status is not KnowledgeVersionStatus.DRAFT
        or version.content_mode != "METADATA_ONLY"
        or version.storage_key is not None
        or version.created_by_user_id is None
        or version.metadata_checksum != pin.metadata_checksum
        or source_metadata_checksum(version) != pin.metadata_checksum
        or version.legal_metadata is None
    ):
        raise ScopedReviewError("PIN_UNAVAILABLE")
    if (
        session.scalar(
            select(KnowledgeDocumentVersion.id)
            .where(
                KnowledgeDocumentVersion.supersedes_document_version_id == version.id,
                KnowledgeDocumentVersion.status.not_in(
                    (KnowledgeVersionStatus.RETIRED, KnowledgeVersionStatus.REJECTED)
                ),
            )
            .limit(1)
        )
        is not None
    ):
        raise ScopedReviewError("PIN_SUPERSEDED")
    if version.created_by_user_id == reviewer:
        raise ScopedReviewError("INDEPENDENT_REVIEWER_REQUIRED")
    source = session.get(
        KnowledgeSource, version.source_id, with_for_update={"read": True}, populate_existing=True
    )
    if (
        source is None
        or source.scope is not KnowledgeScope.PLATFORM_SHARED
        or source.status != "ACTIVE"
    ):
        raise ScopedReviewError("PIN_UNAVAILABLE")
    authority = session.get(
        KnowledgeAuthority,
        source.authority_id,
        with_for_update={"read": True},
        populate_existing=True,
    )
    if authority is None or authority.status != "ACTIVE":
        raise ScopedReviewError("PIN_UNAVAILABLE")
    authors = set(
        session.scalars(
            select(AuditEvent.actor_id).where(
                AuditEvent.event_type == "standards.source.created",
                AuditEvent.entity_type == "knowledge_source",
                AuditEvent.entity_id == str(source.id),
                AuditEvent.actor_id.is_not(None),
            )
        )
    )
    if not authors or reviewer in authors:
        raise ScopedReviewError("INDEPENDENT_REVIEWER_REQUIRED")
    return version


def _display_metadata(version: KnowledgeDocumentVersion) -> dict[str, str | None]:
    return {
        "title": version.source.title,
        "publisher": version.source.authority.name,
        "version_label": version.version_label,
        "publication_date": timestamp(version.publication_date)
        if version.publication_date
        else None,
        "effective_from": timestamp(version.effective_from) if version.effective_from else None,
        "effective_until": timestamp(version.effective_until) if version.effective_until else None,
    }


def _check_pin(
    session: Session, resource: dict[str, Any], actor: UUID, *, write: bool = False
) -> tuple[ResourcePin, KnowledgeDocumentVersion]:
    pin = ResourcePin.model_validate(
        {key: value for key, value in resource.items() if key != "display_checksum"}
    )
    version = _pinned_version(session, pin, actor, write=write)
    if resource["display_checksum"] != canonical_checksum(_display_metadata(version)):
        raise ScopedReviewError("PIN_UNAVAILABLE")
    return pin, version


def _project(pin: ResourcePin, version: KnowledgeDocumentVersion) -> dict[str, object]:
    legal = version.legal_metadata
    assert legal is not None
    metadata = {**_display_metadata(version), "rights_status": legal.legal_review_status.value}
    return {
        "version_id": str(version.id),
        "metadata_checksum": pin.metadata_checksum,
        "metadata": {key: metadata[key] for key in sorted(pin.metadata_fields)},
        "allowed_decisions": sorted(pin.allowed_decisions)
        if legal.decision_checksum is None
        else [],
    }


def summary(grant: Grant) -> dict[str, object]:
    return {
        "grant_id": str(grant.id),
        "organisation_id": str(grant.organisation_id),
        "starts_at": timestamp(grant.starts_at),
        "expires_at": timestamp(grant.expires_at),
        "status": grant.status,
        "scope_checksum": grant.scope_checksum,
    }


def _replay(
    session: Session, operation_id: UUID, actor: UUID, action: str, checksum: str
) -> Receipt | None:
    receipt = session.get(Receipt, operation_id)
    if receipt is not None and (
        receipt.actor_id != actor
        or receipt.action != action
        or receipt.request_checksum != checksum
    ):
        raise ScopedReviewError("OPERATION_REPLAY_CONFLICT", 409)
    return receipt


def _receipt(
    session: Session, grant: Grant, operation_id: UUID, actor: UUID, action: str, checksum: str
) -> None:
    session.add(
        Receipt(
            operation_id=operation_id,
            grant_id=grant.id,
            actor_id=actor,
            action=action,
            request_checksum=checksum,
        )
    )


def _require_initial_grant(
    session: Session, actor: UUID, organisation_id: UUID, payload: CreateGrant
) -> None:
    try:
        if _platform_roles(session, actor):
            raise RecoveryError("SCOPED_ONLY_ISSUER_REQUIRED")
        require_exact_initial_grant(session, actor, organisation_id, payload)
    except RecoveryError as exc:
        raise ScopedReviewError("GRANT_AUTHORITY_REQUIRED") from exc


def issue(
    session: Session, principal: Principal, organisation_id: UUID, payload: CreateGrant
) -> dict[str, object]:
    actor = _active_actor(session, principal)
    audit(session, actor, "requested", operation_id=payload.operation_id, operation="ISSUE")
    try:
        _issuer(session, actor)
        initial = False
    except ScopedReviewError:
        initial = True
        _require_initial_grant(session, actor, organisation_id, payload)
    _organisation(session, organisation_id, issue=True)
    pins = sorted(payload.resources, key=lambda pin: pin.version_id)
    scope_checksum = grant_scope_checksum(organisation_id, actor, payload)
    if scope_checksum != payload.approved_scope_checksum:
        raise ScopedReviewError("APPROVED_SCOPE_CHECKSUM_MISMATCH", 409)
    request_checksum = canonical_checksum(
        {
            "scope_checksum": scope_checksum,
            "business_approval_reference": payload.business_approval_reference,
        }
    )
    replay = _replay(session, payload.operation_id, actor, "ISSUE", request_checksum)
    if replay is not None:
        existing = session.get(Grant, replay.grant_id)
        assert existing is not None
        return summary(existing)
    if (
        session.scalar(
            select(Grant.id).where(
                Grant.business_approval_reference == payload.business_approval_reference
            )
        )
        is not None
    ):
        raise ScopedReviewError("APPROVAL_REFERENCE_ALREADY_USED", 409)
    reviewer = session.get(
        User, payload.reviewer_user_id, with_for_update={"read": True}, populate_existing=True
    )
    if reviewer is None or reviewer.status is not UserStatus.ACTIVE or reviewer.id == actor:
        raise ScopedReviewError("INDEPENDENT_ACTIVE_REVIEWER_REQUIRED")
    if _platform_roles(session, reviewer.id):
        raise ScopedReviewError("SCOPED_ONLY_ACCOUNT_REQUIRED")
    if session.scalar(
        select(Grant.id)
        .where(
            Grant.organisation_id == organisation_id,
            Grant.reviewer_user_id == reviewer.id,
            Grant.status.in_(OPEN_STATUSES),
            Grant.starts_at < payload.expires_at,
            Grant.expires_at > payload.starts_at,
            Grant.expires_at > utcnow(),
        )
        .limit(1)
    ):
        raise ScopedReviewError("OVERLAPPING_GRANT", 409)
    resources = []
    for pin in pins:
        version = _pinned_version(session, pin, reviewer.id)
        resources.append(
            {**pin.canonical(), "display_checksum": canonical_checksum(_display_metadata(version))}
        )
    if initial:
        _require_initial_grant(session, actor, organisation_id, payload)
    now = utcnow()
    if now >= payload.expires_at:
        raise ScopedReviewError("GRANT_WINDOW_ELAPSED", 409)
    grant = Grant(
        reviewer_user_id=reviewer.id,
        organisation_id=organisation_id,
        authorized_by=actor,
        creation_operation_id=payload.operation_id,
        purpose=payload.purpose,
        business_approval_reference=payload.business_approval_reference,
        starts_at=payload.starts_at,
        expires_at=payload.expires_at,
        created_at=now,
        status="APPROVED",
        scope_checksum=scope_checksum,
        resources=resources,
    )
    session.add(grant)
    session.flush()
    _receipt(session, grant, payload.operation_id, actor, "ISSUE", request_checksum)
    audit(
        session, actor, "created", grant=grant, operation_id=payload.operation_id, operation="ISSUE"
    )
    _observe(session, grant, actor, now)
    return summary(grant)


def end_access(
    session: Session,
    principal: Principal,
    organisation_id: UUID,
    grant_id: UUID,
    payload: Operation,
    action: str,
) -> dict[str, object]:
    actor = _active_actor(session, principal)
    _issuer(session, actor)
    grant = session.scalar(
        select(Grant)
        .where(Grant.id == grant_id, Grant.organisation_id == organisation_id)
        .with_for_update()
    )
    if grant is None:
        raise ScopedReviewError("GRANT_UNAVAILABLE")
    checksum = canonical_checksum({"grant_id": str(grant_id), "action": action})
    if _replay(session, payload.operation_id, actor, action, checksum) is not None:
        return summary(grant)
    _observe(session, grant, actor, utcnow())
    permitted = (*OPEN_STATUSES, "SUSPENDED") if action == "REVOKE" else OPEN_STATUSES
    if grant.status not in permitted:
        raise ScopedReviewError("GRANT_TRANSITION_UNAVAILABLE", 409)
    grant.status = "REVOKED" if action == "REVOKE" else "SUSPENDED"
    _receipt(session, grant, payload.operation_id, actor, action, checksum)
    audit(
        session,
        actor,
        grant.status.lower(),
        grant=grant,
        operation_id=payload.operation_id,
        operation=action,
    )
    return summary(grant)


def owned_grants(session: Session, principal: Principal) -> list[dict[str, object]]:
    actor = _active_actor(session, principal)
    if _platform_roles(session, actor):
        return []
    grants = session.scalars(
        select(Grant)
        .join(Organisation, Organisation.id == Grant.organisation_id)
        .where(
            Grant.reviewer_user_id == actor,
            Grant.status.in_(OPEN_STATUSES),
            Organisation.status == OrganisationStatus.ACTIVE,
        )
        .order_by(Grant.organisation_id, Grant.id)
        .with_for_update(of=Grant)
    ).all()
    active = []
    for grant in grants:
        try:
            _organisation(session, grant.organisation_id)
        except ScopedReviewError:
            continue
        if _observe(session, grant, actor, utcnow()):
            active.append(summary(grant))
    return active


def inspect_grant(
    session: Session, principal: Principal, organisation_id: UUID, grant_id: UUID
) -> dict[str, object]:
    actor = _active_actor(session, principal)
    grant = _owned_grant(session, actor, organisation_id, grant_id)
    sources = []
    for resource in grant.resources:
        try:
            pin, version = _check_pin(session, resource, actor)
        except ScopedReviewError:
            continue
        sources.append(_project(pin, version))
    if not _observe(session, grant, actor, utcnow()):
        raise ScopedReviewError("GRANT_UNAVAILABLE")
    audit(session, actor, "inspected", grant=grant, operation="LIST")
    return {**summary(grant), "sources": sources}


def _resource(grant: Grant, version_id: UUID) -> dict[str, Any]:
    for resource in grant.resources:
        if resource["version_id"] == str(version_id):
            return resource
    raise ScopedReviewError("PIN_UNAVAILABLE")


def inspect_source(
    session: Session, principal: Principal, organisation_id: UUID, grant_id: UUID, version_id: UUID
) -> dict[str, object]:
    actor = _active_actor(session, principal)
    grant = _owned_grant(session, actor, organisation_id, grant_id)
    pin, version = _check_pin(session, _resource(grant, version_id), actor)
    if not _observe(session, grant, actor, utcnow()):
        raise ScopedReviewError("GRANT_UNAVAILABLE")
    audit(
        session,
        actor,
        "inspected",
        grant=grant,
        version_id=version_id,
        pin=pin.metadata_checksum,
        operation="DETAIL",
    )
    return _project(pin, version)


def decide(
    session: Session,
    principal: Principal,
    organisation_id: UUID,
    grant_id: UUID,
    version_id: UUID,
    payload: ReviewDecision,
) -> dict[str, object]:
    actor = _active_actor(session, principal)
    grant = _owned_grant(session, actor, organisation_id, grant_id)
    pin, version = _check_pin(session, _resource(grant, version_id), actor, write=True)
    if payload.decision not in pin.allowed_decisions:
        raise ScopedReviewError("DECISION_OUT_OF_SCOPE")
    if payload.expected_metadata_checksum != pin.metadata_checksum:
        raise ScopedReviewError("PIN_CHECKSUM_MISMATCH", 409)
    args = payload.model_dump(exclude={"operation_id"})
    checksum = canonical_checksum(
        {"grant_id": str(grant_id), "version_id": str(version_id), **args}
    )
    replay = _replay(session, payload.operation_id, actor, "DECIDE", checksum)
    _active_actor(session, principal)
    if not _observe(session, grant, actor, utcnow()):
        raise ScopedReviewError("GRANT_UNAVAILABLE")
    legal = version.legal_metadata
    assert legal is not None
    if replay is not None and legal.decision_checksum is not None:
        return {**_project(pin, version), "decision_checksum": legal.decision_checksum}
    if legal.decision_checksum is not None or replay is not None:
        raise ScopedReviewError("SOURCE_RIGHTS_DECISION_IMMUTABLE", 409)
    try:
        legal = KnowledgeRegistryService._record_rights_decision(
            session, version, actor_id=actor, scoped_grant_id=grant.id, **args
        )
    except SourceConflict as exc:
        raise ScopedReviewError("SOURCE_RIGHTS_CONFLICT", 409) from exc
    _receipt(session, grant, payload.operation_id, actor, "DECIDE", checksum)
    audit(
        session,
        actor,
        "review_submitted",
        grant=grant,
        version_id=version_id,
        pin=pin.metadata_checksum,
        operation_id=payload.operation_id,
        operation=payload.decision,
    )
    return {**_project(pin, version), "decision_checksum": legal.decision_checksum}
