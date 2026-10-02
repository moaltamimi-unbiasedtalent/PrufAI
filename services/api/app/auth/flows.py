from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.auth.passwords import hash_password
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


@dataclass
class FakeNotificationSink:
    """Safe local/test delivery sink; production adapters can implement this protocol."""

    messages: list[dict[str, str]] = field(default_factory=list)

    def send(self, *, purpose: str, email: str, token: str) -> None:
        self.messages.append({"purpose": purpose, "email": email, "token": token})


class InvitationService:
    def __init__(self, notification_sink: Optional[FakeNotificationSink] = None) -> None:
        self.notifications = notification_sink or FakeNotificationSink()

    def create(
        self,
        session: Session,
        context: TenantContext,
        *,
        organisation_id: uuid.UUID,
        email: str,
        role_keys: list[str],
        expires_in_days: int = 7,
    ) -> tuple[OrganisationInvitation, str]:
        AuthorizationService.require_permission(context, "membership.manage", organisation_id)
        normalized = normalize_email(email)
        roles = list(
            session.scalars(
                select(Role).where(Role.key.in_(role_keys), Role.scope == RoleScope.TENANT)
            )
        )
        if len(roles) != len(set(role_keys)):
            raise ValueError("invitation contains an unknown or non-tenant role")
        raw_token = generate_opaque_token()
        invitation = OrganisationInvitation(
            organisation_id=organisation_id,
            email_normalized=normalized,
            status=InvitationStatus.PENDING,
            expires_at=utcnow() + timedelta(days=expires_in_days),
            token_hash=hash_token(raw_token),
            created_by_user_id=context.user_id,
            intended_role_keys=list(dict.fromkeys(role_keys)),
        )
        session.add(invitation)
        session.flush()
        self.notifications.send(purpose="invitation", email=normalized, token=raw_token)
        AuditService.record_security(
            session,
            event_type="auth.invitation.created",
            entity_type="organisation_invitation",
            entity_id=str(invitation.id),
            actor_id=context.user_id,
            metadata={"organisation_id": str(organisation_id), "email": normalized},
        )
        session.commit()
        return invitation, raw_token

    def accept(
        self,
        session: Session,
        *,
        token: str,
        password: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> User:
        invitation = session.scalar(
            select(OrganisationInvitation).where(
                OrganisationInvitation.token_hash == hash_token(token)
            )
        )
        if invitation is None or invitation.status is not InvitationStatus.PENDING:
            raise ValueError("invalid or already used invitation")
        if is_expired(invitation.expires_at):
            invitation.status = InvitationStatus.EXPIRED
            session.commit()
            raise ValueError("invitation has expired")
        user = session.scalar(
            select(User).where(User.normalized_email == invitation.email_normalized)
        )
        if user is None:
            if password is None:
                raise ValueError("password is required for a new invited user")
            user = User(
                email=invitation.email_normalized,
                normalized_email=invitation.email_normalized,
                display_name=display_name,
                status=UserStatus.ACTIVE,
            )
            session.add(user)
            session.flush()
            credential = UserPasswordCredential(
                user_id=user.id, password_hash=hash_password(password), password_changed_at=utcnow()
            )
            session.add(credential)
        elif password is not None and user.password_credential is None:
            user.password_credential = UserPasswordCredential(
                user_id=user.id, password_hash=hash_password(password), password_changed_at=utcnow()
            )
        elif user.status in (UserStatus.SUSPENDED, UserStatus.DISABLED):
            raise ValueError("user is not eligible to accept an invitation")
        if user.status is UserStatus.INVITED:
            user.status = UserStatus.ACTIVE
        membership = session.scalar(
            select(OrganisationMembership).where(
                OrganisationMembership.organisation_id == invitation.organisation_id,
                OrganisationMembership.user_id == user.id,
            )
        )
        if membership is None:
            membership = OrganisationMembership(
                organisation_id=invitation.organisation_id,
                user_id=user.id,
                status=MembershipStatus.ACTIVE,
            )
            session.add(membership)
            session.flush()
        elif membership.status is MembershipStatus.SUSPENDED:
            raise ValueError("membership is suspended")
        for role_key in invitation.intended_role_keys:
            role = session.scalar(
                select(Role).where(Role.key == role_key, Role.scope == RoleScope.TENANT)
            )
            if role is not None and not any(
                assignment.role_id == role.id for assignment in membership.roles
            ):
                membership.roles.append(MembershipRole(role_id=role.id))
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = utcnow()
        AuditService.record_security(
            session,
            event_type="auth.invitation.accepted",
            entity_type="organisation_invitation",
            entity_id=str(invitation.id),
            actor_id=user.id,
            metadata={"organisation_id": str(invitation.organisation_id)},
        )
        session.commit()
        return user


class PasswordResetService:
    def __init__(self, notification_sink: Optional[FakeNotificationSink] = None) -> None:
        self.notifications = notification_sink or FakeNotificationSink()

    def request(self, session: Session, *, email: str, settings: object) -> Optional[str]:
        normalized = normalize_email(email)
        user = session.scalar(select(User).where(User.normalized_email == normalized))
        if user is None:
            AuditService.record_security(
                session, event_type="auth.password_reset.requested", entity_type="user", metadata={}
            )
            session.commit()
            return None
        raw_token = generate_opaque_token()
        token = PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=utcnow() + timedelta(hours=1),
        )
        session.add(token)
        self.notifications.send(purpose="password_reset", email=normalized, token=raw_token)
        AuditService.record_security(
            session,
            event_type="auth.password_reset.requested",
            entity_type="user",
            entity_id=str(user.id),
            actor_id=user.id,
            metadata={},
        )
        session.commit()
        return raw_token

    def confirm(
        self,
        session: Session,
        *,
        token: str,
        new_password: str,
        authentication: AuthenticationService,
    ) -> User:
        reset = session.scalar(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(token))
        )
        if reset is None or reset.used_at is not None or reset.revoked_at is not None:
            raise ValueError("invalid or already used password reset token")
        if is_expired(reset.expires_at):
            raise ValueError("password reset token has expired")
        user = session.get(User, reset.user_id)
        if user is None:
            raise ValueError("invalid password reset token")
        authentication.set_password(session, user, new_password)
        reset.used_at = utcnow()
        authentication.revoke_all(session, user.id, reason="password_reset")
        AuditService.record_security(
            session,
            event_type="auth.password_reset.completed",
            entity_type="user",
            entity_id=str(user.id),
            actor_id=user.id,
            metadata={},
        )
        session.commit()
        return user
