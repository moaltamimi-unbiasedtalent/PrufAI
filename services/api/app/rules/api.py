from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.db import get_db_session
from app.models import RuleDefinition, RuleVersion, StandardPackVersion
from app.models.enums import RuleCategory, RuleEvidenceType, RuleSeverity, RuleStatus
from app.rules.service import RulesService

router = APIRouter(prefix="/api/v1/platform", tags=["rules"])


class RuleCreateRequest(BaseModel):
    code: str = Field(min_length=3, max_length=160)
    name: str = Field(min_length=1, max_length=240)
    category: RuleCategory
    description: Optional[str] = None


class RuleVersionRequest(BaseModel):
    version_label: str = Field(min_length=1, max_length=120)
    expression: dict[str, Any]
    severity: RuleSeverity
    failure_code: str = Field(min_length=1, max_length=120)
    failure_message_template: str = Field(min_length=1, max_length=500)
    effective_from: Optional[datetime] = None
    effective_until: Optional[datetime] = None
    internal_policy: bool = False
    test_cases: list[dict[str, Any]] = Field(default_factory=list)


class EvidenceRequest(BaseModel):
    document_version_id: uuid.UUID
    evidence_type: RuleEvidenceType
    page_id: Optional[uuid.UUID] = None
    chunk_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class DryRunRequest(BaseModel):
    facts: dict[str, Any]


class BindingRequest(BaseModel):
    rule_version_id: uuid.UUID
    required: bool = True
    scope: Optional[str] = None
    sequence: int = Field(default=0, ge=0)


def _require(principal: Principal, permission: str) -> TenantContext:
    context = principal.tenant_context
    if context is None or not context.platform_scope:
        raise HTTPException(status_code=403, detail="platform authority required")
    try:
        AuthorizationService.require_permission(context, permission)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return context


def _definition(item: RuleDefinition) -> dict[str, object]:
    return {
        "id": str(item.id),
        "code": item.code,
        "name": item.name,
        "category": item.category.value,
        "status": item.status.value,
    }


def _version(item: RuleVersion) -> dict[str, object]:
    return {
        "id": str(item.id),
        "rule_definition_id": str(item.rule_definition_id),
        "version_label": item.version_label,
        "status": item.status.value,
        "severity": item.severity.value,
        "failure_code": item.failure_code,
        "checksum": item.checksum,
        "effective_from": item.effective_from,
        "effective_until": item.effective_until,
    }


@router.get("/rules")
def list_rules(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    category: Optional[RuleCategory] = None,
    status_filter: Optional[RuleStatus] = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict[str, object]]:
    _require(principal, "rules.read")
    query = select(RuleDefinition).order_by(RuleDefinition.code).offset(offset).limit(limit)
    if category is not None:
        query = query.where(RuleDefinition.category == category)
    if status_filter is not None:
        query = query.where(RuleDefinition.status == status_filter)
    return [_definition(item) for item in session.scalars(query)]


@router.post("/rules", status_code=status.HTTP_201_CREATED)
def create_rule(
    payload: RuleCreateRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    context = _require(principal, "rules.manage")
    try:
        item = RulesService.create_definition(session, context=context, **payload.model_dump())
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _definition(item)


@router.post("/rules/{rule_id}/versions", status_code=status.HTTP_201_CREATED)
def create_rule_version(
    rule_id: uuid.UUID,
    payload: RuleVersionRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    context = _require(principal, "rules.manage")
    definition = session.get(RuleDefinition, rule_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="rule definition not found")
    try:
        item = RulesService.create_version(
            session,
            definition,
            created_by_user_id=principal.user.id,
            context=context,
            **payload.model_dump(),
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _version(item)


@router.get("/rule-versions/{version_id}")
def get_rule_version(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require(principal, "rules.read")
    item = session.get(RuleVersion, version_id)
    if item is None:
        raise HTTPException(status_code=404, detail="rule version not found")
    return _version(item)


@router.post("/rule-versions/{version_id}/evidence", status_code=status.HTTP_201_CREATED)
def bind_evidence(
    version_id: uuid.UUID,
    payload: EvidenceRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    context = _require(principal, "rules.manage")
    version = session.get(RuleVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="rule version not found")
    try:
        item = RulesService.bind_evidence(session, version, context=context, **payload.model_dump())
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": str(item.id),
        "document_version_id": str(item.knowledge_document_version_id),
        "checksum": item.document_checksum,
    }


@router.post(
    "/standard-pack-versions/{pack_version_id}/rule-bindings", status_code=status.HTTP_201_CREATED
)
def bind_rule(
    pack_version_id: uuid.UUID,
    payload: BindingRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    context = _require(principal, "rules.manage")
    pack = session.get(StandardPackVersion, pack_version_id)
    rule = session.get(RuleVersion, payload.rule_version_id)
    if pack is None or rule is None:
        raise HTTPException(status_code=404, detail="pack or rule version not found")
    try:
        item = RulesService.bind_to_pack(
            session, pack, rule, context=context, **payload.model_dump(exclude={"rule_version_id"})
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": str(item.id),
        "rule_version_id": str(item.rule_version_id),
        "required": item.required,
    }


@router.post("/rule-versions/{version_id}/validate")
def validate_rule(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    context = _require(principal, "rules.manage")
    version = session.get(RuleVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="rule version not found")
    result = RulesService.validate(session, version)
    RulesService._audit(
        session,
        context,
        "rule.validation.completed",
        version,
        {"checksum": result.checksum, "status": result.status},
    )
    session.commit()
    return {
        "status": result.status,
        "checksum": result.checksum,
        "tests_passed": result.tests_passed,
        "issues": [issue.__dict__ for issue in result.issues],
    }


@router.post("/rule-versions/{version_id}/dry-run")
def dry_run(
    version_id: uuid.UUID,
    payload: DryRunRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    context = _require(principal, "rules.evaluate")
    version = session.get(RuleVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="rule version not found")
    report = RulesService.dry_run(version, payload.facts)
    RulesService._audit(
        session, context, "rule.dry_run.completed", version, {"evaluation_id": report.evaluation_id}
    )
    session.commit()
    return {
        "evaluation_id": report.evaluation_id,
        "engine": report.engine_name,
        "engine_version": report.engine_version,
        "overall_status": report.overall_status.value,
        "summary": report.summary,
        "results": [result.__dict__ for result in report.results],
    }


def _transition(
    version_id: uuid.UUID, target: RuleStatus, principal: Principal, session: Session
) -> dict[str, object]:
    context = _require(
        principal,
        "rules.publish"
        if target in {RuleStatus.PUBLISHED, RuleStatus.ACTIVE, RuleStatus.RETIRED}
        else "rules.review",
    )
    version = session.get(RuleVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="rule version not found")
    try:
        result = RulesService.transition(
            session, version, target, actor_id=principal.user.id, context=context
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "version": _version(version),
        "validation": {"status": result.status, "tests_passed": result.tests_passed},
    }


@router.post("/rule-versions/{version_id}/request-review")
def request_review(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return _transition(version_id, RuleStatus.HUMAN_REVIEW, principal, session)


@router.post("/rule-versions/{version_id}/approve")
def approve(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return _transition(version_id, RuleStatus.APPROVED, principal, session)


@router.post("/rule-versions/{version_id}/publish")
def publish(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return _transition(version_id, RuleStatus.PUBLISHED, principal, session)


@router.get("/rule-versions/{version_id}/export")
def export_rule(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require(principal, "rules.read")
    version = session.get(RuleVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="rule version not found")
    return RulesService.export(version)


@router.get("/rule-versions/{version_id}/diff/{other_id}")
def diff_rule(
    version_id: uuid.UUID,
    other_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, bool]:
    _require(principal, "rules.read")
    first, second = session.get(RuleVersion, version_id), session.get(RuleVersion, other_id)
    if first is None or second is None:
        raise HTTPException(status_code=404, detail="rule version not found")
    return RulesService.diff(first, second)
