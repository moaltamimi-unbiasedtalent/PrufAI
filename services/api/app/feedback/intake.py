"""Governed intake and independent, append-only feedback decisions."""

from __future__ import annotations

import uuid
from datetime import timezone
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError, TenantContext
from app.evaluation.contracts import Contract
from app.feedback import common
from app.feedback.sources import (
    FeedbackSource,
    authorize_evaluation_receipt,
    authorize_scope,
    operational_candidate_id,
    resolve_source,
)
from app.models import ResultAppeal, SubjectiveAssessment, SubjectiveAssessmentVersion
from app.models.feedback import FeedbackOperation, GovernedFeedback
from app.writing_assessment.service import canonical_checksum

Category = Literal[
    "RUBRIC_ALIGNMENT",
    "SCORING_CORRECTNESS",
    "EVIDENCE_PROVENANCE",
    "CITATION_GROUNDING",
    "RETRIEVAL_QUALITY",
    "TRANSCRIPTION_ERROR",
    "AUDIO_EVIDENCE",
    "EXAM_CONTENT",
    "MODEL_OUTPUT",
    "ACCESS_CONTROL",
    "RUNTIME_RELIABILITY",
    "ACCESSIBILITY",
    "OTHER_REVIEW_REQUIRED",
]
Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
Action = Literal["TRIAGE", "CONFIRM", "NOT_CONFIRM", "CLOSE"]
Reason = Literal[
    "SOURCE_REVIEWED",
    "REPRODUCED",
    "GOVERNED_EVIDENCE_CONFIRMED",
    "INSUFFICIENT_EVIDENCE",
    "NOT_REPRODUCIBLE",
    "RESOLVED_WITHOUT_CHANGE",
]
AccessMode = Literal["read", "intake", "decide"]
TRANSITIONS: dict[Action, tuple[set[str], str, str, set[str]]] = {
    "TRIAGE": ({"RECEIVED"}, "TRIAGED", "classified", {"SOURCE_REVIEWED"}),
    "CONFIRM": (
        {"TRIAGED"},
        "CONFIRMED",
        "confirmed",
        {"REPRODUCED", "GOVERNED_EVIDENCE_CONFIRMED"},
    ),
    "NOT_CONFIRM": (
        {"TRIAGED"},
        "NOT_CONFIRMED",
        "not_confirmed",
        {"INSUFFICIENT_EVIDENCE", "NOT_REPRODUCIBLE"},
    ),
    "CLOSE": (
        {"CONFIRMED", "NOT_CONFIRMED"},
        "CLOSED",
        "closed",
        {"RESOLVED_WITHOUT_CHANGE"},
    ),
}


class FeedbackIntakeInput(Contract):
    operation_id: uuid.UUID
    source: FeedbackSource
    category: Category
    severity: Severity


class FeedbackDecisionInput(Contract):
    operation_id: uuid.UUID
    expected_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    action: Action
    reason_code: Reason
    category: Category | None = None
    severity: Severity | None = None


def require_feedback_access(
    session: Session, context: TenantContext, row: GovernedFeedback, *, mode: AccessMode = "read"
) -> TenantContext:
    trusted = authorize_scope(
        session, context, row.organisation_id, row.source["read_permission"], mode=mode
    )
    if row.source_kind == "REVIEWER_FLAG":
        candidate_id = session.scalar(
            select(SubjectiveAssessment.candidate_user_id)
            .join(SubjectiveAssessmentVersion)
            .where(SubjectiveAssessmentVersion.id == uuid.UUID(row.source["source_id"]))
        )
        if candidate_id is None or candidate_id == trusted.user_id:
            raise AuthorizationError("FEEDBACK_REVIEWER_CANDIDATE_DENIED")
    elif row.source_kind == "GOVERNED_APPEAL_OUTCOME":
        appeal = session.get(ResultAppeal, uuid.UUID(row.source["appeal_id"]))
        if appeal is None or appeal.candidate_user_id == trusted.user_id:
            raise AuthorizationError("FEEDBACK_APPEAL_CANDIDATE_DENIED")
    elif row.source_kind == "OPERATIONAL_FAILURE":
        candidate_id = operational_candidate_id(session, row.source)
        if candidate_id is None or candidate_id == trusted.user_id:
            raise AuthorizationError("FEEDBACK_OPERATIONAL_CANDIDATE_DENIED")
    elif row.source_kind == "EVALUATION_FAILURE":
        authorize_evaluation_receipt(session, trusted, row.source)
    return trusted


def requires_reference(row: GovernedFeedback, category: str) -> bool:
    return bool(row.source["requires_reference"]) or (
        row.source["skill"] in {"WRITING", "SPEAKING", "TRANSCRIPTION"}
        and category in {"RUBRIC_ALIGNMENT", "SCORING_CORRECTNESS", "TRANSCRIPTION_ERROR"}
        and row.evidence_class != "EXPERT_LABELLED"
    )


def feedback_summary(
    session: Session,
    row: GovernedFeedback,
    context: TenantContext,
    *,
    receipt: FeedbackOperation | None = None,
) -> dict[str, Any]:
    trusted = require_feedback_access(session, context, row)
    current = common.latest(session, "FEEDBACK", row.id)
    visible = receipt or current
    if visible.entity_kind != "FEEDBACK" or visible.entity_id != row.id:
        raise common.FeedbackConflict("FEEDBACK_STATE_UNAVAILABLE")
    common.verify_receipt(visible)
    actions = []
    if visible.id == current.id:
        try:
            require_feedback_access(session, trusted, row, mode="decide")
        except AuthorizationError:
            pass
        else:
            actions = [
                action
                for action, (states, _, _, _) in TRANSITIONS.items()
                if current.state["status"] in states
                and (action not in {"CONFIRM", "NOT_CONFIRM"} or trusted.user_id != row.created_by)
            ]
    source = row.source
    return {
        "id": str(row.id),
        "organisation_id": str(row.organisation_id) if row.organisation_id else None,
        "source_kind": row.source_kind,
        "source_id": source["source_id"],
        "source_checksum": source["source_checksum"],
        "source_run": {"id": source["run_id"], "checksum": source["run_checksum"]}
        if row.source_kind == "EVALUATION_FAILURE"
        else None,
        "failed_check": source.get("failed_check"),
        "assessment_family": source["assessment_family"],
        "skill": source["skill"],
        "method": source["method"],
        "language": source.get("language"),
        "cefr_level": source.get("cefr_level"),
        "artifact_pins": source["artifact_pins"],
        "execution_class": source["execution_class"],
        "evidence_class": row.evidence_class,
        "privacy_class": row.privacy_class,
        "requires_reference": requires_reference(row, visible.state["category"]),
        "created_at": row.created_at.astimezone(timezone.utc).isoformat(),
        "status": visible.state["status"],
        "category": visible.state["category"],
        "severity": visible.state["severity"],
        "reason_code": visible.state["reason_code"],
        "checksum": visible.checksum,
        "allowed_actions": actions,
    }


def _get(
    session: Session, context: TenantContext, identifier: uuid.UUID, *, lock: bool = False
) -> GovernedFeedback:
    statement = select(GovernedFeedback).where(
        GovernedFeedback.id == identifier,
        GovernedFeedback.organisation_id == context.organisation_id,
    )
    if lock:
        statement = statement.with_for_update()
    row = session.scalar(statement)
    if row is None:
        raise AuthorizationError("FEEDBACK_SOURCE_SCOPE_REQUIRED")
    return row


def intake_feedback(
    session: Session, context: TenantContext, payload: FeedbackIntakeInput
) -> dict[str, Any]:
    request = payload.model_dump(mode="json")
    previous = common.operation(session, context, payload.operation_id, "received", request)
    if previous is not None:
        row = _get(session, context, previous.entity_id)
        require_feedback_access(session, context, row, mode="intake")
        return feedback_summary(session, row, context, receipt=previous)
    resolved = resolve_source(session, context, payload.source)
    body = resolved.body
    fingerprint = canonical_checksum(
        {
            "organisation_id": str(resolved.organisation_id) if resolved.organisation_id else None,
            "source_kind": payload.source.kind,
            "source_id": body["source_id"],
            "source_checksum": body["source_checksum"],
            "failed_check": body.get("failed_check"),
            "authored_category": payload.category
            if payload.source.kind == "REVIEWER_FLAG"
            else None,
        }
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "feedback-source:" + fingerprint},
        )
    if session.scalar(
        select(GovernedFeedback.id).where(GovernedFeedback.source_fingerprint == fingerprint)
    ):
        raise common.FeedbackConflict("FEEDBACK_SOURCE_ALREADY_RECORDED")
    scope_key = canonical_checksum(
        {
            "organisation_id": str(resolved.organisation_id) if resolved.organisation_id else None,
            **{
                key: body.get(key)
                for key in (
                    "assessment_family",
                    "skill",
                    "method",
                    "evaluation_method",
                    "evaluation_method_version",
                    "language",
                    "cefr_level",
                    "artifact_pins",
                    "split",
                    "execution_class",
                )
            },
        }
    )
    row = GovernedFeedback(
        organisation_id=resolved.organisation_id,
        source_kind=payload.source.kind,
        source_fingerprint=fingerprint,
        source=body,
        scope_key=scope_key,
        evidence_class=resolved.evidence_class,
        privacy_class=resolved.privacy_class,
        created_by=context.user_id,
        created_at=common.utcnow(),
    )
    session.add(row)
    session.flush()
    common.record(
        session,
        context,
        payload.operation_id,
        "received",
        request,
        entity_kind="FEEDBACK",
        entity_id=row.id,
        state={
            "status": "RECEIVED",
            "category": payload.category,
            "severity": payload.severity,
            "reason_code": None,
        },
    )
    return feedback_summary(session, row, context)


def decide_feedback(
    session: Session, context: TenantContext, identifier: uuid.UUID, payload: FeedbackDecisionInput
) -> dict[str, Any]:
    states, target, action, reasons = TRANSITIONS[payload.action]
    request = {"feedback_id": str(identifier), **payload.model_dump(mode="json")}
    replay = common.operation(session, context, payload.operation_id, action, request)
    row = _get(session, context, identifier, lock=replay is None)
    trusted = require_feedback_access(session, context, row, mode="decide")
    if replay is not None:
        return feedback_summary(session, row, trusted, receipt=replay)
    previous = common.latest(session, "FEEDBACK", row.id)
    if previous.checksum != payload.expected_checksum or previous.state["status"] not in states:
        raise common.FeedbackConflict("FEEDBACK_STATE_CONFLICT")
    if payload.reason_code not in reasons or (
        payload.action != "TRIAGE"
        and (payload.category is not None or payload.severity is not None)
    ):
        raise ValueError("FEEDBACK_ACTION_CLASSIFICATION_INVALID")
    if payload.action in {"CONFIRM", "NOT_CONFIRM"} and row.created_by == trusted.user_id:
        raise AuthorizationError("FEEDBACK_INDEPENDENT_CONFIRMATION_REQUIRED")
    common.record(
        session,
        trusted,
        payload.operation_id,
        action,
        request,
        entity_kind="FEEDBACK",
        entity_id=row.id,
        previous=previous,
        state={
            "status": target,
            "category": payload.category or previous.state["category"],
            "severity": payload.severity or previous.state["severity"],
            "reason_code": payload.reason_code,
        },
    )
    return feedback_summary(session, row, trusted)
