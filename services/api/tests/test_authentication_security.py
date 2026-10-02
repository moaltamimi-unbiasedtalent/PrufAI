import time
import uuid

import pytest
from redis.exceptions import RedisError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.flows import FakeNotificationSink, InvitationService, PasswordResetService
from app.auth.security import (
    InMemoryRateLimiter,
    RateLimitUnavailable,
    RedisRateLimiter,
    TokenService,
    rate_limit_key,
)
from app.auth.service import AuthenticationError, AuthenticationService
from app.authorization.context import TenantContext, build_tenant_context
from app.authorization.seed import seed_defaults
from app.config import Settings
from app.models import AuthSession, Base, UserPasswordCredential
from app.models.enums import AuthSessionStatus, OrganisationType, UserStatus
from app.services.identity import IdentityService
from app.services.organisations import OrganisationService


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, tuple[int, float]] = {}

    def eval(self, _script: str, _keys: int, key: str, window: str) -> int:
        now = time.monotonic()
        current, expires = self.values.get(key, (0, now))
        if expires <= now:
            current = 0
        current += 1
        self.values[key] = (current, now + int(window))
        return current

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class FailingRedis:
    def eval(self, *_args: object) -> int:
        raise RedisError("unavailable")

    def delete(self, *_args: object) -> None:
        raise RedisError("unavailable")


@pytest.fixture
def auth_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def auth_settings() -> Settings:
    return Settings(environment="test", auth_jwt_secret="test-secret-" + "x" * 40)


def test_password_is_argon2id_and_policy_is_enforced(auth_session: Session) -> None:
    user = IdentityService.create_user(auth_session, email="secure@example.test")
    service = AuthenticationService(Settings(environment="test"))
    service.set_password(auth_session, user, "a secure passphrase")
    auth_session.commit()
    credential = auth_session.scalar(
        select(UserPasswordCredential).where(UserPasswordCredential.user_id == user.id)
    )
    assert credential is not None
    assert credential.password_hash.startswith("$argon2id$")
    assert credential.password_hash != "a secure passphrase"
    with pytest.raises(ValueError):
        service.set_password(auth_session, user, "short")


def test_login_refresh_rotation_reuse_and_revocation(
    auth_session: Session, auth_settings: Settings
) -> None:
    user = IdentityService.create_user(auth_session, email="login@example.test")
    service = AuthenticationService(auth_settings, rate_limiter=InMemoryRateLimiter())
    service.set_password(auth_session, user, "correct horse battery staple")
    auth_session.commit()

    result = service.login(
        auth_session, email="LOGIN@example.test", password="correct horse battery staple"
    )
    assert result.access_token
    assert result.refresh_token
    rotated = service.refresh(auth_session, result.refresh_token)
    assert rotated.refresh_token != result.refresh_token
    with pytest.raises(AuthenticationError):
        service.refresh(auth_session, result.refresh_token)
    stored = auth_session.get(AuthSession, result.session.id)
    assert stored is not None and stored.status is AuthSessionStatus.REVOKED


def test_suspended_user_cannot_login_or_use_existing_session(
    auth_session: Session, auth_settings: Settings
) -> None:
    user = IdentityService.create_user(auth_session, email="suspended@example.test")
    service = AuthenticationService(auth_settings)
    service.set_password(auth_session, user, "correct horse battery staple")
    auth_session.commit()
    user.status = UserStatus.SUSPENDED
    auth_session.commit()
    with pytest.raises(AuthenticationError):
        service.login(auth_session, email=user.email, password="correct horse battery staple")


def test_invitation_and_password_reset_tokens_are_one_time(auth_session: Session) -> None:
    seed_defaults(auth_session)
    admin = IdentityService.create_user(auth_session, email="admin@example.test")
    platform_context = TenantContext(
        user_id=admin.id,
        organisation_id=None,
        membership_id=None,
        platform_permissions=frozenset(
            {
                "platform.organisation.manage",
                "platform.membership.manage",
                "platform.role.assign",
                "platform.audit.read",
            }
        ),
    )
    organisation = OrganisationService.create_organisation(
        auth_session,
        platform_context,
        name="Invite School",
        slug="invite-school",
        organisation_type=OrganisationType.LANGUAGE_SCHOOL,
    )
    member = OrganisationService.create_membership(
        auth_session, platform_context, organisation_id=organisation.id, user_id=admin.id
    )
    OrganisationService.assign_membership_role(
        auth_session, platform_context, membership_id=member.id, role_key="INSTITUTION_ADMIN"
    )
    tenant = build_tenant_context(auth_session, admin.id, organisation.id)
    notifications = FakeNotificationSink()
    invitations = InvitationService(notifications)
    invitation, raw_invite = invitations.create(
        auth_session,
        tenant,
        organisation_id=organisation.id,
        email="new@example.test",
        role_keys=["TEACHER"],
    )
    assert invitation.token_hash != raw_invite
    invited = invitations.accept(
        auth_session, token=raw_invite, password="correct horse battery staple"
    )
    assert invited.normalized_email == "new@example.test"
    with pytest.raises(ValueError):
        invitations.accept(auth_session, token=raw_invite, password="correct horse battery staple")

    reset_notifications = FakeNotificationSink()
    reset = PasswordResetService(reset_notifications)
    raw_reset = reset.request(
        auth_session, email="new@example.test", settings=Settings(environment="test")
    )
    assert raw_reset is not None
    reset.confirm(
        auth_session,
        token=raw_reset,
        new_password="an even better passphrase",
        authentication=AuthenticationService(Settings(environment="test")),
    )
    with pytest.raises(ValueError):
        reset.confirm(
            auth_session,
            token=raw_reset,
            new_password="another passphrase",
            authentication=AuthenticationService(Settings(environment="test")),
        )


def test_access_token_claims_do_not_include_authorization_state(auth_settings: Settings) -> None:
    token, _ = TokenService(auth_settings).issue_access_token(uuid.uuid4(), uuid.uuid4())
    assert "permissions" not in token
    assert "roles" not in token


def test_authentication_rate_limiter_is_temporary_and_resettable() -> None:
    limiter = InMemoryRateLimiter()
    assert limiter.allow("login:ip:account", 2, 300)
    assert limiter.allow("login:ip:account", 2, 300)
    assert not limiter.allow("login:ip:account", 2, 300)
    limiter.clear("login:ip:account")
    assert limiter.allow("login:ip:account", 2, 300)


def test_redis_rate_limiter_is_atomic_shared_and_expires() -> None:
    fake = FakeRedis()
    first = RedisRateLimiter("redis://unused", client=fake)  # type: ignore[arg-type]
    second = RedisRateLimiter("redis://unused", client=fake)  # type: ignore[arg-type]
    key = rate_limit_key("login", "127.0.0.1", "private@example.test")
    assert "private@example.test" not in key
    assert first.allow(key, 1, 1)
    assert not second.allow(key, 1, 1)
    time.sleep(1.05)
    assert second.allow(key, 1, 1)


def test_redis_rate_limiter_failure_does_not_downgrade() -> None:
    limiter = RedisRateLimiter("redis://unused", client=FailingRedis())  # type: ignore[arg-type]
    with pytest.raises(RateLimitUnavailable):
        limiter.allow("prufai:auth:rl:test", 1, 60)


def test_production_configuration_rejects_memory_rate_limiting() -> None:
    with pytest.raises(ValueError, match="must use Redis"):
        Settings(
            environment="production",
            auth_jwt_secret="production-secret-" + "x" * 40,
            auth_rate_limit_backend="memory",
        )
