"""Use the real invitation and recovery APIs with migrated PostgreSQL and Redis."""

import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app.auth.api import auth_settings_dependency
from app.auth.delivery import enrollment_delivery
from app.auth.security import hash_token, utcnow
from app.auth.service import AuthenticationService
from app.authorization.context import TenantContext
from app.authorization.seed import seed_defaults
from app.config import Settings
from app.db import create_session_factory, get_db_session
from app.evaluation.disposable import assert_disposable_database
from app.main import app
from app.models import (
    AuthSession,
    OrganisationInvitation,
    OrganisationMembership,
    PasswordResetToken,
    User,
)
from app.models.enums import InvitationStatus, OrganisationType
from app.services.identity import IdentityService
from app.services.organisations import OrganisationService

REQUIRED = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRED,
    reason="Enrollment integration needs disposable PostgreSQL and Redis",
)
API = "/api/v1/auth"
ORGS = "/api/v1/organisations"
ADMIN = "enrollment-admin@example.test"
PASSWORD = "Synthetic original password!"
NEW_PASSWORD = "Synthetic replacement password!"


class Capture:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)

    def token(self, email, purpose="invitation"):
        return next(
            message.token
            for message in reversed(self.messages)
            if message.recipient == email and message.purpose == purpose
        )


@pytest.fixture(scope="module")
def setup_enrollment():
    source = os.environ.get("POSTGRES_INTEGRATION_URL")
    redis = os.environ.get("REDIS_INTEGRATION_URL")
    if not source or not redis:
        pytest.fail("Required enrollment gate needs PostgreSQL and Redis integration URLs")
    url = make_url(source)
    assert url.get_backend_name() == "postgresql" and url.host in {"localhost", "127.0.0.1"}
    assert url.database == os.environ["PRUFAI_DISPOSABLE_DATABASE"]
    if os.getenv("GITHUB_ACTIONS") == "true":
        assert_disposable_database(source)
    else:
        assert url.database.startswith("o1b2_"), "Use an owned disposable PostgreSQL rehearsal"
    assert make_url(redis).host in {"localhost", "127.0.0.1"}
    control = url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    name = "prufai_enrollment_" + uuid.uuid4().hex
    fresh = url.set(database=name).render_as_string(hide_password=False)
    with psycopg.connect(control, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "DATABASE_URL": fresh},
            check=True,
            capture_output=True,
            timeout=90,
        )
        settings = Settings(
            _env_file=None,
            environment="test",
            database_url=fresh,
            redis_url=redis,
            auth_rate_limit_backend="redis",
            auth_enrollment_delivery_mode="TEST_ONLY",
        )
        factory = create_session_factory(settings)
        try:
            with factory() as session:
                seed_defaults(session)
                admin = IdentityService.create_user(session, email=ADMIN)
                AuthenticationService(settings).set_password(session, admin, PASSWORD)
                session.commit()
                platform = TenantContext(
                    user_id=admin.id,
                    organisation_id=None,
                    membership_id=None,
                    permissions=frozenset(),
                    platform_permissions=frozenset(
                        {
                            "platform.organisation.manage",
                            "platform.membership.manage",
                            "platform.audit.read",
                        }
                    ),
                )
                organisations = [
                    OrganisationService.create_organisation(
                        session,
                        platform,
                        name=label,
                        slug=label.lower(),
                        organisation_type=OrganisationType.LANGUAGE_SCHOOL,
                    )
                    for label in ("Berlin", "Other")
                ]
                org, other = (item.id for item in organisations)
                member = OrganisationService.create_membership(
                    session,
                    platform,
                    organisation_id=org,
                    user_id=admin.id,
                )
                OrganisationService.assign_membership_role(
                    session,
                    platform,
                    membership_id=member.id,
                    role_key="INSTITUTION_ADMIN",
                )
            yield settings, factory, org, other
        finally:
            factory.kw["bind"].dispose()
    finally:
        with psycopg.connect(control, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def enrollment(setup_enrollment, monkeypatch):
    settings, factory, org, other = setup_enrollment
    capture = Capture()

    def sessions():
        with factory() as session:
            yield session

    for dependency, override in (
        (get_db_session, sessions),
        (auth_settings_dependency, lambda: settings),
        (enrollment_delivery, lambda: capture),
    ):
        monkeypatch.setitem(app.dependency_overrides, dependency, override)
    with TestClient(app) as client:
        login = client.post(API + "/login", json={"email": ADMIN, "password": PASSWORD})
        assert login.status_code == 200
        headers = {
            "Authorization": "Bearer " + login.json()["access_token"],
            "X-PrufAI-Organisation-ID": str(org),
        }
        yield client, factory, capture, org, other, headers


def candidate_email():
    return uuid.uuid4().hex + "@example.test"


def invite(client, capture, org, headers, email):
    response = client.post(f"{ORGS}/{org}/invitations", headers=headers, json={"email": email})
    assert response.status_code == 200
    token = capture.token(email)
    if token in response.text:
        pytest.fail("Invitation response disclosed its token", pytrace=False)
    return uuid.UUID(response.json()["id"]), token


def acceptance(client, token, org, password=PASSWORD):
    return client.post(
        API + "/invitations/accept",
        json={
            "token": token,
            "organisation_id": str(org),
            "password": password,
        },
    )


def error(response, code, status=400):
    assert response.status_code == status
    matched = response.json() == {"detail": {"code": code}}
    assert matched, "Unexpected public error code"


def test_candidate_role_tenant_password_and_existing_account(enrollment):
    client, factory, capture, org, other, headers = enrollment
    email = candidate_email()
    denied = client.post(f"{ORGS}/{other}/invitations", headers=headers, json={"email": email})
    error(denied, "INVITATION_DENIED", 403)
    wrong_role = client.post(
        f"{ORGS}/{org}/invitations",
        headers=headers,
        json={"email": email, "role_keys": ["INSTITUTION_ADMIN"]},
    )
    error(wrong_role, "INVITATION_ROLE_DENIED")
    identity, token = invite(client, capture, org, headers, email)
    error(acceptance(client, token, other), "INVITATION_WRONG_INSTITUTION")
    error(acceptance(client, token, org, "short"), "INVALID_PASSWORD")
    with factory() as session:
        item = session.get(OrganisationInvitation, identity)
        expected_digest = hash_token(token)
        assert item.token_hash == expected_digest and item.status is InvitationStatus.PENDING
    assert acceptance(client, token, org).status_code == 200
    error(acceptance(client, token, org), "INVITATION_USED")
    _, duplicate = invite(client, capture, org, headers, email)
    error(acceptance(client, duplicate, org), "INVITATION_ACCOUNT_EXISTS")
    with factory() as session:
        user = session.scalar(select(User).where(User.normalized_email == email))
        assert len(user.memberships) == 1 and user.memberships[0].organisation_id == org
        assert [item.role.key for item in user.memberships[0].roles] == ["EXAMINER"]
        assert (
            session.scalar(
                select(func.count()).select_from(AuthSession).where(AuthSession.user_id == user.id)
            )
            == 0
        )


@pytest.mark.parametrize(
    ("state", "code"),
    [
        ("expired", "INVITATION_EXPIRED"),
        ("revoked", "INVITATION_INVALID"),
        ("wrong_role", "INVITATION_ROLE_DENIED"),
    ],
)
def test_unusable_invitations_never_create_users(enrollment, state, code):
    client, factory, capture, org, _, headers = enrollment
    email = candidate_email()
    identity, token = invite(client, capture, org, headers, email)
    with factory() as session:
        item = session.get(OrganisationInvitation, identity)
        if state == "expired":
            item.expires_at = utcnow() - timedelta(seconds=1)
        elif state == "revoked":
            item.status = InvitationStatus.REVOKED
            item.revoked_at = utcnow()
        else:
            item.intended_role_keys = ["INSTITUTION_ADMIN"]
        session.commit()
    error(acceptance(client, token, org), code)
    error(acceptance(client, "invalid-token-" + uuid.uuid4().hex, org), "INVITATION_INVALID")
    with factory() as session:
        assert session.scalar(select(User.id).where(User.normalized_email == email)) is None


@pytest.mark.parametrize("same_token", [True, False])
def test_concurrent_invitation_acceptance_creates_one_identity_and_membership(
    enrollment, same_token
):
    client, factory, capture, org, _, headers = enrollment
    email = candidate_email()
    _, token = invite(client, capture, org, headers, email)
    other = token if same_token else invite(client, capture, org, headers, email)[1]
    gate = Barrier(2)

    def run(code):
        gate.wait(timeout=5)
        return acceptance(client, code, org)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = sorted(pool.map(run, (token, other)), key=lambda reply: reply.status_code)
    assert responses[0].status_code == 200
    error(responses[1], "INVITATION_USED" if same_token else "INVITATION_ACCOUNT_EXISTS")
    with factory() as session:
        users = list(session.scalars(select(User).where(User.normalized_email == email)))
        assert len(users) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(OrganisationMembership)
                .where(
                    OrganisationMembership.user_id == users[0].id,
                )
            )
            == 1
        )


def test_recovery_expiry_rotation_rate_limit_and_session_revocation(enrollment):
    client, factory, capture, org, _, headers = enrollment
    email = candidate_email()
    _, token = invite(client, capture, org, headers, email)
    assert acceptance(client, token, org).status_code == 200
    signin = client.post(API + "/login", json={"email": email, "password": PASSWORD})
    assert signin.status_code == 200
    previous = {"Authorization": "Bearer " + signin.json()["access_token"]}
    csrf = signin.cookies["prufai_csrf"]
    refresh_cookie = signin.cookies["prufai_refresh"]
    unknown = client.post(API + "/password-reset/request", json={"email": candidate_email()})
    reply = client.post(API + "/password-reset/request", json={"email": email})
    assert reply.status_code == unknown.status_code == 202
    assert reply.json() == unknown.json() == {"status": "accepted"}
    expired = capture.token(email, "password_reset")
    if expired in reply.text:
        pytest.fail("Recovery response disclosed its token", pytrace=False)
    with factory() as session:
        row = session.scalar(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(expired))
        )
        row.expires_at = utcnow() - timedelta(seconds=1)
        session.commit()

    def confirm(value, password=NEW_PASSWORD):
        return client.post(
            API + "/password-reset/confirm", json={"token": value, "new_password": password}
        )

    error(confirm(expired), "RESET_EXPIRED")
    assert (
        client.post(API + "/password-reset/request", json={"email": email.upper()}).status_code
        == 202
    )
    alternate = capture.token(email, "password_reset")
    assert client.post(API + "/password-reset/request", json={"email": email}).status_code == 202
    current = capture.token(email, "password_reset")
    error(client.post(API + "/password-reset/request", json={"email": email}), "TRY_LATER", 429)
    error(confirm(current, "short"), "INVALID_PASSWORD")
    gate = Barrier(2)

    def reset(_):
        gate.wait(timeout=5)
        return confirm(current)

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = sorted(pool.map(reset, range(2)), key=lambda result: result.status_code)
    assert replies[0].status_code == 200
    error(replies[1], "RESET_USED")
    error(confirm(alternate), "RESET_INVALID")
    assert client.get(API + "/me", headers=previous).status_code == 401
    headers = {
        "X-PrufAI-CSRF": csrf,
        "Cookie": f"prufai_refresh={refresh_cookie}; prufai_csrf={csrf}",
    }
    assert client.post(API + "/refresh", headers=headers).status_code == 401
    assert (
        client.post(API + "/login", json={"email": email, "password": PASSWORD}).status_code == 401
    )
    assert (
        client.post(API + "/login", json={"email": email, "password": NEW_PASSWORD}).status_code
        == 200
    )


def test_unavailable_redis_fails_closed_and_disabled_delivery_does_not_issue_tokens(
    enrollment, setup_enrollment, monkeypatch
):
    client, _, capture, _, _, _ = enrollment
    settings = setup_enrollment[0]
    secret = "secret-that-must-never-appear-in-a-validation-response"
    malformed = client.post(
        API + "/invitations/accept",
        json={
            "token": {"secret": secret},
            "password": secret,
        },
    )
    error(malformed, "INVALID_INPUT", 422)
    if secret in malformed.text:
        pytest.fail("Validation response echoed submitted input", pytrace=False)
    snapshot = len(capture.messages)

    def provider(value):
        def get_value():
            return value

        return get_value

    for changes, code in (
        ({"redis_url": "redis://127.0.0.1:1/0"}, "AUTH_UNAVAILABLE"),
        ({"auth_enrollment_delivery_mode": "DISABLED"}, "DELIVERY_DISABLED"),
    ):
        override = settings.model_copy(update=changes)
        monkeypatch.setitem(app.dependency_overrides, auth_settings_dependency, provider(override))
        error(client.post(API + "/password-reset/request", json={"email": ADMIN}), code, 503)
        assert len(capture.messages) == snapshot
    assert client.get(API + "/enrollment").json()["enabled"] is False
