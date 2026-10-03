from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.db import get_db_session
from app.models import AssessmentRubric, StandardPackVersion
from app.models.enums import HumanReviewStatus
from app.writing_assessment.service import RubricError, RubricService, SubjectiveAssessmentService

writing_router = APIRouter(prefix="/api/v1/organisations", tags=["writing-assessment"])


def _context(principal: Principal) -> TenantContext:
    if principal.tenant_context is None:
        raise HTTPException(status_code=403, detail="organisation context required")
    return principal.tenant_context


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, (AuthorizationError, PermissionError)):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


@writing_router.post("/{organisation_id}/rubrics")
def create_rubric(
    organisation_id: uuid.UUID,
    payload: dict[str, Any],
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        AuthorizationService.require_permission(context, "rubric.manage", organisation_id)
        rubric = RubricService.create_rubric(
            session,
            code=str(payload["code"]),
            name=str(payload["name"]),
            organisation_id=organisation_id,
            context=context,
        )
        session.commit()
        return {"id": str(rubric.id), "code": rubric.code, "status": rubric.status.value}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@writing_router.post("/{organisation_id}/rubrics/{rubric_id}/versions")
def create_rubric_version(
    organisation_id: uuid.UUID,
    rubric_id: uuid.UUID,
    payload: dict[str, Any],
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        AuthorizationService.require_permission(context, "rubric.manage", organisation_id)
        rubric = session.get(AssessmentRubric, rubric_id)
        pack = session.get(StandardPackVersion, uuid.UUID(str(payload["standard_pack_version_id"])))
        if rubric is None or rubric.organisation_id != organisation_id or pack is None:
            raise RubricError("rubric or StandardPackVersion is not available")
        version = RubricService.create_version(
            session,
            rubric=rubric,
            standard_pack=pack,
            version_label=str(payload["version_label"]),
            criteria=payload["criteria"],
            performance_levels=payload["performance_levels"],
            aggregation_policy=payload["aggregation_policy"],
            evidence_policy=payload["evidence_policy"],
            ai_assessment_policy=payload.get("ai_assessment_policy", {"all_require_review": True}),
            human_review_policy=payload.get("human_review_policy", {"required": True}),
            evidence_bindings=payload.get("evidence_bindings", []),
            context=context,
        )
        session.commit()
        return {"id": str(version.id), "status": version.status.value, "checksum": version.checksum}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@writing_router.post("/{organisation_id}/subjective-assessments")
def create_assessment(
    organisation_id: uuid.UUID,
    payload: dict[str, Any],
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        version = SubjectiveAssessmentService.create(
            session,
            context,
            attempt_id=uuid.UUID(str(payload["attempt_id"])),
            binding_id=uuid.UUID(str(payload["exam_version_item_binding_id"])),
            rubric_version_id=uuid.UUID(str(payload["rubric_version_id"])),
        )
        session.commit()
        return {"id": str(version.id), "status": version.status.value, "checksum": version.checksum}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@writing_router.post("/{organisation_id}/subjective-assessments/{version_id}/propose")
def propose_assessment(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: dict[str, Any],
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        version = SubjectiveAssessmentService.propose(
            session,
            context,
            version_id,
            payload,
            ai_run_id=uuid.UUID(str(payload["ai_run_id"])) if payload.get("ai_run_id") else None,
        )
        session.commit()
        return {"id": str(version.id), "status": version.status.value, "checksum": version.checksum}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@writing_router.post("/{organisation_id}/subjective-assessments/{version_id}/approve")
def approve_assessment(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: Optional[dict[str, Any]] = None,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        version = SubjectiveAssessmentService.approve(
            session, context, version_id, (payload or {}).get("corrections")
        )
        session.commit()
        return {
            "id": str(version.id),
            "status": version.status.value,
            "approved_score": str(version.approved_score),
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@writing_router.post("/{organisation_id}/subjective-assessments/{version_id}/decision")
def decide_assessment(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: dict[str, Any],
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        decision = HumanReviewStatus(str(payload["decision"]))
        version = SubjectiveAssessmentService.decide(
            session, context, version_id, decision, payload.get("reason")
        )
        session.commit()
        return {"id": str(version.id), "status": version.status.value}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc
