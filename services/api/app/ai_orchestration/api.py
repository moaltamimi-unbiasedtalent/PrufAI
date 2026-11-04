from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai_orchestration.service import AIOrchestrationService
from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.config import Settings, get_settings
from app.db import get_db_session
from app.models import (
    AIModelProfile,
    AIRun,
    AIWorkflowVersion,
    HumanReviewRequest,
)
from app.models.enums import HumanReviewStatus
from app.speaking_execution import registry as speaking_registry
from app.speaking_execution import runtime as speaking_runtime
from app.speech_transcription.execution import snapshot_profile
from app.speech_transcription.profiles import approve_profile, profile_descriptor
from app.writing_execution.registry import (
    approve_execution_profile,
    approved_profile_record,
    create_execution_profile,
    snapshot_execution_profile,
)
from app.writing_execution.runtime import validate_execution_configuration

router = APIRouter(prefix="/api/v1/platform/ai", tags=["ai-orchestration"])


def _require(principal: Principal, permission: str) -> TenantContext:
    context = principal.tenant_context
    if context is None or not context.platform_scope:
        raise HTTPException(status_code=403, detail="platform authority required")
    try:
        AuthorizationService.require_permission(context, permission)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return context


class ModelRequest(BaseModel):
    code: str = Field(min_length=3, max_length=160)
    name: str
    provider: str
    model_name: str
    purpose: str
    configuration: dict[str, Any] = Field(default_factory=dict)


class RunRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=120)


class TranscriptionProfileApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_profile_version: str = Field(pattern=r"^[a-f0-9]{64}$")


class WritingProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=80)
    model_name: str = Field(min_length=1, max_length=160)
    configuration: dict[str, Any]
    system_template: str = Field(min_length=1, max_length=16000)
    instruction_template: Optional[str] = Field(default=None, max_length=16000)


class WritingProfileApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_profile_id: uuid.UUID
    prompt_version_id: uuid.UUID
    agent_version_id: uuid.UUID
    workflow_version_id: uuid.UUID
    expected_profile_version: str = Field(pattern=r"^[a-f0-9]{64}$")


class SpeakingProfileCreate(WritingProfileCreate):
    """AUDIO-only contract, never a caller-selected workflow or purpose."""


class SpeakingProfileApproval(WritingProfileApproval):
    """Independent approval of the exact audio-capable deployment bundle."""


def _speaking_profile_payload(
    session: Session, snapshot: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    keys = (
        "execution_profile_id",
        "profile_version",
        "model_profile_id",
        "model_version",
        "prompt_version_id",
        "prompt_checksum",
        "agent_version_id",
        "agent_checksum",
        "workflow_version_id",
        "workflow_checksum",
        "provider",
        "model_name",
        "adapter_version",
    )
    return {
        **{key: snapshot[key] for key in keys},
        **speaking_runtime.validate_execution_configuration(snapshot, settings),
        "registry_approved": speaking_registry.approved_profile_record(session, snapshot)
        is not None,
        "model_revision": snapshot["configuration"].get("model_revision"),
        "native_structured_outputs": False,
        "output_contract": speaking_runtime.OUTPUT_CONTRACT,
        "external_processing_authorized": False,
    }


@router.post("/speaking-execution-profiles", status_code=status.HTTP_201_CREATED)
def create_speaking_execution_profile(
    payload: SpeakingProfileCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    context = _require(principal, "ai.manage")
    if context.organisation_id is not None:
        raise HTTPException(status_code=403, detail="platform configuration context required")
    try:
        snapshot = speaking_registry.create_execution_profile(
            session, context=context, **payload.model_dump()
        )
        session.commit()
    except AuthorizationError as exc:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="SPEAKING_PROFILE_NAMESPACE_CONFLICT") from exc
    return _speaking_profile_payload(session, snapshot, settings)


@router.post("/speaking-execution-profiles/approve")
def approve_speaking_execution_profile(
    payload: SpeakingProfileApproval,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    context = _require(principal, "ai.manage")
    if context.organisation_id is not None:
        raise HTTPException(status_code=403, detail="platform configuration context required")
    try:
        ids = payload.model_dump(exclude={"expected_profile_version"})
        snapshot = speaking_registry.snapshot_execution_profile(session, **ids)
        approved = speaking_registry.approve_execution_profile(
            session,
            snapshot=snapshot,
            context=context,
            settings=settings,
            expected_profile_version=payload.expected_profile_version,
        )
        session.commit()
    except AuthorizationError as exc:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _speaking_profile_payload(session, approved, settings)


def _writing_profile_payload(
    session: Session, snapshot: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    # Registry content and processing references are not platform metadata projections.
    keys = (
        "execution_profile_id",
        "profile_version",
        "model_profile_id",
        "model_version",
        "prompt_version_id",
        "prompt_checksum",
        "agent_version_id",
        "agent_checksum",
        "workflow_version_id",
        "workflow_checksum",
        "provider",
        "model_name",
        "adapter_version",
    )
    return {
        **{key: snapshot[key] for key in keys},
        **validate_execution_configuration(snapshot, settings),
        "registry_approved": approved_profile_record(session, snapshot) is not None,
        "external_processing_authorized": False,
    }


@router.post("/writing-execution-profiles", status_code=status.HTTP_201_CREATED)
def create_writing_execution_profile(
    payload: WritingProfileCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    context = _require(principal, "ai.manage")
    try:
        snapshot = create_execution_profile(session, context=context, **payload.model_dump())
        session.commit()
    except AuthorizationError as exc:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="WRITING_PROFILE_NAMESPACE_CONFLICT") from exc
    return _writing_profile_payload(session, snapshot, settings)


@router.post("/writing-execution-profiles/approve")
def approve_writing_execution_profile(
    payload: WritingProfileApproval,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    context = _require(principal, "ai.manage")
    try:
        ids = payload.model_dump(exclude={"expected_profile_version"})
        snapshot = snapshot_execution_profile(session, **ids)
        approved = approve_execution_profile(
            session,
            snapshot=snapshot,
            context=context,
            settings=settings,
            expected_profile_version=payload.expected_profile_version,
        )
        session.commit()
    except AuthorizationError as exc:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _writing_profile_payload(session, approved, settings)


class ReviewDecision(BaseModel):
    decision: HumanReviewStatus
    checksum: Optional[str] = None
    reason: Optional[str] = None


class ReviewRequest(BaseModel):
    entity_type: str = Field(min_length=1, max_length=120)
    entity_id: uuid.UUID
    checksum: Optional[str] = None


@router.get("/models")
def list_models(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db_session)
) -> list[dict[str, Any]]:
    _require(principal, "ai.read")
    return [
        {
            "id": str(item.id),
            "code": item.code,
            "provider": item.provider,
            "model_name": item.model_name,
            "status": item.status.value,
            **profile_descriptor(item),
        }
        for item in session.scalars(select(AIModelProfile).order_by(AIModelProfile.code))
    ]


@router.post("/models", status_code=status.HTTP_201_CREATED)
def create_model(
    payload: ModelRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    context = _require(principal, "ai.manage")
    try:
        item = AIOrchestrationService.create_model(session, context=context, **payload.model_dump())
        if item.purpose == "SPEECH_TRANSCRIPTION":
            # Persist the creator for an independent, exact-version approval.
            item.created_by = context.user_id
            languages = item.configuration.get("languages", [])
            snapshot_profile(item, languages[0] if languages else "de", {})
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": str(item.id), "code": item.code, "status": item.status.value}


@router.post("/models/{profile_id}/approve-transcription")
def approve_transcription_profile(
    profile_id: uuid.UUID,
    payload: TranscriptionProfileApproval,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    context = _require(principal, "ai.manage")
    try:
        profile = approve_profile(
            session,
            profile_id=profile_id,
            expected_version=payload.expected_profile_version,
            context=context,
            settings=settings,
        )
        session.commit()
    except AuthorizationError as exc:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "id": str(profile.id),
        "status": profile.status.value,
        **profile_descriptor(profile),
        "external_processing_authorized": False,
    }


@router.post("/workflow-versions/{version_id}/run", status_code=status.HTTP_202_ACCEPTED)
def run_workflow(
    version_id: uuid.UUID,
    payload: RunRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    context = _require(principal, "ai.run")
    workflow = session.get(AIWorkflowVersion, version_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow version not found")
    try:
        run = AIOrchestrationService.start_run(
            session,
            workflow,
            request_id=payload.request_id,
            initiated_by_user_id=principal.user.id,
            context=context,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": str(run.id), "status": run.status.value}


@router.get("/runs/{run_id}")
def get_run(
    run_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    _require(principal, "ai.read")
    run = session.get(AIRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="AI run not found")
    return {"id": str(run.id), "status": run.status.value, "request_id": run.request_id}


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    context = _require(principal, "ai.run")
    run = session.get(AIRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="AI run not found")
    try:
        AIOrchestrationService.cancel_run(session, run, context=context)
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": str(run.id), "status": run.status.value}


@router.post("/runs/{run_id}/review", status_code=status.HTTP_201_CREATED)
def request_review(
    run_id: uuid.UUID,
    payload: ReviewRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    context = _require(principal, "ai.review")
    run = session.get(AIRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="AI run not found")
    try:
        item = AIOrchestrationService.request_review(
            session,
            run,
            requested_by=principal.user.id,
            context=context,
            **payload.model_dump(),
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": str(item.id), "status": item.status.value}


@router.get("/reviews")
def list_reviews(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db_session)
) -> list[dict[str, str]]:
    _require(principal, "ai.review")
    return [
        {"id": str(item.id), "status": item.status.value, "review_type": item.review_type}
        for item in session.scalars(
            select(HumanReviewRequest).order_by(HumanReviewRequest.created_at.desc())
        )
    ]


@router.post("/reviews/{review_id}/decision")
def decide_review(
    review_id: uuid.UUID,
    payload: ReviewDecision,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    context = _require(principal, "ai.review")
    review = session.get(HumanReviewRequest, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    try:
        item = AIOrchestrationService.decide_review(
            session,
            review,
            decision=payload.decision,
            actor_id=principal.user.id,
            checksum=payload.checksum,
            reason=payload.reason,
            context=context,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": str(item.id), "status": item.status.value}


def _decision(
    review_id: uuid.UUID,
    decision: HumanReviewStatus,
    principal: Principal,
    session: Session,
) -> dict[str, str]:
    context = _require(principal, "ai.review")
    review = session.get(HumanReviewRequest, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    try:
        item = AIOrchestrationService.decide_review(
            session, review, decision=decision, actor_id=principal.user.id, context=context
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": str(item.id), "status": item.status.value}


@router.post("/reviews/{review_id}/approve")
def approve_review(
    review_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    return _decision(review_id, HumanReviewStatus.APPROVED, principal, session)


@router.post("/reviews/{review_id}/reject")
def reject_review(
    review_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    return _decision(review_id, HumanReviewStatus.REJECTED, principal, session)


@router.post("/reviews/{review_id}/request-changes")
def request_review_changes(
    review_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    return _decision(review_id, HumanReviewStatus.CHANGES_REQUESTED, principal, session)
