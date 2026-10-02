from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AuthSessionStatus,
    InvitationStatus,
    MembershipStatus,
    OrganisationStatus,
    OrganisationType,
    PlatformAssignmentStatus,
    RoleScope,
    SiteStatus,
    UserStatus,
)


def enum_type(enum_class: type) -> SAEnum:
    return SAEnum(enum_class, native_enum=False, validate_strings=True)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(200))
    status: Mapped[UserStatus] = mapped_column(enum_type(UserStatus), default=UserStatus.ACTIVE)

    memberships: Mapped[list[OrganisationMembership]] = relationship(back_populates="user")
    platform_assignments: Mapped[list[PlatformRoleAssignment]] = relationship(back_populates="user")
    password_credential: Mapped[Optional[UserPasswordCredential]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    auth_sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Organisation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organisations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    organisation_type: Mapped[OrganisationType] = mapped_column(
        enum_type(OrganisationType), nullable=False
    )
    status: Mapped[OrganisationStatus] = mapped_column(
        enum_type(OrganisationStatus), default=OrganisationStatus.ACTIVE, nullable=False
    )

    sites: Mapped[list[OrganisationSite]] = relationship(
        back_populates="organisation", cascade="all, delete-orphan"
    )
    memberships: Mapped[list[OrganisationMembership]] = relationship(
        back_populates="organisation", cascade="all, delete-orphan"
    )


class OrganisationSite(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organisation_sites"
    __table_args__ = (
        UniqueConstraint("organisation_id", "slug", name="uq_site_organisation_slug"),
        Index("ix_site_organisation_id", "organisation_id"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[SiteStatus] = mapped_column(
        enum_type(SiteStatus), default=SiteStatus.ACTIVE, nullable=False
    )

    organisation: Mapped[Organisation] = relationship(back_populates="sites")


class OrganisationMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organisation_memberships"
    __table_args__ = (
        UniqueConstraint("organisation_id", "user_id", name="uq_membership_organisation_user"),
        Index("ix_membership_organisation_id", "organisation_id"),
        Index("ix_membership_user_id", "user_id"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[MembershipStatus] = mapped_column(
        enum_type(MembershipStatus), default=MembershipStatus.ACTIVE, nullable=False
    )

    organisation: Mapped[Organisation] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")
    roles: Mapped[list[MembershipRole]] = relationship(
        back_populates="membership", cascade="all, delete-orphan"
    )


class Role(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "roles"

    key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    scope: Mapped[RoleScope] = mapped_column(enum_type(RoleScope), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(300))

    permissions: Mapped[list[RolePermission]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )
    membership_assignments: Mapped[list[MembershipRole]] = relationship(back_populates="role")
    platform_assignments: Mapped[list[PlatformRoleAssignment]] = relationship(back_populates="role")


class Permission(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    scope: Mapped[RoleScope] = mapped_column(enum_type(RoleScope), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(300))

    roles: Mapped[list[RolePermission]] = relationship(back_populates="permission")


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )

    role: Mapped[Role] = relationship(back_populates="permissions")
    permission: Mapped[Permission] = relationship(back_populates="roles")


class MembershipRole(Base):
    __tablename__ = "membership_roles"

    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisation_memberships.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )

    membership: Mapped[OrganisationMembership] = relationship(back_populates="roles")
    role: Mapped[Role] = relationship(back_populates="membership_assignments")


class PlatformRoleAssignment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "platform_role_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_platform_assignment_user_role"),
        Index("ix_platform_assignment_user_id", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[PlatformAssignmentStatus] = mapped_column(
        enum_type(PlatformAssignmentStatus), default=PlatformAssignmentStatus.ACTIVE, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="platform_assignments")
    role: Mapped[Role] = relationship(back_populates="platform_assignments")


class UserPasswordCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_password_credentials"
    __table_args__ = (UniqueConstraint("user_id", name="uq_password_credential_user"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[User] = relationship(back_populates="password_credential")


class AuthSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_status", "user_id", "status"),
        Index("ix_auth_sessions_refresh_hash", "refresh_token_hash"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[AuthSessionStatus] = mapped_column(
        enum_type(AuthSessionStatus), default=AuthSessionStatus.ACTIVE, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[Optional[str]] = mapped_column(String(120))
    refresh_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    refresh_token_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    refresh_generation: Mapped[int] = mapped_column(default=0, nullable=False)
    authentication_method: Mapped[str] = mapped_column(
        String(40), default="password", nullable=False
    )
    user_agent: Mapped[Optional[str]] = mapped_column(String(500))
    ip_address: Mapped[Optional[str]] = mapped_column(String(64))

    user: Mapped[User] = relationship(back_populates="auth_sessions")


class OrganisationInvitation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "organisation_invitations"
    __table_args__ = (
        Index("ix_invitations_org_status", "organisation_id", "status"),
        Index("ix_invitations_token_hash", "token_hash"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[InvitationStatus] = mapped_column(
        enum_type(InvitationStatus), default=InvitationStatus.PENDING, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    intended_role_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class PasswordResetToken(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (Index("ix_password_reset_token_hash", "token_hash"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
