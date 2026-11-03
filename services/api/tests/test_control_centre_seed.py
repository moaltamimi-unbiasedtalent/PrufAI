"""Unit checks for the explicitly invoked development platform identity."""

import uuid

import pytest
from sqlalchemy import delete, func, select

from app.auth.passwords import verify_password
from app.auth.service import AuthenticationService
from app.authorization import build_tenant_context
from app.authorization.seed import ROLE_DEFINITIONS
from app.config import Settings
from app.models import (
    CertificateRecord,
    CertificationAuthorization,
    OrganisationMembership,
    PlatformRoleAssignment,
    Role,
    User,
    UserPasswordCredential,
)
from app.models.enums import PlatformAssignmentStatus, RoleScope, UserStatus
from app.scripts.seed_demo import PASSWORD, PLATFORM_ADMIN_EMAIL, demo_id, reconcile_demo


def _settings() -> Settings:
    return Settings(_env_file=None, environment="development")


def assert_platform_demo_seed(session) -> dict[str, str]:
    certification_counts = {
        model.__name__: session.scalar(select(func.count()).select_from(model))
        for model in (CertificationAuthorization, CertificateRecord)
    }
    first = reconcile_demo(session, _settings(), enabled=True)
    platform_id = uuid.UUID(first["platform_admin_user_id"])
    assert platform_id == demo_id("platform-admin")
    platform = session.get(User, platform_id)
    assert platform is not None
    assert platform.email == PLATFORM_ADMIN_EMAIL
    assert platform.status is UserStatus.ACTIVE
    credential = platform.password_credential
    assert credential is not None
    original_hash = credential.password_hash
    assert original_hash != PASSWORD
    assert verify_password(original_hash, PASSWORD)
    counts = {
        model.__name__: session.scalar(select(func.count()).select_from(model))
        for model in (User, PlatformRoleAssignment, OrganisationMembership, UserPasswordCredential)
    }
    assert reconcile_demo(session, _settings(), enabled=True) == first
    assert counts == {
        model.__name__: session.scalar(select(func.count()).select_from(model))
        for model in (User, PlatformRoleAssignment, OrganisationMembership, UserPasswordCredential)
    }
    session.expire_all()
    assert platform.password_credential.password_hash == original_hash
    assignments = list(
        session.scalars(
            select(PlatformRoleAssignment).where(PlatformRoleAssignment.user_id == platform.id)
        )
    )
    assert len(assignments) == 1
    assert assignments[0].status is PlatformAssignmentStatus.ACTIVE
    assert assignments[0].role.key == "PRUFAI_SUPER_ADMIN"
    assert assignments[0].role.scope is RoleScope.PLATFORM
    assert (
        session.scalar(
            select(OrganisationMembership.id).where(OrganisationMembership.user_id == platform.id)
        )
        is None
    )
    context = build_tenant_context(session, platform.id)
    assert context.organisation_id is None
    assert context.membership_id is None
    assert context.permissions == frozenset()
    assert context.platform_scope is True
    assert {
        "platform.control_center.read",
        "platform.organisation.read",
        "platform.audit.read",
        "platform.system.read",
        "certification.read",
    } <= context.platform_permissions
    assert certification_counts == {
        model.__name__: session.scalar(select(func.count()).select_from(model))
        for model in (CertificationAuthorization, CertificateRecord)
    }
    assert _settings().certification_issuance_enabled is False
    return first


def test_platform_demo_has_real_platform_role_without_tenant_membership(session) -> None:
    assert_platform_demo_seed(session)


def test_platform_demo_repairs_its_missing_credential_and_assignment(session) -> None:
    first = assert_platform_demo_seed(session)
    platform_id = uuid.UUID(first["platform_admin_user_id"])
    session.execute(
        delete(PlatformRoleAssignment).where(PlatformRoleAssignment.user_id == platform_id)
    )
    session.execute(
        delete(UserPasswordCredential).where(UserPasswordCredential.user_id == platform_id)
    )
    session.commit()
    session.expire_all()
    assert assert_platform_demo_seed(session) == first


def test_platform_demo_does_not_claim_an_unrelated_same_email_identity(session) -> None:
    existing = User(
        email=PLATFORM_ADMIN_EMAIL,
        normalized_email=PLATFORM_ADMIN_EMAIL,
        display_name="Existing developer identity",
        status=UserStatus.DISABLED,
    )
    session.add(existing)
    session.flush()
    credential = AuthenticationService(_settings()).set_password(
        session, existing, "Unrelated-development-password-2026!"
    )
    session.commit()
    original_hash = credential.password_hash
    with pytest.raises(RuntimeError, match="another identity"):
        reconcile_demo(session, _settings(), enabled=True)
    session.expire_all()
    assert existing.status is UserStatus.DISABLED
    assert existing.display_name == "Existing developer identity"
    assert existing.password_credential.password_hash == original_hash
    assert (
        session.scalar(
            select(PlatformRoleAssignment.id).where(PlatformRoleAssignment.user_id == existing.id)
        )
        is None
    )
    assert session.get(User, demo_id("platform-admin")) is None


def test_platform_demo_refuses_tenant_link_instead_of_deleting_membership(session) -> None:
    identifiers = assert_platform_demo_seed(session)
    membership = OrganisationMembership(
        organisation_id=uuid.UUID(identifiers["organisation_id"]),
        user_id=uuid.UUID(identifiers["platform_admin_user_id"]),
    )
    session.add(membership)
    session.commit()
    membership_id = membership.id
    with pytest.raises(RuntimeError, match="tenant membership"):
        reconcile_demo(session, _settings(), enabled=True)
    assert session.get(OrganisationMembership, membership_id) is not None


def test_platform_demo_gate_blocks_writes_before_account_creation(session) -> None:
    for settings, enabled in (
        (_settings(), False),
        (Settings(_env_file=None, environment="test"), True),
        (
            Settings(_env_file=None, environment="production", auth_rate_limit_backend="redis"),
            True,
        ),
    ):
        with pytest.raises(RuntimeError, match="ENVIRONMENT=development"):
            reconcile_demo(session, settings, enabled=enabled)
    assert session.scalar(select(func.count()).select_from(User)) == 0
    assert session.scalar(select(func.count()).select_from(Role)) == 0


def test_control_centre_permissions_never_enter_tenant_role_definitions() -> None:
    protected = {"platform.control_center.read", "platform.system.read", "certification.read"}
    for _key, (scope, permissions) in ROLE_DEFINITIONS.items():
        if scope is RoleScope.TENANT:
            assert protected.isdisjoint(permissions)
    assert protected <= set(ROLE_DEFINITIONS["PRUFAI_SUPER_ADMIN"][1])
    assert "platform.control_center.read" in ROLE_DEFINITIONS["PRUFAI_STANDARDS_ADMIN"][1]
    assert "platform.system.read" not in ROLE_DEFINITIONS["PRUFAI_STANDARDS_ADMIN"][1]
    assert "certification.read" not in ROLE_DEFINITIONS["PRUFAI_STANDARDS_ADMIN"][1]
    assert "platform.control_center.read" in ROLE_DEFINITIONS["PRUFAI_AI_ADMIN"][1]
    assert "platform.system.read" in ROLE_DEFINITIONS["PRUFAI_AI_ADMIN"][1]
    assert "certification.read" not in ROLE_DEFINITIONS["PRUFAI_AI_ADMIN"][1]
