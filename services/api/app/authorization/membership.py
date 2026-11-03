"""Institution evidence authority requires a real tenant membership."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization.context import TenantContext
from app.authorization.errors import AuthorizationError
from app.models import OrganisationMembership
from app.models.enums import MembershipStatus


def require_active_tenant_permission(
    session: Session,
    context: TenantContext,
    permission: str,
    organisation_id: uuid.UUID | None = None,
) -> None:
    """Do not substitute platform oversight for institution evidence access."""
    target = organisation_id or context.organisation_id
    if (
        target is None
        or context.organisation_id != target
        or context.membership_id is None
        or permission not in context.permissions
    ):
        raise AuthorizationError("active institution membership and permission required")
    membership = session.scalar(
        select(OrganisationMembership.id).where(
            OrganisationMembership.id == context.membership_id,
            OrganisationMembership.organisation_id == target,
            OrganisationMembership.user_id == context.user_id,
            OrganisationMembership.status == MembershipStatus.ACTIVE,
        )
    )
    if membership is None:
        raise AuthorizationError("active institution membership and permission required")
