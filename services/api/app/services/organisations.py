import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationService, TenantContext
from app.models import (
    MembershipRole,
    Organisation,
    OrganisationMembership,
    OrganisationSite,
    PlatformRoleAssignment,
    Role,
)
from app.models.enums import (
    MembershipStatus,
    OrganisationType,
    PlatformAssignmentStatus,
    RoleScope,
    SiteStatus,
)


def normalize_slug(slug: str) -> str:
    normalized = slug.strip().casefold()
    if not normalized or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in normalized
    ):
        raise ValueError("slug must contain only letters, numbers, hyphens, or underscores")
    return normalized


class OrganisationService:
    """Tenant-scoped operations with centralized authorization and audit writes."""

    @staticmethod
    def create_organisation(
        session: Session,
        context: TenantContext,
        *,
        name: str,
        slug: str,
        organisation_type: OrganisationType,
    ) -> Organisation:
        AuthorizationService.require_permission(context, "platform.organisation.manage")
        normalized_slug = normalize_slug(slug)
        if (
            session.scalar(select(Organisation).where(Organisation.slug == normalized_slug))
            is not None
        ):
            raise ValueError("organisation slug already exists")
        organisation = Organisation(
            name=name.strip(), slug=normalized_slug, organisation_type=organisation_type
        )
        session.add(organisation)
        session.flush()
        AuditService.record(
            session,
            context,
            event_type="organisation.created",
            entity_type="organisation",
            entity_id=str(organisation.id),
            organisation_id=organisation.id,
            metadata={"slug": organisation.slug, "organisation_type": organisation_type.value},
        )
        session.commit()
        session.refresh(organisation)
        return organisation

    @staticmethod
    def create_site(
        session: Session,
        context: TenantContext,
        *,
        organisation_id: uuid.UUID,
        name: str,
        slug: str,
        status: SiteStatus = SiteStatus.ACTIVE,
    ) -> OrganisationSite:
        AuthorizationService.require_permission(context, "site.manage", organisation_id)
        site = OrganisationSite(
            organisation_id=organisation_id,
            name=name.strip(),
            slug=normalize_slug(slug),
            status=status,
        )
        session.add(site)
        session.flush()
        AuditService.record(
            session,
            context,
            event_type="organisation_site.created",
            entity_type="organisation_site",
            entity_id=str(site.id),
            organisation_id=organisation_id,
            metadata={"slug": site.slug},
        )
        session.commit()
        session.refresh(site)
        return site

    @staticmethod
    def list_sites(
        session: Session, context: TenantContext, *, organisation_id: uuid.UUID
    ) -> list[OrganisationSite]:
        AuthorizationService.require_permission(context, "site.read", organisation_id)
        return list(
            session.scalars(
                select(OrganisationSite)
                .where(OrganisationSite.organisation_id == organisation_id)
                .order_by(OrganisationSite.slug)
            )
        )

    @staticmethod
    def create_membership(
        session: Session,
        context: TenantContext,
        *,
        organisation_id: uuid.UUID,
        user_id: uuid.UUID,
        status: MembershipStatus = MembershipStatus.ACTIVE,
    ) -> OrganisationMembership:
        AuthorizationService.require_permission(context, "membership.manage", organisation_id)
        existing = session.scalar(
            select(OrganisationMembership).where(
                OrganisationMembership.organisation_id == organisation_id,
                OrganisationMembership.user_id == user_id,
            )
        )
        if existing is not None:
            raise ValueError("organisation membership already exists")
        membership = OrganisationMembership(
            organisation_id=organisation_id, user_id=user_id, status=status
        )
        session.add(membership)
        session.flush()
        AuditService.record(
            session,
            context,
            event_type="membership.created",
            entity_type="organisation_membership",
            entity_id=str(membership.id),
            organisation_id=organisation_id,
            metadata={"user_id": str(user_id), "status": status.value},
        )
        session.commit()
        session.refresh(membership)
        return membership

    @staticmethod
    def change_membership_status(
        session: Session,
        context: TenantContext,
        *,
        membership_id: uuid.UUID,
        status: MembershipStatus,
    ) -> OrganisationMembership:
        membership = session.get(OrganisationMembership, membership_id)
        if membership is None:
            raise ValueError("membership not found")
        AuthorizationService.require_permission(
            context, "membership.manage", membership.organisation_id
        )
        membership.status = status
        AuditService.record(
            session,
            context,
            event_type="membership.status_changed",
            entity_type="organisation_membership",
            entity_id=str(membership.id),
            organisation_id=membership.organisation_id,
            metadata={"status": status.value},
        )
        session.commit()
        session.refresh(membership)
        return membership

    @staticmethod
    def assign_membership_role(
        session: Session,
        context: TenantContext,
        *,
        membership_id: uuid.UUID,
        role_key: str,
    ) -> MembershipRole:
        membership = session.get(OrganisationMembership, membership_id)
        if membership is None:
            raise ValueError("membership not found")
        AuthorizationService.require_permission(
            context, "membership.manage", membership.organisation_id
        )
        role = session.scalar(select(Role).where(Role.key == role_key))
        if role is None or role.scope is not RoleScope.TENANT:
            raise ValueError("a tenant role is required")
        assignment = MembershipRole(membership_id=membership.id, role_id=role.id)
        session.add(assignment)
        session.flush()
        AuditService.record(
            session,
            context,
            event_type="membership.role_assigned",
            entity_type="membership_role",
            entity_id=f"{membership.id}:{role.id}",
            organisation_id=membership.organisation_id,
            metadata={"role": role.key},
        )
        session.commit()
        return assignment

    @staticmethod
    def assign_platform_role(
        session: Session,
        context: TenantContext,
        *,
        user_id: uuid.UUID,
        role_key: str,
        status: PlatformAssignmentStatus = PlatformAssignmentStatus.ACTIVE,
    ) -> PlatformRoleAssignment:
        AuthorizationService.require_permission(context, "platform.role.assign")
        role = session.scalar(select(Role).where(Role.key == role_key))
        if role is None or role.scope is not RoleScope.PLATFORM:
            raise ValueError("a platform role is required")
        assignment = PlatformRoleAssignment(user_id=user_id, role_id=role.id, status=status)
        session.add(assignment)
        session.flush()
        AuditService.record(
            session,
            context,
            event_type="platform_role.assigned",
            entity_type="platform_role_assignment",
            entity_id=str(assignment.id),
            metadata={"user_id": str(user_id), "role": role.key, "status": status.value},
        )
        session.commit()
        session.refresh(assignment)
        return assignment
