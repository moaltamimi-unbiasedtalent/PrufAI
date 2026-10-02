from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import FrozenSet, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization.errors import AuthorizationError
from app.models import (
    MembershipRole,
    OrganisationMembership,
    Permission,
    PlatformRoleAssignment,
    Role,
    RolePermission,
    User,
)
from app.models.enums import MembershipStatus, PlatformAssignmentStatus, RoleScope, UserStatus


@dataclass(frozen=True)
class TenantContext:
    """Trusted authorization input constructed by application code or tests.

    Authentication is intentionally out of scope. Production callers must obtain this
    context from a future authenticated session rather than arbitrary HTTP fields.
    """

    user_id: uuid.UUID
    organisation_id: Optional[uuid.UUID]
    membership_id: Optional[uuid.UUID]
    permissions: FrozenSet[str] = frozenset()
    platform_permissions: FrozenSet[str] = frozenset()

    @property
    def platform_scope(self) -> bool:
        return bool(self.platform_permissions)


def _permission_keys_for_role_ids(session: Session, role_ids: list[uuid.UUID]) -> set[str]:
    if not role_ids:
        return set()
    rows = session.execute(
        select(Permission.key)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id.in_(role_ids))
    )
    return {row[0] for row in rows}


def build_tenant_context(
    session: Session, user_id: uuid.UUID, organisation_id: Optional[uuid.UUID] = None
) -> TenantContext:
    user = session.get(User, user_id)
    if user is None or user.status is not UserStatus.ACTIVE:
        raise AuthorizationError("user is not active")

    platform_role_ids = list(
        session.scalars(
            select(PlatformRoleAssignment.role_id)
            .join(Role, Role.id == PlatformRoleAssignment.role_id)
            .where(
                PlatformRoleAssignment.user_id == user_id,
                PlatformRoleAssignment.status == PlatformAssignmentStatus.ACTIVE,
                Role.scope == RoleScope.PLATFORM,
            )
        )
    )
    platform_permissions = _permission_keys_for_role_ids(session, platform_role_ids)

    membership_id: Optional[uuid.UUID] = None
    tenant_permissions: set[str] = set()
    if organisation_id is not None:
        membership = session.scalar(
            select(OrganisationMembership).where(
                OrganisationMembership.organisation_id == organisation_id,
                OrganisationMembership.user_id == user_id,
            )
        )
        if membership is not None and membership.status is MembershipStatus.ACTIVE:
            membership_id = membership.id
            role_ids = list(
                session.scalars(
                    select(MembershipRole.role_id)
                    .join(Role, Role.id == MembershipRole.role_id)
                    .where(
                        MembershipRole.membership_id == membership.id,
                        Role.scope == RoleScope.TENANT,
                    )
                )
            )
            tenant_permissions = _permission_keys_for_role_ids(session, role_ids)
        elif not platform_permissions:
            raise AuthorizationError("active organisation membership required")
    elif not platform_permissions:
        raise AuthorizationError("organisation context required")

    return TenantContext(
        user_id=user_id,
        organisation_id=organisation_id,
        membership_id=membership_id,
        permissions=frozenset(tenant_permissions),
        platform_permissions=frozenset(platform_permissions),
    )
