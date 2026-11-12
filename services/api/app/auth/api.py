import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Optional

import jwt
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.auth.delivery import EnrollmentDelivery, EnrollmentMessage, enrollment_delivery
from app.auth.flows import CANDIDATE_ROLE, EnrollmentError, InvitationService, PasswordResetService
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
from app.models import (
    AuthSession,
    MembershipRole,
    OrganisationMembership,
    Role,
    ScopedSourceReviewGrant,
    User,
)
from app.models.enums import AuthSessionStatus, MembershipStatus, RoleScope
from app.operations.readiness import is_database_unavailable
from app.operations.telemetry import Failure, Signal, record
from app.services.identity import normalize_email


class SecretRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handle = super().get_route_handler()

        async def redact_validation(request: Request) -> Response:
            try:
                return await handle(request)
            except RequestValidationError:
                # FastAPI's default validation response echoes the submitted input.
                raise HTTPException(status_code=422, detail={"code": "INVALID_INPUT"}) from None

        return redact_validation


router = APIRouter(prefix="/api/v1/auth", tags=["authentication"], route_class=SecretRoute)
org_router = APIRouter(
    prefix="/api/v1/organisations", tags=["organisation-authentication"], route_class=SecretRoute
)
bearer = HTTPBearer(auto_error=False)
_rate_limiters: dict[tuple[str, str], RateLimiter] = {}


def auth_settings_dependency() -> Settings:
    return get_settings()


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InvitationAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=20, max_length=128)
    organisation_id: uuid.UUID
    password: SecretStr = Field(max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=200)


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=320)


class PasswordResetConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=20, max_length=128)
    new_password: SecretStr = Field(max_length=128)


class InvitationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=320)
    role_keys: list[str] = Field(default_factory=lambda: [CANDIDATE_ROLE], max_length=1)


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
        record(Signal.AUTHENTICATION, Failure.UNAVAILABLE)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication rate limiting is temporarily unavailable",
        ) from exc
    except DBAPIError as exc:
        if not is_database_unavailable(exc):
            raise
        record(Signal.AUTHENTICATION, Failure.UNAVAILABLE)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AUTH_DATABASE_UNAVAILABLE"},
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
        "scoped_source_review": session.scalar(
            select(ScopedSourceReviewGrant.id)
            .where(ScopedSourceReviewGrant.reviewer_user_id == principal.user.id)
            .limit(1)
        )
        is not None,
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


def _delivery_enabled(settings: Settings, delivery: EnrollmentDelivery | None) -> bool:
    return (
        settings.environment in {"development", "test"}
        and settings.auth_enrollment_delivery_mode == "TEST_ONLY"
        and delivery is not None
    )


def _require_delivery(
    settings: Settings, delivery: EnrollmentDelivery | None
) -> EnrollmentDelivery:
    if delivery is None or not _delivery_enabled(settings, delivery):
        raise HTTPException(status_code=503, detail={"code": "DELIVERY_DISABLED"})
    return delivery


def _enrollment_limit(
    request: Request, settings: Settings, scope: str, account: str | None = None
) -> None:
    source = request.client.host if request.client else "unknown"
    limit = settings.auth_rate_limit_reset_attempts
    window = settings.auth_rate_limit_window_seconds
    limiter = _rate_limiter(settings)
    try:
        if not limiter.allow(rate_limit_key(scope, source), limit * 10, window) or (
            account is not None
            and not limiter.allow(rate_limit_key(scope, "account", account), limit, window)
        ):
            raise HTTPException(status_code=429, detail={"code": "TRY_LATER"})
    except RateLimitUnavailable as exc:
        record(Signal.AUTHENTICATION, Failure.UNAVAILABLE)
        raise HTTPException(status_code=503, detail={"code": "AUTH_UNAVAILABLE"}) from exc


@router.get("/enrollment")
def enrollment_options(
    response: Response,
    settings: Settings = Depends(auth_settings_dependency),
    delivery: EnrollmentDelivery | None = Depends(enrollment_delivery),
) -> dict[str, str | bool]:
    response.headers["Cache-Control"] = "no-store"
    return {
        "enabled": _delivery_enabled(settings, delivery),
        "support_contact": settings.auth_enrollment_support_contact.strip(),
    }


@router.post("/invitations/accept")
def accept_invitation(
    request: Request,
    payload: InvitationAcceptRequest = Body(..., embed=False),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(auth_settings_dependency),
    delivery: EnrollmentDelivery | None = Depends(enrollment_delivery),
) -> dict[str, str]:
    _require_delivery(settings, delivery)
    _enrollment_limit(request, settings, "invitation-accept")
    try:
        user = InvitationService.accept(
            session,
            token=payload.token.get_secret_value(),
            organisation_id=payload.organisation_id,
            password=payload.password.get_secret_value(),
            display_name=payload.display_name,
        )
    except EnrollmentError as exc:
        raise HTTPException(status_code=400, detail={"code": str(exc)}) from exc
    return {"user_id": str(user.id), "status": user.status.value}


@router.post("/password-reset/request", status_code=202)
def password_reset_request(
    request: Request,
    payload: PasswordResetRequest = Body(..., embed=False),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(auth_settings_dependency),
    delivery: EnrollmentDelivery | None = Depends(enrollment_delivery),
) -> dict[str, str]:
    adapter = _require_delivery(settings, delivery)
    email = payload.email.strip().casefold()
    _enrollment_limit(request, settings, "password-reset", email)
    try:
        normalize_email(email)
    except ValueError:
        # Malformed addresses receive the same public response as unknown accounts.
        return {"status": "accepted"}
    token = PasswordResetService.request(session, email=email)
    if token is not None:
        try:
            adapter.send(EnrollmentMessage("password_reset", email, token))
        except OSError:
            # A delivery error must not disclose whether this account exists.
            record(Signal.AUTHENTICATION, Failure.UNAVAILABLE)
    return {"status": "accepted"}


@router.post("/password-reset/confirm")
def password_reset_confirm(
    request: Request,
    response: Response,
    payload: PasswordResetConfirmRequest = Body(..., embed=False),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(auth_settings_dependency),
    delivery: EnrollmentDelivery | None = Depends(enrollment_delivery),
) -> dict[str, str]:
    _require_delivery(settings, delivery)
    _enrollment_limit(request, settings, "password-reset-confirm")
    try:
        PasswordResetService.confirm(
            session,
            token=payload.token.get_secret_value(),
            new_password=payload.new_password.get_secret_value(),
            authentication=_service(settings),
        )
    except EnrollmentError as exc:
        raise HTTPException(status_code=400, detail={"code": str(exc)}) from exc
    _clear_auth_cookies(response, settings)
    return {"status": "password_changed"}


@org_router.post("/{organisation_id}/invitations")
def create_invitation(
    organisation_id: uuid.UUID,
    payload: InvitationCreateRequest = Body(..., embed=False),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(auth_settings_dependency),
    delivery: EnrollmentDelivery | None = Depends(enrollment_delivery),
) -> dict[str, object]:
    if principal.tenant_context is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="organisation context required"
        )
    adapter = _require_delivery(settings, delivery)
    try:
        email = normalize_email(payload.email)
        invitation, token = InvitationService.create(
            session,
            principal.tenant_context,
            organisation_id=organisation_id,
            email=email,
            role_keys=payload.role_keys,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail={"code": "INVITATION_DENIED"}) from exc
    except EnrollmentError as exc:
        raise HTTPException(status_code=400, detail={"code": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "INVALID_INPUT"}) from exc
    try:
        adapter.send(EnrollmentMessage("invitation", email, token, organisation_id))
    except OSError as exc:
        record(Signal.AUTHENTICATION, Failure.UNAVAILABLE)
        raise HTTPException(status_code=503, detail={"code": "AUTH_UNAVAILABLE"}) from exc
    return {
        "id": str(invitation.id),
        "status": invitation.status.value,
        "expires_at": invitation.expires_at,
    }
