import uuid
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.flows import FakeNotificationSink, InvitationService, PasswordResetService
from app.auth.security import (
    InMemoryRateLimiter,
    RateLimiter,
    RateLimitUnavailable,
    RedisRateLimiter,
    TokenService,
    generate_opaque_token,
    rate_limit_key,
)
from app.auth.service import AuthenticationError, AuthenticationService
from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.config import Settings, get_settings
from app.db import get_db_session
from app.models import AuthSession, MembershipRole, OrganisationMembership, Role, User
from app.models.enums import AuthSessionStatus, MembershipStatus, RoleScope

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
org_router = APIRouter(prefix="/api/v1/organisations", tags=["organisation-authentication"])
bearer = HTTPBearer(auto_error=False)
_notifications = FakeNotificationSink()
_rate_limiters: dict[tuple[str, str], RateLimiter] = {}


def auth_settings_dependency() -> Settings:
    return get_settings()


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InvitationAcceptRequest(BaseModel):
    token: str = Field(min_length=20)
    password: Optional[str] = None
    display_name: Optional[str] = None


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=20)
    new_password: str


class InvitationCreateRequest(BaseModel):
    email: str
    role_keys: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class Principal:
    user: User
    session: AuthSession
    tenant_context: Optional[TenantContext]


def _rate_limiter(settings: Settings) -> RateLimiter:
    key = (settings.auth_rate_limit_backend, settings.redis_url)
    if key not in _rate_limiters:
        _rate_limiters[key] = (
            RedisRateLimiter(settings.redis_url)
            if settings.auth_rate_limit_backend == "redis"
            else InMemoryRateLimiter()
        )
    return _rate_limiters[key]


def _service(settings: Settings) -> AuthenticationService:
    return AuthenticationService(settings, rate_limiter=_rate_limiter(settings))


def _set_auth_cookies(response: Response, settings: Settings, refresh_token: str) -> None:
    csrf_token = generate_opaque_token()
    response.set_cookie(
        settings.auth_refresh_cookie_name,
        refresh_token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path=settings.auth_cookie_path,
        max_age=settings.auth_refresh_token_ttl_days * 86400,
    )
    response.set_cookie(
        settings.auth_csrf_cookie_name,
        csrf_token,
        httponly=False,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path=settings.auth_cookie_path,
        max_age=settings.auth_refresh_token_ttl_days * 86400,
    )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.auth_refresh_cookie_name, path=settings.auth_cookie_path)
    response.delete_cookie(settings.auth_csrf_cookie_name, path=settings.auth_cookie_path)


def _require_csrf(request: Request, settings: Settings) -> None:
    cookie = request.cookies.get(settings.auth_csrf_cookie_name)
    header = request.headers.get("X-PrufAI-CSRF")
    if not cookie or not header or cookie != header:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")


def get_principal(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(auth_settings_dependency),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required"
        )
    try:
        claims = TokenService(settings).decode_access_token(credentials.credentials)
        auth = _service(settings)
        auth_session = auth.validate_session(session, claims.session_id)
        if auth_session.user_id != claims.user_id:
            raise AuthenticationError("token subject mismatch")
        user = session.get(User, auth_session.user_id)
        if user is None:
            raise AuthenticationError("user is not active")
        organisation_header = request.headers.get("X-PrufAI-Organisation-ID")
        context = None
        if organisation_header:
            context = build_tenant_context(session, user.id, uuid.UUID(organisation_header))
        else:
            try:
                context = build_tenant_context(session, user.id)
            except AuthorizationError:
                context = None
        return Principal(user=user, session=auth_session, tenant_context=context)
    except (jwt.InvalidTokenError, AuthenticationError, ValueError, PermissionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid authentication"
        ) from exc


@router.post("/login")
def login(
    response: Response,
    request: Request,
    payload: LoginRequest = Body(..., embed=False),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(auth_settings_dependency),
) -> dict[str, object]:
    try:
        result = _service(settings).login(
            session,
            email=payload.email,
            password=payload.password,
            source=request.client.host if request.client else "unknown",
            user_agent=request.headers.get("user-agent"),
        )
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication rate limiting is temporarily unavailable",
        ) from exc
    except (AuthenticationError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password"
        ) from exc
    _set_auth_cookies(response, settings, result.refresh_token)
    return {
        "access_token": result.access_token,
        "token_type": "bearer",
        "expires_in": result.access_expires_in,
        "user": {
            "id": str(result.user.id),
            "email": result.user.email,
            "display_name": result.user.display_name,
        },
    }


@router.post("/refresh")
def refresh(
    request: Request,
    response: Response,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(auth_settings_dependency),
) -> dict[str, object]:
    _require_csrf(request, settings)
    source = request.client.host if request.client else "unknown"
    try:
        allowed = _rate_limiter(settings).allow(
            rate_limit_key("refresh", source),
            settings.auth_rate_limit_refresh_attempts,
            settings.auth_rate_limit_window_seconds,
        )
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication rate limiting is temporarily unavailable",
        ) from exc
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="try again later")
    raw = request.cookies.get(settings.auth_refresh_cookie_name)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh authentication required"
        )
    try:
        result = _service(settings).refresh(session, raw)
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication rate limiting is temporarily unavailable",
        ) from exc
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token"
        ) from exc
    _set_auth_cookies(response, settings, result.refresh_token)
    return {
        "access_token": result.access_token,
        "token_type": "bearer",
        "expires_in": result.access_expires_in,
    }


@router.get("/csrf")
def csrf_bootstrap(
    request: Request,
    response: Response,
    settings: Settings = Depends(auth_settings_dependency),
) -> dict[str, str]:
    """Read the existing CSRF cookie through its narrow same-origin auth path."""
    response.headers["Cache-Control"] = "no-store"
    token = request.cookies.get(settings.auth_csrf_cookie_name)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="refresh authentication required",
            headers={"Cache-Control": "no-store"},
        )
    return {"csrf_token": token}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(auth_settings_dependency),
) -> Response:
    _require_csrf(request, settings)
    _service(settings).revoke_session(session, principal.session, reason="logout")
    _clear_auth_cookies(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me")
def me(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    memberships = list(
        session.scalars(
            select(OrganisationMembership).where(
                OrganisationMembership.user_id == principal.user.id
            )
        )
    )
    membership_payloads: list[dict[str, object]] = []
    for membership in memberships:
        permissions: list[str] = []
        role_keys: list[str] = []
        if membership.status is MembershipStatus.ACTIVE:
            context = build_tenant_context(session, principal.user.id, membership.organisation_id)
            permissions = sorted(context.permissions)
            role_keys = list(
                session.scalars(
                    select(Role.key)
                    .join(MembershipRole, MembershipRole.role_id == Role.id)
                    .where(
                        MembershipRole.membership_id == membership.id,
                        Role.scope == RoleScope.TENANT,
                    )
                    .order_by(Role.key)
                )
            )
        membership_payloads.append(
            {
                "organisation_id": str(membership.organisation_id),
                "organisation_name": membership.organisation.name,
                "organisation_slug": membership.organisation.slug,
                "status": membership.status.value,
                "role_keys": role_keys,
                "permissions": permissions,
            }
        )
    # Platform capabilities are independent of any selected tenant. A platform
    # identity is never given a fabricated organisation membership by this view.
    try:
        platform_permissions = sorted(
            build_tenant_context(session, principal.user.id).platform_permissions
        )
    except AuthorizationError:
        platform_permissions = []
    return {
        "id": str(principal.user.id),
        "email": principal.user.email,
        "display_name": principal.user.display_name,
        "status": principal.user.status.value,
        "memberships": membership_payloads,
        "platform_scope": bool(platform_permissions),
        "platform_permissions": platform_permissions,
    }


@router.get("/sessions")
def list_sessions(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, object]]:
    rows = session.scalars(
        select(AuthSession).where(
            AuthSession.user_id == principal.user.id,
            AuthSession.status == AuthSessionStatus.ACTIVE,
        )
    )
    return [
        {
            "id": str(item.id),
            "created_at": item.created_at,
            "last_seen_at": item.last_seen_at,
            "expires_at": item.expires_at,
            "authentication_method": item.authentication_method,
            "current": item.id == principal.session.id,
        }
        for item in rows
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(
    session_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(auth_settings_dependency),
) -> Response:
    target = session.get(AuthSession, session_id)
    if target is None or target.user_id != principal.user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    _service(settings).revoke_session(session, target, reason="user_requested")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invitations/accept")
def accept_invitation(
    payload: InvitationAcceptRequest = Body(..., embed=False),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    try:
        user = InvitationService(_notifications).accept(
            session,
            token=payload.token,
            password=payload.password,
            display_name=payload.display_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"user_id": str(user.id), "status": user.status.value}


@router.post("/password-reset/request")
def password_reset_request(
    payload: PasswordResetRequest = Body(..., embed=False),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(auth_settings_dependency),
) -> dict[str, str]:
    try:
        allowed = _rate_limiter(settings).allow(
            rate_limit_key("password-reset", payload.email),
            settings.auth_rate_limit_reset_attempts,
            settings.auth_rate_limit_window_seconds,
        )
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication rate limiting is temporarily unavailable",
        ) from exc
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="try again later")
    PasswordResetService(_notifications).request(session, email=payload.email, settings=settings)
    return {"message": "If an account exists, password reset instructions have been sent."}


@router.post("/password-reset/confirm")
def password_reset_confirm(
    payload: PasswordResetConfirmRequest = Body(..., embed=False),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(auth_settings_dependency),
) -> dict[str, str]:
    try:
        user = PasswordResetService(_notifications).confirm(
            session,
            token=payload.token,
            new_password=payload.new_password,
            authentication=_service(settings),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"user_id": str(user.id), "message": "Password reset completed; please log in again."}


@org_router.post("/{organisation_id}/invitations")
def create_invitation(
    organisation_id: uuid.UUID,
    payload: InvitationCreateRequest = Body(..., embed=False),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    if principal.tenant_context is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="organisation context required"
        )
    try:
        invitation, _ = InvitationService(_notifications).create(
            session,
            principal.tenant_context,
            organisation_id=organisation_id,
            email=payload.email,
            role_keys=payload.role_keys,
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return {
        "id": str(invitation.id),
        "status": invitation.status.value,
        "expires_at": invitation.expires_at,
    }
