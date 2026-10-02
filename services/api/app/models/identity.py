from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
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
