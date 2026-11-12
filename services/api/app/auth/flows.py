from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.auth.passwords import hash_password, validate_password
from app.auth.security import generate_opaque_token, hash_token, is_expired, utcnow
from app.auth.service import AuthenticationService
from app.authorization import AuthorizationService, TenantContext
from app.models import (
    MembershipRole,
    OrganisationInvitation,
    OrganisationMembership,
    PasswordResetToken,
    Role,
    User,
    UserPasswordCredential,
)
from app.models.enums import InvitationStatus, MembershipStatus, RoleScope, UserStatus
from app.services.identity import normalize_email

# The existing runtime uses EXAMINER for candidate access.
CANDIDATE_ROLE = "EXAMINER"


class EnrollmentError(ValueError):
    """Fixed public outcome for a token holder."""


def _validate_password(password: str) -> None:
    try:
        validate_password(password)
    except ValueError as exc:
        raise EnrollmentError("INVALID_PASSWORD") from exc


class InvitationService:
    @staticmethod
    def create(
        session: Session,
        context: TenantContext,
        *,
        organisation_id: uuid.UUID,
        email: str,
        role_keys: list[str],
    ) -> tuple[OrganisationInvitation, str]:
        AuthorizationService.require_permission(context, "membership.manage", organisation_id)
        if role_keys != [CANDIDATE_ROLE]:
            raise EnrollmentError("INVITATION_ROLE_DENIED")
        normalized = normalize_email(email)
        if (
            session.scalar(
                select(Role.id).where(Role.key == CANDIDATE_ROLE, Role.scope == RoleScope.TENANT)
            )
            is None
        ):
            raise EnrollmentError("INVITATION_ROLE_DENIED")
        raw_token = generate_opaque_token()
        invitation = OrganisationInvitation(
            organisation_id=organisation_id,
            email_normalized=normalized,
            status=InvitationStatus.PENDING,
            expires_at=utcnow() + timedelta(days=7),
            token_hash=hash_token(raw_token),
            created_by_user_id=context.user_id,
            intended_role_keys=[CANDIDATE_ROLE],
        )
        session.add(invitation)
        session.flush()
        AuditService.record_security(
            session,
            event_type="auth.invitation.created",
            entity_type="organisation_invitation",
            entity_id=str(invitation.id),
            actor_id=context.user_id,
            metadata={"organisation_id": str(organisation_id)},
        )
        session.commit()
        return invitation, raw_token

    @staticmethod
    def accept(
        session: Session,
        *,
        token: str,
        organisation_id: uuid.UUID,
        password: str,
        display_name: str | None = None,
    ) -> User:
        invitation = session.scalar(
            select(OrganisationInvitation)
            .where(OrganisationInvitation.token_hash == hash_token(token))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if invitation is None:
            raise EnrollmentError("INVITATION_INVALID")
        if invitation.organisation_id != organisation_id:
            raise EnrollmentError("INVITATION_WRONG_INSTITUTION")
        if invitation.status is InvitationStatus.ACCEPTED:
            raise EnrollmentError("INVITATION_USED")
        if invitation.status is InvitationStatus.EXPIRED or (
            invitation.status is InvitationStatus.PENDING and is_expired(invitation.expires_at)
        ):
            raise EnrollmentError("INVITATION_EXPIRED")
        if invitation.status is not InvitationStatus.PENDING or invitation.revoked_at is not None:
            raise EnrollmentError("INVITATION_INVALID")
        if invitation.intended_role_keys != [CANDIDATE_ROLE]:
            raise EnrollmentError("INVITATION_ROLE_DENIED")
        role = session.scalar(
            select(Role).where(Role.key == CANDIDATE_ROLE, Role.scope == RoleScope.TENANT)
        )
        if role is None:
            raise EnrollmentError("INVITATION_ROLE_DENIED")
        _validate_password(password)
        if (
            session.scalar(
                select(User.id).where(User.normalized_email == invitation.email_normalized)
            )
            is not None
        ):
            raise EnrollmentError("INVITATION_ACCOUNT_EXISTS")
        user = User(
            email=invitation.email_normalized,
            normalized_email=invitation.email_normalized,
            display_name=display_name,
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        try:
            session.flush()
        except IntegrityError as exc:
            # Different invitations for the same address may race at the unique index.
            if getattr(exc.orig, "sqlstate", None) != "23505":
                raise
            session.rollback()
            raise EnrollmentError("INVITATION_ACCOUNT_EXISTS") from exc
        session.add(
            UserPasswordCredential(
                user_id=user.id, password_hash=hash_password(password), password_changed_at=utcnow()
            )
        )
        membership = OrganisationMembership(
            organisation_id=organisation_id,
            user_id=user.id,
            status=MembershipStatus.ACTIVE,
            roles=[MembershipRole(role_id=role.id)],
        )
        session.add(membership)
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = utcnow()
        AuditService.record_security(
            session,
            event_type="auth.invitation.accepted",
            entity_type="organisation_invitation",
            entity_id=str(invitation.id),
            actor_id=user.id,
            metadata={"organisation_id": str(organisation_id)},
        )
        session.commit()
        return user


class PasswordResetService:
    @staticmethod
    def request(session: Session, *, email: str) -> str | None:
        normalized = normalize_email(email)
        user = session.scalar(
            select(User)
            .where(User.normalized_email == normalized, User.status == UserStatus.ACTIVE)
            .with_for_update()
        )
        raw_token = None
        if user is not None and user.password_credential is not None:
            raw_token = generate_opaque_token()
            session.add(
                PasswordResetToken(
                    user_id=user.id,
                    token_hash=hash_token(raw_token),
                    expires_at=utcnow() + timedelta(hours=1),
                )
            )
        AuditService.record_security(
            session,
            event_type="auth.password_reset.requested",
            entity_type="user",
            actor_id=user.id if user is not None else None,
            entity_id=str(user.id) if user is not None else None,
            metadata={},
        )
        session.commit()
        return raw_token

    @staticmethod
    def confirm(
        session: Session,
        *,
        token: str,
        new_password: str,
        authentication: AuthenticationService,
    ) -> None:
        digest = hash_token(token)
        user_id = session.scalar(
            select(PasswordResetToken.user_id).where(PasswordResetToken.token_hash == digest)
        )
        if user_id is None:
            raise EnrollmentError("RESET_INVALID")
        # A single lock order serializes reset requests, password changes and logins for this user.
        user = session.scalar(select(User).where(User.id == user_id).with_for_update())
        reset = session.scalar(
            select(PasswordResetToken)
            .where(PasswordResetToken.token_hash == digest)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None or user.status is not UserStatus.ACTIVE or reset is None:
            raise EnrollmentError("RESET_INVALID")
        if reset.used_at is not None:
            raise EnrollmentError("RESET_USED")
        if reset.revoked_at is not None:
            raise EnrollmentError("RESET_INVALID")
        if is_expired(reset.expires_at):
            raise EnrollmentError("RESET_EXPIRED")
        _validate_password(new_password)
        authentication.set_password(session, user, new_password)
        now = utcnow()
        reset.used_at = now
        session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.id != reset.id,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        authentication.revoke_all(session, user.id, reason="password_reset", commit=False)
        AuditService.record_security(
            session,
            event_type="auth.password_reset.completed",
            entity_type="user",
            entity_id=str(user.id),
            actor_id=user.id,
            metadata={},
        )
        session.commit()
