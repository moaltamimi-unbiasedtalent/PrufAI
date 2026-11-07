from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.auth.security import utcnow
from app.initial_review_authority.trust import (
    Envelope,
    Manifest,
    RecoveryError,
    TrustRoot,
    require_active,
    verify,
    verify_database,
)
from app.models import (
    InitialReviewAuthority,
    Organisation,
    Permission,
    PlatformRoleAssignment,
    Role,
    RolePermission,
    ScopedSourceReviewGrant,
    User,
)
from app.models.enums import OrganisationStatus, PlatformAssignmentStatus, RoleScope, UserStatus
from app.scoped_source_review import service as scoped_review
from app.writing_assessment.service import canonical_checksum


def _event(
    session: Session,
    event: str,
    digest: str,
    manifest: Manifest | None = None,
    *,
    reason: str | None = None,
) -> None:
    AuditService.record_security(
        session,
        event_type=f"standards.initial_review.{event}",
        entity_type="initial_review_authority",
        entity_id=digest,
        actor_id=manifest.executor_id if manifest else None,
        metadata={
            **(
                {
                    "operation": manifest.action,
                    "organisation_id": str(manifest.organisation_id),
                    "approver_id": str(manifest.approver_id),
                    "beneficiary_id": str(manifest.beneficiary_id),
                    "nonce": str(manifest.nonce),
                    "scope_checksum": manifest.grant.approved_scope_checksum
                    if manifest.grant is not None
                    else None,
                    "original_manifest_digest": manifest.original_manifest_digest,
                }
                if manifest
                else {}
            ),
            **({"reason_code": reason} if reason else {}),
        },
    )


def _active_user(session: Session, identifier: UUID) -> None:
    user = session.get(User, identifier, with_for_update={"read": True}, populate_existing=True)
    if user is None or user.status != UserStatus.ACTIVE:
        raise RecoveryError("INACTIVE_NAMED_ACCOUNT")


def _require_no_existing_admin(session: Session) -> None:
    # Serialize the no-admin check with ordinary role-assignment inserts
    # and status updates without changing the existing assignment service.
    session.execute(text("LOCK TABLE platform_role_assignments IN SHARE MODE"))
    active_admin = session.scalar(
        select(PlatformRoleAssignment.id)
        .join(User, User.id == PlatformRoleAssignment.user_id)
        .join(Role, Role.id == PlatformRoleAssignment.role_id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(
            User.status == UserStatus.ACTIVE,
            Role.scope == RoleScope.PLATFORM,
            Permission.key == "platform.role.assign",
            PlatformRoleAssignment.status == PlatformAssignmentStatus.ACTIVE,
        )
        .limit(1)
    )
    if active_admin is not None:
        raise RecoveryError("EXISTING_PLATFORM_ADMINISTRATIVE_PATH")


def _establish(session: Session, envelope: Envelope, manifest: Manifest) -> str:
    _require_no_existing_admin(session)
    org = session.get(
        Organisation,
        manifest.organisation_id,
        with_for_update={"read": True},
        populate_existing=True,
    )
    if org is None or org.status != OrganisationStatus.ACTIVE:
        raise RecoveryError("ORGANISATION_UNAVAILABLE")
    grant = manifest.grant
    assert grant is not None
    people = {
        manifest.approver_id,
        manifest.executor_id,
        manifest.beneficiary_id,
        grant.reviewer_user_id,
    }
    for person in sorted(people):
        _active_user(session, person)
    for person in (manifest.beneficiary_id, grant.reviewer_user_id):
        if scoped_review._platform_roles(session, person):
            raise RecoveryError("SCOPED_ONLY_ACCOUNTS_REQUIRED")
    for pin in sorted(grant.resources, key=lambda item: item.version_id):
        for person in sorted(people):
            try:
                scoped_review._pinned_version(session, pin, person)
            except scoped_review.ScopedReviewError as exc:
                raise RecoveryError("UNAVAILABLE_PIN_OR_CONFLICTED_PARTICIPANT") from exc
    require_active(manifest, utcnow())
    if grant.expires_at <= utcnow():
        raise RecoveryError("GRANT_WINDOW_ELAPSED")
    if (
        session.scalar(
            select(ScopedSourceReviewGrant.id).where(
                (ScopedSourceReviewGrant.creation_operation_id == grant.operation_id)
                | (
                    ScopedSourceReviewGrant.business_approval_reference
                    == grant.business_approval_reference
                )
            )
        )
        is not None
    ):
        raise RecoveryError("GRANT_APPROVAL_ALREADY_USED")
    session.add(
        InitialReviewAuthority(
            id=1,
            envelope=envelope.model_dump(),
            manifest_digest=envelope.manifest_digest,
            established_at=utcnow(),
        )
    )
    _event(session, "established", envelope.manifest_digest, manifest)
    _event(session, "consumed", envelope.manifest_digest, manifest)
    return "ESTABLISHED"


def _end(
    session: Session,
    row: InitialReviewAuthority,
    original: Manifest,
    manifest: Manifest,
    envelope: Envelope,
) -> str:
    if (
        manifest.original_manifest_digest != row.manifest_digest
        or manifest.beneficiary_id != original.beneficiary_id
        or manifest.nonce == original.nonce
    ):
        raise RecoveryError("TERMINATION_TARGET_MISMATCH")
    grant_request = original.grant
    assert grant_request is not None
    grant = session.scalar(
        select(ScopedSourceReviewGrant)
        .where(
            ScopedSourceReviewGrant.creation_operation_id == grant_request.operation_id,
            ScopedSourceReviewGrant.organisation_id == manifest.organisation_id,
            ScopedSourceReviewGrant.authorized_by == manifest.beneficiary_id,
            ScopedSourceReviewGrant.scope_checksum == grant_request.approved_scope_checksum,
        )
        .with_for_update()
    )
    require_active(manifest, utcnow())
    row.ended_at = utcnow()
    row.end_action = manifest.action
    row.end_digest = envelope.manifest_digest
    if grant is not None and grant.status in (*scoped_review.OPEN_STATUSES, "SUSPENDED"):
        grant.status = "REVOKED"
        scoped_review.audit(
            session,
            manifest.executor_id,
            "revoked",
            grant=grant,
            operation_id=manifest.nonce,
            operation="INITIAL_AUTHORITY_ENDED",
        )
    _event(session, "ended", envelope.manifest_digest, manifest)
    return "ENDED"


def _apply(
    session: Session, envelope: Envelope, manifest: Manifest, refresh_trust: Callable[[], None]
) -> str:
    # A database-wide one-time authority, including when the table is empty.
    session.execute(text("SELECT pg_catalog.pg_advisory_xact_lock(5042, 71101)"))
    row = session.get(InitialReviewAuthority, 1, with_for_update=True, populate_existing=True)
    refresh_trust()
    if row is not None and envelope.manifest_digest in {row.manifest_digest, row.end_digest}:
        if row.ended_at is not None:
            return "ALREADY_ENDED"
        if utcnow() >= manifest.expires_at:
            return "ALREADY_EXPIRED"
        return "ALREADY_ESTABLISHED"
    require_active(manifest, utcnow())
    if manifest.action == "ESTABLISH":
        if row is not None:
            raise RecoveryError("INITIAL_AUTHORITY_ALREADY_CONSUMED")
        return _establish(session, envelope, manifest)
    if row is None or row.ended_at is not None:
        raise RecoveryError("INITIAL_AUTHORITY_UNAVAILABLE")
    # Original proof was verified on insertion and is sealed by PostgreSQL.
    # Termination must remain possible when the original nonce was revoked.
    original_envelope = Envelope.model_validate(row.envelope)
    if canonical_checksum(original_envelope.manifest) != row.manifest_digest:
        raise RecoveryError("ORIGINAL_MANIFEST_DIGEST_MISMATCH")
    original = Manifest.model_validate(original_envelope.manifest)
    return _end(session, row, original, manifest, envelope)


def execute(
    session: Session,
    trust: TrustRoot,
    envelope: Envelope,
    *,
    dry_run: bool,
    reload_trust: Callable[[], TrustRoot] | None = None,
) -> str:
    verify_database(session, trust.target)

    def refresh() -> None:
        if reload_trust is not None and reload_trust() != trust:
            raise RecoveryError("TRUST_ROOT_CHANGED_DURING_OPERATION")

    manifest = None
    try:
        manifest = verify(envelope, trust)
        if not dry_run:
            _event(session, "prepared", envelope.manifest_digest, manifest)
            _event(session, "requested", envelope.manifest_digest, manifest)
            _event(session, "environment_verified", envelope.manifest_digest, manifest)
            _event(session, "approval_verified", envelope.manifest_digest, manifest)
        with session.begin_nested():
            result = _apply(session, envelope, manifest, refresh)
            row = session.get(InitialReviewAuthority, 1, populate_existing=True)
            if row is None or envelope.manifest_digest not in {row.manifest_digest, row.end_digest}:
                raise RecoveryError("RECOVERY_POSTCONDITION_FAILED")
            if not dry_run:
                _event(session, "verified", envelope.manifest_digest, manifest)
    except (RecoveryError, SQLAlchemyError) as exc:
        if not dry_run:
            _event(
                session,
                "rejected" if isinstance(exc, RecoveryError) else "failed",
                envelope.manifest_digest,
                manifest,
                reason=str(exc) if isinstance(exc, RecoveryError) else "TRANSACTION_FAILED",
            )
            session.commit()
        raise
    if dry_run:
        session.rollback()
    else:
        session.commit()
    return result
