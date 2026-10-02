from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

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
