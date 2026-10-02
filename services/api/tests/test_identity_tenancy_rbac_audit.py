import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.authorization.seed import seed_defaults
from app.models import AuditEvent, Organisation, User
from app.models.enums import MembershipStatus, OrganisationType, UserStatus
from app.services.identity import IdentityService
from app.services.organisations import OrganisationService


def bootstrap_context(user_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        user_id=user_id,
        organisation_id=None,
        membership_id=None,
        platform_permissions=frozenset(
            {
                "platform.organisation.manage",
                "platform.membership.manage",
                "platform.site.manage",
                "platform.audit.read",
                "platform.role.assign",
            }
        ),
    )


def create_platform(session: Session) -> tuple[User, TenantContext]:
    seed_defaults(session)
    user = IdentityService.create_user(session, email="platform@example.test")
    return user, bootstrap_context(user.id)


def create_tenant_fixture(
    session: Session,
) -> tuple[User, User, Organisation, Organisation, TenantContext]:
    platform_user, platform_context = create_platform(session)
    organisation_a = OrganisationService.create_organisation(
        session,
        platform_context,
        name="Alpha School",
        slug="alpha-school",
        organisation_type=OrganisationType.LANGUAGE_SCHOOL,
    )
    organisation_b = OrganisationService.create_organisation(
        session,
        platform_context,
        name="Beta School",
        slug="beta-school",
        organisation_type=OrganisationType.LANGUAGE_SCHOOL,
    )
    user_a = IdentityService.create_user(session, email="teacher@example.test")
    user_b = IdentityService.create_user(session, email="other@example.test")
    membership_a = OrganisationService.create_membership(
        session, platform_context, organisation_id=organisation_a.id, user_id=user_a.id
    )
    OrganisationService.create_membership(
        session, platform_context, organisation_id=organisation_b.id, user_id=user_b.id
    )
    OrganisationService.assign_membership_role(
        session,
        platform_context,
        membership_id=membership_a.id,
        role_key="INSTITUTION_ADMIN",
    )
    tenant_context = build_tenant_context(session, user_a.id, organisation_a.id)
    return user_a, user_b, organisation_a, organisation_b, tenant_context


def test_user_email_is_normalized_unique_and_status_persisted(session: Session) -> None:
    user = IdentityService.create_user(
        session,
        email="  Person@Example.Test ",
        display_name="Person",
        status=UserStatus.INVITED,
    )

    assert user.normalized_email == "person@example.test"
    assert user.status is UserStatus.INVITED
    with pytest.raises(ValueError, match="normalized email"):
        IdentityService.create_user(session, email="PERSON@example.test")


def test_tenant_scoped_site_access_and_audit(session: Session) -> None:
    _, _, organisation_a, organisation_b, tenant_context = create_tenant_fixture(session)
    site = OrganisationService.create_site(
        session,
        tenant_context,
        organisation_id=organisation_a.id,
        name="Downtown",
        slug="downtown",
    )

    assert OrganisationService.list_sites(
        session, tenant_context, organisation_id=organisation_a.id
    ) == [site]
    with pytest.raises(AuthorizationError, match="permission denied"):
        OrganisationService.list_sites(session, tenant_context, organisation_id=organisation_b.id)

    events = AuditService.list_for_organisation(session, tenant_context, organisation_a.id)
    event_types = {event.event_type for event in events}
    assert "organisation_site.created" in event_types
    site_event = next(event for event in events if event.entity_id == str(site.id))
    assert site_event.actor_id == tenant_context.user_id
    assert site_event.organisation_id == organisation_a.id
    assert site_event.metadata_json == {"slug": "downtown"}


def test_tenant_admin_cannot_create_organisation_or_access_other_tenant(session: Session) -> None:
    _, _, organisation_a, organisation_b, tenant_context = create_tenant_fixture(session)

    with pytest.raises(AuthorizationError):
        OrganisationService.create_organisation(
            session,
            tenant_context,
            name="Not Allowed",
            slug="not-allowed",
            organisation_type=OrganisationType.OTHER,
        )
    with pytest.raises(AuthorizationError):
        OrganisationService.create_membership(
            session,
            tenant_context,
            organisation_id=organisation_b.id,
            user_id=tenant_context.user_id,
        )


def test_missing_permission_and_suspended_membership_are_denied(session: Session) -> None:
    _, _, organisation_a, _, tenant_context = create_tenant_fixture(session)
    reviewer = IdentityService.create_user(session, email="reviewer@example.test")
    reviewer_membership = OrganisationService.create_membership(
        session,
        bootstrap_context(tenant_context.user_id),
        organisation_id=organisation_a.id,
        user_id=reviewer.id,
    )
    OrganisationService.assign_membership_role(
        session,
        bootstrap_context(tenant_context.user_id),
        membership_id=reviewer_membership.id,
        role_key="REVIEWER",
    )
    reviewer_context = build_tenant_context(session, reviewer.id, organisation_a.id)
    with pytest.raises(AuthorizationError):
        OrganisationService.create_site(
            session, reviewer_context, organisation_id=organisation_a.id, name="Nope", slug="nope"
        )

    OrganisationService.change_membership_status(
        session,
        bootstrap_context(tenant_context.user_id),
        membership_id=reviewer_membership.id,
        status=MembershipStatus.SUSPENDED,
    )
    with pytest.raises(AuthorizationError, match="active organisation membership"):
        build_tenant_context(session, reviewer.id, organisation_a.id)


def test_platform_authority_requires_explicit_assignment(session: Session) -> None:
    seed_defaults(session)
    user = IdentityService.create_user(session, email="plain@example.test")
    with pytest.raises(AuthorizationError):
        build_tenant_context(session, user.id)

    platform_user = IdentityService.create_user(session, email="admin@example.test")
    bootstrap = bootstrap_context(platform_user.id)
    OrganisationService.assign_platform_role(
        session, bootstrap, user_id=platform_user.id, role_key="PRUFAI_SUPER_ADMIN"
    )
    context = build_tenant_context(session, platform_user.id)
    organisation = OrganisationService.create_organisation(
        session,
        context,
        name="Platform Created",
        slug="platform-created",
        organisation_type=OrganisationType.OTHER,
    )
    assert organisation.slug == "platform-created"


def test_duplicate_membership_and_site_slug_are_prevented(session: Session) -> None:
    _, _, organisation_a, _, tenant_context = create_tenant_fixture(session)
    with pytest.raises(ValueError, match="membership already exists"):
        OrganisationService.create_membership(
            session,
            bootstrap_context(tenant_context.user_id),
            organisation_id=organisation_a.id,
            user_id=tenant_context.user_id,
        )

    OrganisationService.create_site(
        session,
        tenant_context,
        organisation_id=organisation_a.id,
        name="First",
        slug="same-site",
    )
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        OrganisationService.create_site(
            session,
            tenant_context,
            organisation_id=organisation_a.id,
            name="Duplicate",
            slug="same-site",
        )


def test_audit_metadata_is_structured_and_append_only(session: Session) -> None:
    _, _, organisation_a, _, tenant_context = create_tenant_fixture(session)
    with pytest.raises(ValueError, match="sensitive key"):
        AuditService.record(
            session,
            tenant_context,
            event_type="test.recorded",
            entity_type="test",
            organisation_id=organisation_a.id,
            metadata={"secret": "never-store"},
        )
    session.rollback()
    assert not hasattr(AuditService, "update")
    assert not hasattr(AuditService, "delete")
    assert (
        session.scalar(select(AuditEvent).where(AuditEvent.event_type == "test.recorded")) is None
    )
