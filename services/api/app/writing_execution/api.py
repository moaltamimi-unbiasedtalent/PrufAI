"""Institution-scoped durable Writing requests, never caller-authored proposals."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, TenantContext
from app.config import Settings, get_settings
from app.db import get_db_session
from app.models import (
    AIWorkflowVersion,
    AuditEvent,
    CandidateExamAttempt,
    CandidateResponse,
    ExamVersionItemBinding,
    QuestionBankItemVersion,
    WritingExecutionJob,
)
from app.models.enums import CandidateAttemptStatus
from app.writing_execution.execution import WritingExecutionService

router = APIRouter(prefix="/api/v1/organisations", tags=["writing-execution"])


class WritingExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: uuid.UUID
    attempt_id: uuid.UUID
    exam_version_item_binding_id: uuid.UUID
    candidate_response_revision_id: uuid.UUID
    response_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    rubric_version_id: uuid.UUID
    rubric_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    execution_profile_id: uuid.UUID
    execution_profile_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_profile_id: uuid.UUID
    prompt_version_id: uuid.UUID
    agent_version_id: uuid.UUID
    workflow_version_id: uuid.UUID


def _context(
    session: Session, principal: Principal, organisation_id: uuid.UUID, permission: str
) -> TenantContext:
    try:
        if principal.tenant_context is None:
            raise AuthorizationError("institution context required")
        return WritingExecutionService.authorize(
            session, principal.tenant_context, organisation_id, permission
        )
    except AuthorizationError as exc:
        raise HTTPException(
            status_code=403, detail="institution Writing permission required"
        ) from exc


def _error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=403 if isinstance(exc, AuthorizationError) else 409,
        detail="institution Writing permission required"
        if isinstance(exc, AuthorizationError)
        else str(exc)
        if isinstance(exc, ValueError)
        else "WRITING_OPERATION_CONFLICT",
    )


def _job(
    session: Session, context: TenantContext, job_id: uuid.UUID, *, lock: bool = False
) -> WritingExecutionJob:
    query = select(WritingExecutionJob).where(
        WritingExecutionJob.id == job_id,
        WritingExecutionJob.organisation_id == context.organisation_id,
    )
    if lock:
        query = query.with_for_update()
    job = session.scalar(query.execution_options(populate_existing=True))
    if job is None:
        raise HTTPException(status_code=404, detail="Writing execution not found")
    try:
        WritingExecutionService.authorize(
            session,
            context,
            job.organisation_id,
            "subjective.read",
            candidate_user_id=job.candidate_user_id,
        )
    except AuthorizationError as exc:
        raise _error(exc) from exc
    return job


@router.get("/{organisation_id}/writing-executions/options")
def options(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    from app.writing_execution.registry import approved_profile_record, snapshot_execution_profile
    from app.writing_execution.runtime import validate_execution_configuration

    context = _context(session, principal, organisation_id, "subjective.assess")
    sources = []
    responses = session.scalars(
        select(CandidateResponse)
        .join(CandidateExamAttempt)
        .where(
            CandidateExamAttempt.organisation_id == organisation_id,
            CandidateExamAttempt.status == CandidateAttemptStatus.SUBMITTED,
            CandidateExamAttempt.candidate_user_id != context.user_id,
        )
        .order_by(CandidateResponse.id)
        .limit(100)
    )
    for response in responses:
        try:
            binding = session.get(ExamVersionItemBinding, response.exam_version_item_binding_id)
            item = (
                session.get(QuestionBankItemVersion, binding.question_bank_item_version_id)
                if binding
                else None
            )
            if item is None or not item.rubric_reference:
                continue
            _, _, _, source = WritingExecutionService.source(
                session,
                attempt_id=response.attempt_id,
                binding_id=response.exam_version_item_binding_id,
                rubric_version_id=uuid.UUID(item.rubric_reference),
            )
            sources.append(
                {
                    k: v
                    for k, v in source.items()
                    if k not in {"candidate_user_id", "organisation_id"}
                }
            )
        except (ValueError, TypeError, AttributeError):
            continue
    profiles = []
    seen = set()
    approvals = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.event_type == "writing_execution.profile_approved")
        .order_by(AuditEvent.timestamp.desc())
        .limit(100)
    )
    for approval in approvals:
        try:
            data = approval.metadata_json or {}
            ids = {
                key: uuid.UUID(str(data[key]))
                for key in (
                    "model_profile_id",
                    "prompt_version_id",
                    "agent_version_id",
                    "workflow_version_id",
                )
            }
            identity = tuple(str(value) for value in ids.values())
            if identity in seen:
                continue
            seen.add(identity)
            snapshot = snapshot_execution_profile(session, **ids)
            if approved_profile_record(session, snapshot) is None:
                continue
            availability = validate_execution_configuration(snapshot, settings)
            workflow = session.get(AIWorkflowVersion, ids["workflow_version_id"])
            if workflow is None:
                continue
            profiles.append(
                {
                    "execution_profile_id": snapshot["execution_profile_id"],
                    "execution_profile_version": snapshot["profile_version"],
                    "name": workflow.definition.name,
                    **{
                        key: snapshot[key]
                        for key in (
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
                    },
                    **availability,
                }
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return {"sources": sources, "profiles": profiles}


@router.post("/{organisation_id}/writing-executions", status_code=status.HTTP_201_CREATED)
def request_execution(
    organisation_id: uuid.UUID,
    payload: WritingExecutionRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    context = _context(session, principal, organisation_id, "subjective.assess")
    try:
        args = payload.model_dump()
        args["binding_id"] = args.pop("exam_version_item_binding_id")
        job = WritingExecutionService.request(session, context, settings=settings, **args)
        if job.organisation_id != organisation_id:
            raise AuthorizationError("organisation mismatch")
        session.commit()
        return WritingExecutionService.job_payload(session, job, context)
    except (ValueError, AuthorizationError, IntegrityError) as exc:
        session.rollback()
        raise _error(exc) from exc


@router.get("/{organisation_id}/writing-executions")
def list_jobs(
    organisation_id: uuid.UUID,
    attempt_id: Optional[uuid.UUID] = None,
    binding_id: Optional[uuid.UUID] = None,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(session, principal, organisation_id, "subjective.read")
    query = select(WritingExecutionJob).where(
        WritingExecutionJob.organisation_id == organisation_id,
        WritingExecutionJob.candidate_user_id != context.user_id,
    )
    if attempt_id:
        query = query.where(WritingExecutionJob.attempt_id == attempt_id)
    if binding_id:
        query = query.where(WritingExecutionJob.exam_version_item_binding_id == binding_id)
    jobs = session.scalars(
        query.order_by(WritingExecutionJob.created_at.desc(), WritingExecutionJob.id).limit(100)
    )
    return {"jobs": [WritingExecutionService.job_payload(session, job, context) for job in jobs]}


@router.get("/{organisation_id}/writing-executions/{job_id}")
def get_job(
    organisation_id: uuid.UUID,
    job_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(session, principal, organisation_id, "subjective.read")
    return WritingExecutionService.job_payload(session, _job(session, context, job_id), context)


@router.post("/{organisation_id}/writing-executions/{job_id}/retry")
def retry(
    organisation_id: uuid.UUID,
    job_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(session, principal, organisation_id, "subjective.assess")
    try:
        job = WritingExecutionService.retry(
            session, job=_job(session, context, job_id, lock=True), context=context
        )
        session.commit()
        return WritingExecutionService.job_payload(session, job, context)
    except (ValueError, AuthorizationError, IntegrityError) as exc:
        session.rollback()
        raise _error(exc) from exc


@router.post("/{organisation_id}/writing-executions/{job_id}/cancel")
def cancel(
    organisation_id: uuid.UUID,
    job_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(session, principal, organisation_id, "subjective.assess")
    try:
        job = WritingExecutionService.cancel(
            session, job=_job(session, context, job_id, lock=True), context=context
        )
        session.commit()
        return WritingExecutionService.job_payload(session, job, context)
    except (ValueError, AuthorizationError, IntegrityError) as exc:
        session.rollback()
        raise _error(exc) from exc
