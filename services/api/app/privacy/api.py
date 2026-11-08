from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.config import Settings, get_settings
from app.db import get_db_session
from app.privacy import service
from app.privacy.schemas import CaseIntake, HoldReview, PolicyDraft, PreviewRequest, Verification

NO_STORE = {"Cache-Control": "no-store"}


class PrivacyRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        route = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                response = await route(request)
            except RequestValidationError as exc:
                raise HTTPException(422, "INVALID_PRIVACY_REQUEST", headers=NO_STORE) from exc
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), **NO_STORE}
                raise
            response.headers.update(NO_STORE)
            return response

        return guarded


router = APIRouter(
    prefix="/api/v1/privacy/organisations/{organisation_id}",
    tags=["privacy"],
    route_class=PrivacyRoute,
)
Actor = Annotated[Principal, Depends(get_principal)]
# Use a separate transaction from authentication so discovery can take one
# repeatable read snapshot before accessing any privacy evidence.
Database = Annotated[Session, Depends(get_db_session, use_cache=False)]
Configuration = Annotated[Settings, Depends(get_settings)]


def _run(
    session: Session, actor: Principal, settings: Settings, work: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    try:
        service.require_disposable(session, settings)
    except service.PrivacyError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    try:
        result = work()
        session.commit()
        return result
    except service.PrivacyError as exc:
        session.rollback()
        if exc.status == 403:
            service.audit(session, actor, "access_denied")
            session.commit()
        raise HTTPException(exc.status, str(exc)) from exc
    except DBAPIError as exc:
        session.rollback()
        if isinstance(exc, IntegrityError) or getattr(exc.orig, "sqlstate", None) in {
            "40001",
            "40P01",
        }:
            raise HTTPException(409, "PRIVACY_STATE_CONFLICT") from exc
        raise


@router.get("/cases")
def cases(
    organisation_id: UUID,
    actor: Actor,
    session: Database,
    settings: Configuration,
    after: UUID | None = None,
) -> dict[str, Any]:
    return _run(
        session, actor, settings, lambda: service.list_cases(session, actor, organisation_id, after)
    )


@router.post("/cases")
def intake(
    organisation_id: UUID,
    payload: CaseIntake,
    actor: Actor,
    session: Database,
    settings: Configuration,
) -> dict[str, Any]:
    return _run(
        session, actor, settings, lambda: service.intake(session, actor, organisation_id, payload)
    )


@router.get("/cases/{case_id}")
def case_detail(
    organisation_id: UUID,
    case_id: UUID,
    actor: Actor,
    session: Database,
    settings: Configuration,
) -> dict[str, Any]:
    return _run(
        session, actor, settings, lambda: service.detail(session, actor, organisation_id, case_id)
    )


@router.post("/cases/{case_id}/verify")
def verify(
    organisation_id: UUID,
    case_id: UUID,
    payload: Verification,
    actor: Actor,
    session: Database,
    settings: Configuration,
) -> dict[str, Any]:
    return _run(
        session,
        actor,
        settings,
        lambda: service.verify(session, actor, organisation_id, case_id, payload),
    )


@router.post("/cases/{case_id}/policy-drafts")
def draft_policy(
    organisation_id: UUID,
    case_id: UUID,
    payload: PolicyDraft,
    actor: Actor,
    session: Database,
    settings: Configuration,
) -> dict[str, Any]:
    return _run(
        session,
        actor,
        settings,
        lambda: service.draft_policy(session, actor, organisation_id, case_id, payload),
    )


@router.post("/cases/{case_id}/hold-reviews")
def review_hold(
    organisation_id: UUID,
    case_id: UUID,
    payload: HoldReview,
    actor: Actor,
    session: Database,
    settings: Configuration,
) -> dict[str, Any]:
    return _run(
        session,
        actor,
        settings,
        lambda: service.review_hold(session, actor, organisation_id, case_id, payload),
    )


@router.post("/cases/{case_id}/preview")
def preview(
    organisation_id: UUID,
    case_id: UUID,
    payload: PreviewRequest,
    actor: Actor,
    session: Database,
    settings: Configuration,
) -> dict[str, Any]:
    return _run(
        session,
        actor,
        settings,
        lambda: service.preview(session, actor, organisation_id, case_id, payload, settings),
    )
