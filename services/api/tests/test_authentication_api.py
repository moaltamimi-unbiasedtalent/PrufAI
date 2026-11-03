from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.api import auth_settings_dependency
from app.auth.service import AuthenticationService
from app.authorization import build_tenant_context
from app.authorization.seed import seed_defaults
from app.config import Settings, get_settings
from app.db import get_db_session
from app.main import app
from app.models import (
    Base,
    MembershipRole,
    Organisation,
    OrganisationMembership,
    PlatformRoleAssignment,
    Role,
)
from app.models.enums import MembershipStatus, OrganisationType
from app.services.identity import IdentityService


def test_http_login_refresh_logout_and_csrf() -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    setup = factory()
    user = IdentityService.create_user(setup, email="http@example.test")
    settings = Settings(environment="test", auth_jwt_secret="http-test-secret-" + "x" * 40)
    AuthenticationService(settings).set_password(setup, user, "correct horse battery staple")
    setup.commit()

    def override_db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[auth_settings_dependency] = lambda: settings
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        assert client.get("/api/v1/auth/csrf").status_code == 401
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "http@example.test", "password": "correct horse battery staple"},
        )
        assert login.status_code == 200
        access_token = login.json()["access_token"]
        assert (
            client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
            ).status_code
            == 200
        )
        assert client.post("/api/v1/auth/refresh").status_code == 403
        csrf = client.cookies.get(settings.auth_csrf_cookie_name)
        bootstrap = client.get("/api/v1/auth/csrf")
        assert bootstrap.status_code == 200
        assert bootstrap.json() == {"csrf_token": csrf}
        assert bootstrap.headers["cache-control"] == "no-store"
        assert (
            client.post("/api/v1/auth/refresh", headers={"X-PrufAI-CSRF": "wrong"}).status_code
            == 403
        )
        refresh = client.post("/api/v1/auth/refresh", headers={"X-PrufAI-CSRF": csrf})
        assert refresh.status_code == 200
        assert (
            client.post(
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {refresh.json()['access_token']}"},
            ).status_code
            == 403
        )
        logout = client.post(
            "/api/v1/auth/logout",
            headers={
                "Authorization": f"Bearer {refresh.json()['access_token']}",
                "X-PrufAI-CSRF": client.cookies.get(settings.auth_csrf_cookie_name),
            },
        )
        assert logout.status_code == 204
        assert client.get("/api/v1/auth/csrf").status_code == 401
        assert (
            client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {refresh.json()['access_token']}"},
            ).status_code
            == 401
        )
    finally:
        app.dependency_overrides.clear()
        setup.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_me_platform_permissions_do_not_fabricate_institution_membership(session) -> None:
    from sqlalchemy import select

    seed_defaults(session)
    user = IdentityService.create_user(session, email="platform-profile@example.test")
    role = session.scalar(select(Role).where(Role.key == "PRUFAI_SUPER_ADMIN"))
    session.add(PlatformRoleAssignment(user_id=user.id, role_id=role.id))
    settings = Settings(environment="test", auth_jwt_secret="platform-profile-" + "x" * 40)
    AuthenticationService(settings).set_password(session, user, "correct horse battery staple")
    session.commit()

    def override_db() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[auth_settings_dependency] = lambda: settings
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        login = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": "correct horse battery staple"},
        )
        assert login.status_code == 200
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert response.status_code == 200
        assert response.json()["memberships"] == []
        assert response.json()["platform_scope"]
        assert response.json()["platform_permissions"] == sorted(
            build_tenant_context(session, user.id).platform_permissions
        )
    finally:
        app.dependency_overrides.clear()


def test_me_returns_real_membership_permissions_and_denies_inactive_context(session) -> None:
    from sqlalchemy import select

    seed_defaults(session)
    user = IdentityService.create_user(session, email="membership-profile@example.test")
    settings = Settings(environment="test", auth_jwt_secret="membership-test-" + "x" * 40)
    AuthenticationService(settings).set_password(session, user, "correct horse battery staple")
    active_org = Organisation(
        name="Active Demo School",
        slug="active-demo-school",
        organisation_type=OrganisationType.LANGUAGE_SCHOOL,
    )
    inactive_org = Organisation(
        name="Suspended Membership School",
        slug="suspended-demo-school",
        organisation_type=OrganisationType.LANGUAGE_SCHOOL,
    )
    session.add_all([active_org, inactive_org])
    session.flush()
    active = OrganisationMembership(organisation_id=active_org.id, user_id=user.id)
    inactive = OrganisationMembership(
        organisation_id=inactive_org.id,
        user_id=user.id,
        status=MembershipStatus.SUSPENDED,
    )
    session.add_all([active, inactive])
    session.flush()
    role = session.scalar(select(Role).where(Role.key == "INSTITUTION_ADMIN"))
    session.add_all(
        [
            MembershipRole(membership_id=membership.id, role_id=role.id)
            for membership in (active, inactive)
        ]
    )
    session.commit()

    def override_db() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[auth_settings_dependency] = lambda: settings
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        assert client.get("/api/v1/auth/me").status_code == 401
        login = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": "correct horse battery staple"},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 200
        profile = response.json()
        assert not profile["platform_scope"]
        assert profile["platform_permissions"] == []
        memberships = {row["organisation_id"]: row for row in profile["memberships"]}
        assert memberships[str(active_org.id)]["organisation_name"] == active_org.name
        assert memberships[str(active_org.id)]["organisation_slug"] == active_org.slug
        assert memberships[str(active_org.id)]["role_keys"] == ["INSTITUTION_ADMIN"]
        assert memberships[str(active_org.id)]["permissions"] == sorted(
            build_tenant_context(session, user.id, active_org.id).permissions
        )
        assert "exam_assignment.read" in memberships[str(active_org.id)]["permissions"]
        assert memberships[str(inactive_org.id)]["role_keys"] == []
        assert memberships[str(inactive_org.id)]["permissions"] == []
        assert (
            client.get(
                "/api/v1/auth/me",
                headers={**headers, "X-PrufAI-Organisation-ID": str(inactive_org.id)},
            ).status_code
            == 401
        )
        assert "password" not in profile
        assert "access_token" not in profile
    finally:
        app.dependency_overrides.clear()
