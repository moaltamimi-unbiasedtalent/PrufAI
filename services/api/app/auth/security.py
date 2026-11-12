from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol, cast

import jwt
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from app.config import Settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_expired(value: datetime) -> bool:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= utcnow()


def generate_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    token_id: str
    issued_at: datetime
    expires_at: datetime


class TokenService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def issue_access_token(self, user_id: uuid.UUID, session_id: uuid.UUID) -> tuple[str, int]:
        issued_at = utcnow()
        expires_at = issued_at + timedelta(minutes=self.settings.auth_access_token_ttl_minutes)
        payload: dict[str, Any] = {
            "sub": str(user_id),
            "sid": str(session_id),
            "iat": issued_at,
            "exp": expires_at,
            "jti": str(uuid.uuid4()),
            "iss": self.settings.auth_jwt_issuer,
            "aud": self.settings.auth_jwt_audience,
        }
        token = jwt.encode(payload, self.settings.auth_jwt_secret, algorithm="HS256")
        return token, int((expires_at - issued_at).total_seconds())

    def decode_access_token(self, token: str) -> AccessTokenClaims:
        payload = jwt.decode(
            token,
            self.settings.auth_jwt_secret,
            algorithms=["HS256"],
            issuer=self.settings.auth_jwt_issuer,
            audience=self.settings.auth_jwt_audience,
            options={"require": ["sub", "sid", "iat", "exp", "jti", "iss", "aud"]},
        )
        try:
            return AccessTokenClaims(
                user_id=uuid.UUID(str(payload["sub"])),
                session_id=uuid.UUID(str(payload["sid"])),
                token_id=str(payload["jti"]),
                issued_at=datetime.fromtimestamp(float(payload["iat"]), tz=timezone.utc),
                expires_at=datetime.fromtimestamp(float(payload["exp"]), tz=timezone.utc),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise jwt.InvalidTokenError("invalid access-token claims") from exc


class InMemoryRateLimiter:
    """Small local/test limiter; production can replace this seam with Redis."""

    def __init__(self) -> None:
        self._attempts: dict[str, list[float]] = {}

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = utcnow().timestamp()
        timestamps = [t for t in self._attempts.get(key, []) if now - t < window_seconds]
        if len(timestamps) >= limit:
            self._attempts[key] = timestamps
            return False
        timestamps.append(now)
        self._attempts[key] = timestamps
        return True

    def clear(self, key: str) -> None:
        self._attempts.pop(key, None)


class RateLimitUnavailable(RuntimeError):
    """Raised when the configured shared limiter cannot be reached."""


class RateLimiter(Protocol):
    def allow(self, key: str, limit: int, window_seconds: int) -> bool: ...

    def clear(self, key: str) -> None: ...


def rate_limit_key(scope: str, source: str, account: Optional[str] = None) -> str:
    """Build privacy-conscious keys without storing source/account plaintext."""
    source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]
    account_digest = (
        hashlib.sha256(account.casefold().encode("utf-8")).hexdigest()[:32]
        if account is not None
        else "none"
    )
    return f"prufai:auth:rl:{scope}:{source_digest}:{account_digest}"


class RedisRateLimiter:
    """Distributed fixed-window limiter backed by an atomic Redis Lua script."""

    _increment_script = """
    local current = redis.call('INCR', KEYS[1])
    if current == 1 then
      redis.call('EXPIRE', KEYS[1], ARGV[1])
    end
    return current
    """

    def __init__(self, redis_url: str, *, client: Optional[Redis] = None) -> None:
        self.client: Redis = client or Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            retry=Retry(NoBackoff(), 0),
        )

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        try:
            value = self.client.eval(self._increment_script, 1, key, str(max(1, window_seconds)))
            current = int(cast(str, value))
        except RedisError as exc:
            raise RateLimitUnavailable("authentication rate-limit backend unavailable") from exc
        return current <= limit

    def clear(self, key: str) -> None:
        try:
            self.client.delete(key)
        except RedisError as exc:
            raise RateLimitUnavailable("authentication rate-limit backend unavailable") from exc
