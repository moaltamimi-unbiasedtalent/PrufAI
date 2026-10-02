from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.auth.passwords import hash_password, validate_password, verify_password
from app.auth.security import (
    InMemoryRateLimiter,
    RateLimiter,
    RedisRateLimiter,
    TokenService,
    generate_opaque_token,
    hash_token,
    is_expired,
    rate_limit_key,
    utcnow,
)
from app.config import Settings
from app.models import AuthSession, User, UserPasswordCredential
from app.models.enums import AuthSessionStatus, UserStatus
from app.services.identity import normalize_email


class AuthenticationError(ValueError):
    """A deliberately generic authentication failure."""


@dataclass(frozen=True)
class AuthenticationResult:
    user: User
    session: AuthSession
    access_token: str
    access_expires_in: int
    refresh_token: str


class AuthenticationService:
    def __init__(
        self,
        settings: Settings,
        *,
        token_service: Optional[TokenService] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ) -> None:
        self.settings = settings
        self.tokens = token_service or TokenService(settings)
        if rate_limiter is not None:
            self.rate_limiter = rate_limiter
        elif settings.auth_rate_limit_backend == "redis":
            self.rate_limiter = RedisRateLimiter(settings.redis_url)
        else:
            self.rate_limiter = InMemoryRateLimiter()

    def set_password(self, session: Session, user: User, password: str) -> UserPasswordCredential:
        validate_password(password)
        credential = user.password_credential
        now = utcnow()
        if credential is None:
            credential = UserPasswordCredential(
                user_id=user.id, password_hash=hash_password(password), password_changed_at=now
            )
            session.add(credential)
        else:
            credential.password_hash = hash_password(password)
            credential.password_changed_at = now
        session.flush()
        return credential

    def login(
        self,
        session: Session,
        *,
        email: str,
        password: str,
        source: str = "unknown",
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> AuthenticationResult:
        normalized = normalize_email(email)
        if not self.rate_limiter.allow(
            rate_limit_key("login", source, normalized),
            self.settings.auth_rate_limit_login_attempts,
            self.settings.auth_rate_limit_window_seconds,
        ):
            raise AuthenticationError("invalid email or password")
        user = session.scalar(select(User).where(User.normalized_email == normalized))
        credential = (
            session.scalar(
                select(UserPasswordCredential).where(UserPasswordCredential.user_id == user.id)
            )
            if user is not None
            else None
        )
        valid = credential is not None and verify_password(credential.password_hash, password)
        if not valid or user is None or user.status is not UserStatus.ACTIVE:
            AuditService.record_security(
                session,
                event_type="auth.login.failed",
                entity_type="user",
                entity_id=str(user.id) if user else None,
                metadata={"source": source},
                actor_id=user.id if user else None,
            )
            session.commit()
            raise AuthenticationError("invalid email or password")
        self.rate_limiter.clear(rate_limit_key("login", source, normalized))
        result = self._create_session(session, user, user_agent=user_agent, ip_address=ip_address)
        AuditService.record_security(
            session,
            event_type="auth.login.succeeded",
            entity_type="auth_session",
            entity_id=str(result.session.id),
            metadata={"authentication_method": "password"},
            actor_id=user.id,
        )
        session.commit()
        return result

    def _create_session(
        self,
        session: Session,
        user: User,
        *,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> AuthenticationResult:
        now = utcnow()
        session_expiry = now + timedelta(days=self.settings.auth_refresh_token_ttl_days)
        auth_session = AuthSession(
            user_id=user.id,
            status=AuthSessionStatus.ACTIVE,
            last_seen_at=now,
            expires_at=session_expiry,
            refresh_token_hash="pending",
            refresh_token_expires_at=session_expiry,
            refresh_generation=0,
            authentication_method="password",
            user_agent=user_agent,
            ip_address=ip_address,
        )
        session.add(auth_session)
        session.flush()
        refresh_token = f"{auth_session.id}.{generate_opaque_token()}"
        auth_session.refresh_token_hash = hash_token(refresh_token)
        access_token, access_expires_in = self.tokens.issue_access_token(user.id, auth_session.id)
        AuditService.record_security(
            session,
            event_type="auth.session.created",
            entity_type="auth_session",
            entity_id=str(auth_session.id),
            metadata={"authentication_method": "password"},
            actor_id=user.id,
        )
        return AuthenticationResult(
            user=user,
            session=auth_session,
            access_token=access_token,
            access_expires_in=access_expires_in,
            refresh_token=refresh_token,
        )

    def validate_session(self, session: Session, session_id: uuid.UUID) -> AuthSession:
        auth_session = session.get(AuthSession, session_id)
        now = utcnow()
        if auth_session is None or auth_session.status is not AuthSessionStatus.ACTIVE:
            raise AuthenticationError("session is not active")
        if is_expired(auth_session.expires_at) or is_expired(auth_session.refresh_token_expires_at):
            auth_session.status = AuthSessionStatus.EXPIRED
            session.commit()
            raise AuthenticationError("session has expired")
        user = session.get(User, auth_session.user_id)
        if user is None or user.status is not UserStatus.ACTIVE:
            raise AuthenticationError("user is not active")
        auth_session.last_seen_at = now
        return auth_session

    def refresh(self, session: Session, refresh_token: str) -> AuthenticationResult:
        try:
            session_id = uuid.UUID(refresh_token.split(".", 1)[0])
        except (ValueError, IndexError):
            raise AuthenticationError("invalid refresh token") from None
        auth_session = session.get(AuthSession, session_id)
        if auth_session is None:
            raise AuthenticationError("invalid refresh token")
        if auth_session.status is not AuthSessionStatus.ACTIVE:
            raise AuthenticationError("session is not active")
        if auth_session.refresh_token_hash != hash_token(refresh_token):
            self.revoke_session(
                session, auth_session, reason="refresh_reuse_detected", commit=False
            )
            AuditService.record_security(
                session,
                event_type="auth.refresh_reuse_detected",
                entity_type="auth_session",
                entity_id=str(auth_session.id),
                actor_id=auth_session.user_id,
                metadata={"refresh_generation": auth_session.refresh_generation},
            )
            session.commit()
            raise AuthenticationError("invalid refresh token")
        self.validate_session(session, auth_session.id)
        user = session.get(User, auth_session.user_id)
        assert user is not None
        now = utcnow()
        new_refresh_token = f"{auth_session.id}.{generate_opaque_token()}"
        auth_session.refresh_token_hash = hash_token(new_refresh_token)
        auth_session.refresh_generation += 1
        auth_session.last_seen_at = now
        access_token, access_expires_in = self.tokens.issue_access_token(user.id, auth_session.id)
        AuditService.record_security(
            session,
            event_type="auth.session.refreshed",
            entity_type="auth_session",
            entity_id=str(auth_session.id),
            actor_id=user.id,
            metadata={"refresh_generation": auth_session.refresh_generation},
        )
        session.commit()
        return AuthenticationResult(
            user=user,
            session=auth_session,
            access_token=access_token,
            access_expires_in=access_expires_in,
            refresh_token=new_refresh_token,
        )

    def revoke_session(
        self, session: Session, auth_session: AuthSession, *, reason: str, commit: bool = True
    ) -> None:
        if auth_session.status is AuthSessionStatus.ACTIVE:
            auth_session.status = AuthSessionStatus.REVOKED
            auth_session.revoked_at = utcnow()
            auth_session.revocation_reason = reason
            AuditService.record_security(
                session,
                event_type="auth.session.revoked",
                entity_type="auth_session",
                entity_id=str(auth_session.id),
                actor_id=auth_session.user_id,
                metadata={"reason": reason},
            )
        if commit:
            session.commit()

    def revoke_all(self, session: Session, user_id: uuid.UUID, *, reason: str) -> int:
        sessions = list(
            session.scalars(
                select(AuthSession).where(
                    AuthSession.user_id == user_id,
                    AuthSession.status == AuthSessionStatus.ACTIVE,
                )
            )
        )
        for auth_session in sessions:
            self.revoke_session(session, auth_session, reason=reason, commit=False)
        session.commit()
        return len(sessions)
