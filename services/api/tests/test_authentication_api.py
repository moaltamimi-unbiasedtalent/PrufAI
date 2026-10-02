from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.api import auth_settings_dependency
from app.auth.service import AuthenticationService
from app.config import Settings, get_settings
from app.db import get_db_session
from app.main import app
from app.models import Base
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
        refresh = client.post("/api/v1/auth/refresh", headers={"X-PrufAI-CSRF": csrf})
        assert refresh.status_code == 200
        logout = client.post(
            "/api/v1/auth/logout",
            headers={
                "Authorization": f"Bearer {refresh.json()['access_token']}",
                "X-PrufAI-CSRF": client.cookies.get(settings.auth_csrf_cookie_name),
            },
        )
        assert logout.status_code == 204
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
