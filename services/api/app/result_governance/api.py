from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, TenantContext
from app.db import get_db_session
from app.models import ResultAppeal, ResultCorrection, ResultModerationCase
from app.models.enums import AppealDecision, AppealStatus, CorrectionType, ModerationStatus
from app.result_governance.service import ResultGovernanceService

candidate_router = APIRouter(prefix="/api/v1/candidate", tags=["candidate-appeals"])
institution_router = APIRouter(prefix="/api/v1/organisations", tags=["result-governance"])


class AppealSubmission(BaseModel):
    category: str = Field(min_length=1, max_length=80)
    grounds: str = Field(min_length=1, max_length=10000)
    operation_id: str = Field(min_length=1, max_length=120)


class InformationRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000)


class AdjudicationRequest(BaseModel):
    appeal_version_number: int = Field(gt=0)
    appeal_checksum: str = Field(min_length=64, max_length=64)
    evidence_bundle_checksum: str = Field(min_length=64, max_length=64)
    decision: AppealDecision
    operation_id: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=10000)


class CorrectionRequest(BaseModel):
    appeal_version_number: int = Field(gt=0)
    appeal_checksum: str = Field(min_length=64, max_length=64)
    evidence_bundle_checksum: str = Field(min_length=64, max_length=64)
    correction_type: CorrectionType
    operation_id: str = Field(min_length=1, max_length=120)
    corrections: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ModerationRequest(BaseModel):
    result_version_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=120)
    operation_id: str = Field(min_length=1, max_length=120)
    priority: str = Field(default="NORMAL", max_length=30)


class ModerationDecisionRequest(BaseModel):
    result_checksum: str = Field(min_length=64, max_length=64)
    outcome: ModerationStatus
    operation_id: str = Field(min_length=1, max_length=120)


def _context(principal: Principal) -> TenantContext:
    if principal.tenant_context is None:
        raise HTTPException(status_code=403, detail="organisation context required")
    return principal.tenant_context


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, (AuthorizationError, PermissionError)):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


def _appeal_projection(appeal: ResultAppeal, *, include_private: bool) -> dict[str, Any]:
    latest = appeal.versions[-1] if appeal.versions else None
    output: dict[str, Any] = {
        "id": str(appeal.id),
        "status": appeal.status.value,
        "category": appeal.category,
        "result_id": str(appeal.result_id),
        "challenged_result_version_id": str(appeal.challenged_result_version_id),
        "challenged_result_checksum": appeal.challenged_result_checksum,
        "current_version_number": appeal.current_version_number,
        "current_checksum": appeal.current_checksum,
        "corrected_result_version_id": str(appeal.corrected_result_version_id)
        if appeal.corrected_result_version_id
        else None,
    }
    if include_private and latest:
        output.update(
            {
                "grounds": latest.grounds,
                "additional_information": latest.additional_information,
                "decision": latest.decision.value if latest.decision else None,
                "decision_reason": latest.decision_reason,
                "evidence_bundle_checksum": latest.evidence_bundle_checksum,
            }
        )
    return output


@candidate_router.post("/results/{result_version_id}/appeals")
def submit_appeal(
    result_version_id: uuid.UUID,
    payload: AppealSubmission,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        appeal = ResultGovernanceService.submit_appeal(
            session,
            _context(principal),
            result_version_id=result_version_id,
            category=payload.category,
            grounds=payload.grounds,
            operation_id=payload.operation_id,
        )
        session.commit()
        return _appeal_projection(appeal, include_private=True)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@candidate_router.get("/appeals")
def list_candidate_appeals(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    context = _context(principal)
    rows = session.scalars(
        select(ResultAppeal).where(
            ResultAppeal.organisation_id == context.organisation_id,
            ResultAppeal.candidate_user_id == context.user_id,
        )
    )
    return [_appeal_projection(row, include_private=True) for row in rows]


@candidate_router.get("/appeals/{appeal_id}")
def get_candidate_appeal(
    appeal_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    appeal = session.get(ResultAppeal, appeal_id)
    if (
        appeal is None
        or appeal.organisation_id != context.organisation_id
        or appeal.candidate_user_id != context.user_id
    ):
        raise HTTPException(status_code=404, detail="appeal not found")
    return _appeal_projection(appeal, include_private=True)


@candidate_router.post("/appeals/{appeal_id}/information")
def candidate_information(
    appeal_id: uuid.UUID,
    payload: InformationRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    try:
        entry = ResultGovernanceService.add_information(
            session, _context(principal), appeal_id=appeal_id, message=payload.message
        )
        session.commit()
        return {"id": str(entry.id), "direction": entry.direction}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@candidate_router.post("/appeals/{appeal_id}/withdraw")
def withdraw_appeal(
    appeal_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    try:
        appeal = ResultGovernanceService.transition(
            session, _context(principal), appeal_id, AppealStatus.WITHDRAWN
        )
        session.commit()
        return {"id": str(appeal.id), "status": appeal.status.value}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@institution_router.get("/{organisation_id}/result-moderation")
def moderation_queue(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    context = _context(principal)
    try:
        from app.authorization import AuthorizationService

        AuthorizationService.require_permission(context, "result.moderate", organisation_id)
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc
    rows = session.scalars(
        select(ResultModerationCase)
        .where(ResultModerationCase.organisation_id == organisation_id)
        .order_by(ResultModerationCase.created_at.desc())
    )
    return [
        {
            "id": str(row.id),
            "result_version_id": str(row.result_version_id),
            "result_checksum": row.result_checksum,
            "status": row.status.value,
            "reason": row.reason,
            "priority": row.priority,
        }
        for row in rows
    ]


@institution_router.get("/{organisation_id}/result-moderation/{case_id}")
def moderation_detail(
    organisation_id: uuid.UUID,
    case_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    try:
        from app.authorization import AuthorizationService

        AuthorizationService.require_permission(context, "result.moderate", organisation_id)
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc
    case = session.get(ResultModerationCase, case_id)
    if case is None or case.organisation_id != organisation_id:
        raise HTTPException(status_code=404, detail="moderation case not found")
    return {
        "id": str(case.id),
        "result_id": str(case.result_id),
        "result_version_id": str(case.result_version_id),
        "result_checksum": case.result_checksum,
        "status": case.status.value,
        "reason": case.reason,
        "priority": case.priority,
        "outcome": case.outcome,
    }


@institution_router.post("/{organisation_id}/result-moderation")
def create_moderation_case(
    organisation_id: uuid.UUID,
    payload: ModerationRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        case = ResultGovernanceService.create_moderation_case(
            session,
            context,
            result_version_id=payload.result_version_id,
            reason=payload.reason,
            operation_id=payload.operation_id,
            priority=payload.priority,
        )
        session.commit()
        return {"id": str(case.id), "status": case.status.value}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@institution_router.post("/{organisation_id}/result-moderation/{case_id}/decision")
def decide_moderation(
    organisation_id: uuid.UUID,
    case_id: uuid.UUID,
    payload: ModerationDecisionRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    if _context(principal).organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        case = ResultGovernanceService.decide_moderation(
            session,
            _context(principal),
            case_id=case_id,
            result_checksum=payload.result_checksum,
            outcome=payload.outcome,
            operation_id=payload.operation_id,
        )
        session.commit()
        return {"id": str(case.id), "status": case.status.value}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@institution_router.get("/{organisation_id}/appeals")
def appeals_queue(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    context = _context(principal)
    try:
        from app.authorization import AuthorizationService

        AuthorizationService.require_permission(context, "appeal.read", organisation_id)
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc
    rows = session.scalars(
        select(ResultAppeal)
        .where(ResultAppeal.organisation_id == organisation_id)
        .order_by(ResultAppeal.created_at.desc())
    )
    return [_appeal_projection(row, include_private=False) for row in rows]


@institution_router.get("/{organisation_id}/appeals/{appeal_id}")
def appeal_detail(
    organisation_id: uuid.UUID,
    appeal_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    try:
        from app.authorization import AuthorizationService

        AuthorizationService.require_permission(context, "appeal.read", organisation_id)
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc
    appeal = session.get(ResultAppeal, appeal_id)
    if appeal is None or appeal.organisation_id != organisation_id:
        raise HTTPException(status_code=404, detail="appeal not found")
    result = _appeal_projection(appeal, include_private=True)
    correction = session.scalar(
        select(ResultCorrection)
        .where(ResultCorrection.appeal_id == appeal.id)
        .order_by(ResultCorrection.created_at.desc())
    )
    result["correction"] = (
        {
            "id": str(correction.id),
            "type": correction.correction_type.value,
            "status": correction.status.value,
            "new_scoring_run_id": str(correction.new_scoring_run_id)
            if correction.new_scoring_run_id
            else None,
            "new_result_version_id": str(correction.new_result_version_id)
            if correction.new_result_version_id
            else None,
        }
        if correction
        else None
    )
    return result


@institution_router.post("/{organisation_id}/appeals/{appeal_id}/review")
def start_appeal_review(
    organisation_id: uuid.UUID,
    appeal_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    if _context(principal).organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        appeal = ResultGovernanceService.transition(
            session, _context(principal), appeal_id, AppealStatus.UNDER_REVIEW
        )
        session.commit()
        return {"id": str(appeal.id), "status": appeal.status.value}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@institution_router.post("/{organisation_id}/appeals/{appeal_id}/adjudicate")
def adjudicate_appeal(
    organisation_id: uuid.UUID,
    appeal_id: uuid.UUID,
    payload: AdjudicationRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    if _context(principal).organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        appeal = ResultGovernanceService.adjudicate(
            session,
            _context(principal),
            appeal_id=appeal_id,
            appeal_version_number=payload.appeal_version_number,
            appeal_checksum=payload.appeal_checksum,
            evidence_bundle_checksum=payload.evidence_bundle_checksum,
            decision=payload.decision,
            operation_id=payload.operation_id,
            reason=payload.reason,
        )
        session.commit()
        return {"id": str(appeal.id), "status": appeal.status.value}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@institution_router.post("/{organisation_id}/appeals/{appeal_id}/information")
def reviewer_information(
    organisation_id: uuid.UUID,
    appeal_id: uuid.UUID,
    payload: InformationRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    if _context(principal).organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        entry = ResultGovernanceService.add_information(
            session, _context(principal), appeal_id=appeal_id, message=payload.message
        )
        session.commit()
        return {"id": str(entry.id), "direction": entry.direction}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@institution_router.post("/{organisation_id}/appeals/{appeal_id}/correction")
def initiate_correction(
    organisation_id: uuid.UUID,
    appeal_id: uuid.UUID,
    payload: CorrectionRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    if _context(principal).organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        correction = ResultGovernanceService.initiate_correction(
            session,
            _context(principal),
            appeal_id=appeal_id,
            appeal_version_number=payload.appeal_version_number,
            appeal_checksum=payload.appeal_checksum,
            evidence_bundle_checksum=payload.evidence_bundle_checksum,
            correction_type=payload.correction_type,
            operation_id=payload.operation_id,
            corrections=payload.corrections,
        )
        session.commit()
        return {
            "id": str(correction.id),
            "status": correction.status.value,
            "new_scoring_run_id": str(correction.new_scoring_run_id)
            if correction.new_scoring_run_id
            else None,
            "new_result_version_id": str(correction.new_result_version_id)
            if correction.new_result_version_id
            else None,
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc
