from collections.abc import Callable
from typing import Annotated, Literal, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.db import get_db_session
from app.scoped_source_review import service
from app.scoped_source_review.schemas import CreateGrant, Operation, ReviewDecision


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/api/v1/scoped-source-review",
    tags=["scoped-source-review"],
    dependencies=[Depends(_no_store)],
)
Actor = Annotated[Principal, Depends(get_principal)]
Database = Annotated[Session, Depends(get_db_session)]
T = TypeVar("T")


def _execute(
    session: Session,
    principal: Principal,
    action: str,
    work: Callable[[], T],
    *,
    grant_id: UUID | None = None,
    version_id: UUID | None = None,
    operation_id: UUID | None = None,
    pin: str | None = None,
) -> T:
    try:
        result = work()
        session.commit()
    except service.ScopedReviewError as exc:
        service.audit(
            session,
            principal.user.id,
            "rejected" if action == "ISSUE" else "denied",
            grant_id=grant_id,
            version_id=version_id,
            operation_id=operation_id,
            pin=pin,
            reason=exc.code,
            operation=action,
        )
        session.commit()
        detail = "Scoped source review access denied" if exc.status == 403 else exc.code
        raise HTTPException(exc.status, detail, headers={"Cache-Control": "no-store"}) from exc
    except IntegrityError as exc:
        session.rollback()
        service.audit(
            session,
            principal.user.id,
            "rejected" if action == "ISSUE" else "denied",
            grant_id=grant_id,
            version_id=version_id,
            operation_id=operation_id,
            pin=pin,
            reason="OPERATION_CONFLICT",
            operation=action,
        )
        session.commit()
        raise HTTPException(
            409, "OPERATION_CONFLICT", headers={"Cache-Control": "no-store"}
        ) from exc
    return result


@router.get("/grants")
def owned_grants(principal: Actor, session: Database) -> list[dict[str, object]]:
    return _execute(
        session, principal, "DISCOVER", lambda: service.owned_grants(session, principal)
    )


@router.post("/orgs/{organisation_id}/grants")
def issue(
    organisation_id: UUID,
    payload: CreateGrant,
    principal: Actor,
    session: Database,
) -> dict[str, object]:
    return _execute(
        session,
        principal,
        "ISSUE",
        lambda: service.issue(session, principal, organisation_id, payload),
        operation_id=payload.operation_id,
    )


@router.post("/orgs/{organisation_id}/grants/{grant_id}/{transition}")
def end_access(
    organisation_id: UUID,
    grant_id: UUID,
    transition: Literal["suspend", "revoke"],
    payload: Operation,
    principal: Actor,
    session: Database,
) -> dict[str, object]:
    action = transition.upper()
    return _execute(
        session,
        principal,
        action,
        lambda: service.end_access(session, principal, organisation_id, grant_id, payload, action),
        grant_id=grant_id,
        operation_id=payload.operation_id,
    )


@router.get("/orgs/{organisation_id}/grants/{grant_id}/sources")
def inspect_grant(
    organisation_id: UUID,
    grant_id: UUID,
    principal: Actor,
    session: Database,
) -> dict[str, object]:
    return _execute(
        session,
        principal,
        "LIST",
        lambda: service.inspect_grant(session, principal, organisation_id, grant_id),
        grant_id=grant_id,
    )


@router.get("/orgs/{organisation_id}/grants/{grant_id}/sources/{version_id}")
def inspect_source(
    organisation_id: UUID,
    grant_id: UUID,
    version_id: UUID,
    principal: Actor,
    session: Database,
) -> dict[str, object]:
    return _execute(
        session,
        principal,
        "DETAIL",
        lambda: service.inspect_source(session, principal, organisation_id, grant_id, version_id),
        grant_id=grant_id,
        version_id=version_id,
    )


@router.post("/orgs/{organisation_id}/grants/{grant_id}/sources/{version_id}/decision")
def decide(
    organisation_id: UUID,
    grant_id: UUID,
    version_id: UUID,
    payload: ReviewDecision,
    principal: Actor,
    session: Database,
) -> dict[str, object]:
    return _execute(
        session,
        principal,
        "DECIDE",
        lambda: service.decide(session, principal, organisation_id, grant_id, version_id, payload),
        grant_id=grant_id,
        version_id=version_id,
        operation_id=payload.operation_id,
        pin=payload.expected_metadata_checksum,
    )
