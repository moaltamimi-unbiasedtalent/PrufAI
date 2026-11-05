"""Authorize and resolve the isolated Writing prompt selection history."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timezone
from typing import Any, Literal

from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.config import Settings
from app.evaluation import service as evaluation
from app.evaluation.contracts import Contract
from app.feedback import common
from app.feedback.common import FeedbackConflict, Pin
from app.improvement import candidates, readiness
from app.improvement.boundary import (
    NONPRODUCTION,
    require_validation_environment,
    validation_available,
)
from app.models import EvaluationReview, EvaluationRun, PromptDefinition, PromptVersion
from app.models.feedback import FeedbackOperation, GovernedChangeCandidate

SELECTION = "PROMPT_SELECTION"
DENIAL = "PROMOTION_DENIAL"
PROMOTE = "prompt_promotion_requested"
REVERT = "prompt_reversion_requested"
PRODUCTION_BLOCKER = "EMPIRICAL_GATE_UNCONFIGURED"
PROMOTE_PERMISSIONS = (evaluation.READ, evaluation.MANAGE, evaluation.REVIEW)
VALIDATE_PERMISSIONS = (evaluation.READ, evaluation.MANAGE, evaluation.RUN)
HISTORY_LIMIT = 50


class _EnvironmentCommand(Contract):
    operation_id: uuid.UUID
    environment: Literal["NONPRODUCTION_VALIDATION", "PRODUCTION"]
    rationale: str = Field(min_length=8, max_length=500)

    @field_validator("rationale")
    @classmethod
    def nonblank(cls, value: str) -> str:
        trimmed = value.strip()
        if len(trimmed) < 8:
            raise ValueError("PROMOTION_RATIONALE_REQUIRED")
        return trimmed


class PromotionInput(_EnvironmentCommand):
    readiness: Pin
    expected_selection: Pin | None


class RevertInput(_EnvironmentCommand):
    expected_selection: Pin
    clear_selection: bool = Field(default=False, strict=True)


@dataclass(frozen=True)
class _Eligible:
    candidate: GovernedChangeCandidate
    prompt: PromptVersion
    readiness: FeedbackOperation
    comparison: EvaluationRun
    evidence: dict[str, Any]
    execution_actors: set[uuid.UUID]


def _pin(row: Any) -> dict[str, str]:
    return {"id": str(row.id), "checksum": row.checksum}


def _candidate(
    session: Session, identifier: uuid.UUID
) -> tuple[GovernedChangeCandidate, PromptVersion]:
    row = session.get(GovernedChangeCandidate, identifier)
    if row is None:
        raise FeedbackConflict("PROMOTION_CANDIDATE_UNAVAILABLE")
    candidates.verify_change_candidate(session, row)
    prompt = session.get(PromptVersion, row.draft_prompt_version_id)
    if prompt is None:
        raise FeedbackConflict("PROMOTION_CANDIDATE_UNAVAILABLE")
    return row, prompt


def _lock_definition(session: Session, identifier: uuid.UUID, *, read: bool = False) -> None:
    statement = select(PromptDefinition.id).where(PromptDefinition.id == identifier)
    # NO KEY UPDATE serializes selection writers while permitting the FK key
    # share used by draft creation. Validation takes a conflicting share lock.
    if session.scalar(statement.with_for_update(read=read, key_share=not read)) is None:
        raise FeedbackConflict("PROMOTION_DEFINITION_UNAVAILABLE")


def _optional_latest(
    session: Session, kind: str, identifier: uuid.UUID
) -> FeedbackOperation | None:
    row = session.scalar(
        select(FeedbackOperation)
        .where(FeedbackOperation.entity_kind == kind, FeedbackOperation.entity_id == identifier)
        .order_by(FeedbackOperation.revision.desc())
        .limit(1)
    )
    return common.verify_receipt(row) if row is not None else None


def _verify_selection(row: FeedbackOperation, definition_id: uuid.UUID) -> None:
    common.verify_receipt(row)
    state = row.state
    mode = state.get("reversion_mode")
    if (
        row.entity_kind != SELECTION
        or row.entity_id != definition_id
        or row.organisation_id is not None
        or state.get("definition_id") != str(definition_id)
        or state.get("environment") != NONPRODUCTION
        or state.get("production_activation") is not False
        or row.action not in {PROMOTE, REVERT}
        or state.get("status") != ("SELECTED" if row.action == PROMOTE else "REVERTED")
        or (row.action == PROMOTE and mode is not None)
        or (row.action == REVERT and mode not in {"RESTORE_PREVIOUS", "CLEAR_SELECTION"})
        or (mode == "CLEAR_SELECTION" and state.get("selected") is not None)
        or (
            row.action == PROMOTE
            and (state.get("selected") is None or state.get("authorization") is not None)
        )
        or (
            row.action == REVERT
            and (state.get("selected") is None) != (state.get("authorization") is None)
        )
    ):
        raise FeedbackConflict("PROMOTION_SELECTION_INTEGRITY_INVALID")


def _previous(session: Session, row: FeedbackOperation) -> FeedbackOperation | None:
    _verify_selection(row, row.entity_id)
    if row.revision == 1:
        if (
            row.previous_checksum is not None
            or row.state.get("prior_selection") is not None
            or row.state.get("previous") is not None
        ):
            raise FeedbackConflict("PROMOTION_SELECTION_PREDECESSOR_INVALID")
        return None
    prior = session.scalar(
        select(FeedbackOperation).where(
            FeedbackOperation.entity_kind == SELECTION,
            FeedbackOperation.entity_id == row.entity_id,
            FeedbackOperation.revision == row.revision - 1,
        )
    )
    if (
        prior is None
        or row.previous_checksum != prior.checksum
        or row.state.get("prior_selection") != _pin(prior)
        or row.state.get("previous") != prior.state.get("selected")
    ):
        raise FeedbackConflict("PROMOTION_SELECTION_PREDECESSOR_INVALID")
    _verify_selection(prior, row.entity_id)
    return prior


def _head(session: Session, definition_id: uuid.UUID) -> FeedbackOperation | None:
    row = _optional_latest(session, SELECTION, definition_id)
    if row is not None:
        _previous(session, row)
    return row


def _matches_head(row: FeedbackOperation | None, pin: Pin | None) -> bool:
    if row is None or pin is None:
        return row is None and pin is None
    return row.id == pin.id and row.checksum == pin.checksum


def _eligible(
    session: Session,
    context: TenantContext,
    row: GovernedChangeCandidate,
    prompt: PromptVersion,
    pin: Pin,
    *,
    current: bool = True,
    lock: bool = True,
) -> _Eligible:
    if current:
        candidates.require_current_candidate_authority(session, context, row, lock=lock)
    history = readiness._verified_history(session, row)
    decision = history[-1]
    if (
        not _matches_head(decision, pin)
        or decision.action != readiness.REVIEW_ACTION
        or decision.state.get("status") != "APPROVED_FOR_NONPRODUCTION_VALIDATION"
        or decision.state.get("decision") != "APPROVE_FOR_NONPRODUCTION_VALIDATION"
        or decision.state.get("production_readiness") != "NOT_READY"
    ):
        raise FeedbackConflict("PROMOTION_READINESS_PIN_MISMATCH")
    comparison, report = readiness._linked_comparison(session, row, decision)
    if (
        comparison.status != "COMPLETED"
        or comparison.completed_at is None
        or comparison.hard_gate != "PASS"
        or comparison.execution_class != "TEST_ONLY"
        or report["comparison_status"] != "COMPARABLE"
        or report["technical_status"] != "PASS"
        or report["empirical_status"] != "UNCONFIGURED"
        or report["decision"] != "UNCONFIGURED"
        or report["complete"] is not True
        or report["expected_case_count"] <= 0
        or report["paired_case_count"] != report["expected_case_count"]
        or report["missing_baseline_case_ids"]
        or report["missing_candidate_case_ids"]
    ):
        raise FeedbackConflict("PROMOTION_REQUIRED_TECHNICAL_EVIDENCE_UNAVAILABLE")
    review = session.get(EvaluationReview, uuid.UUID(decision.state["evaluation_review_id"]))
    if (
        review is None
        or review.operation_id != decision.id
        or review.run_id != comparison.id
        or review.run_checksum != comparison.checksum
        or review.reviewer_user_id != decision.actor_id
        or review.decision != "ACCEPTED"
        or review.review_reference != f"improvement:{row.id}:{decision.id}"
    ):
        raise FeedbackConflict("PROMOTION_READINESS_REVIEW_MISMATCH")
    baseline = session.get(EvaluationRun, uuid.UUID(report["baseline_run"]["id"]))
    executed = session.get(EvaluationRun, uuid.UUID(report["candidate_run"]["id"]))
    if (
        baseline is None
        or _pin(baseline) != report["baseline_run"]
        or executed is None
        or _pin(executed) != report["candidate_run"]
        or executed.configuration.get("prompt_version_id") != str(prompt.id)
        or executed.configuration.get("prompt_checksum") != prompt.checksum
    ):
        raise FeedbackConflict("PROMOTION_EVALUATED_PROMPT_MISMATCH")
    evidence = {
        "candidate": _pin(row),
        "prompt": _pin(prompt),
        "baseline": {key: row.body["target"][key] for key in ("id", "checksum")},
        "readiness": _pin(decision),
        **{
            key: row.body[key]
            for key in ("proposal", "approval", "pattern", "source_feedback", "suite_version")
        },
        "baseline_run": report["baseline_run"],
        "candidate_run": report["candidate_run"],
        "comparison_run": _pin(comparison),
        "technical_status": report["technical_status"],
        "empirical_status": report["empirical_status"],
    }
    return _Eligible(
        row,
        prompt,
        decision,
        comparison,
        evidence,
        {baseline.created_by, executed.created_by, comparison.created_by},
    )


def _independent(session: Session, context: TenantContext, eligible: _Eligible) -> bool:
    return (
        context.user_id != eligible.readiness.actor_id
        and context.user_id not in eligible.execution_actors
        and readiness._can_review(session, context, eligible.candidate, eligible.comparison)
    )


def _require_independent(session: Session, context: TenantContext, eligible: _Eligible) -> None:
    if not _independent(session, context, eligible):
        raise AuthorizationError("PROMOTION_INDEPENDENT_AUTHORITY_REQUIRED")


def _authorization(
    session: Session, receipt: FeedbackOperation, selected: dict[str, Any]
) -> FeedbackOperation:
    if receipt.action == PROMOTE:
        return receipt
    pin = Pin.model_validate(receipt.state["authorization"])
    origin = session.get(FeedbackOperation, pin.id)
    if (
        origin is None
        or origin.checksum != pin.checksum
        or origin.action != PROMOTE
        or origin.revision >= receipt.revision
        or origin.state.get("selected") != selected
    ):
        raise FeedbackConflict("PROMOTION_AUTHORIZATION_UNAVAILABLE")
    _verify_selection(origin, receipt.entity_id)
    return origin


def _active(
    session: Session,
    context: TenantContext,
    receipt: FeedbackOperation,
    *,
    lock: bool,
) -> tuple[_Eligible, FeedbackOperation]:
    _verify_selection(receipt, receipt.entity_id)
    selected = receipt.state.get("selected")
    if not isinstance(selected, dict):
        raise FeedbackConflict("PROMOTION_NO_ACTIVE_SELECTION")
    selected_pin = Pin.model_validate(selected["candidate"])
    row, prompt = _candidate(session, selected_pin.id)
    if row.checksum != selected_pin.checksum or prompt.prompt_definition_id != receipt.entity_id:
        raise FeedbackConflict("PROMOTION_SELECTION_INTEGRITY_INVALID")
    eligible = _eligible(
        session, context, row, prompt, Pin.model_validate(selected["readiness"]), lock=lock
    )
    if eligible.evidence != selected:
        raise FeedbackConflict("PROMOTION_SELECTION_INTEGRITY_INVALID")
    origin = _authorization(session, receipt, selected)
    for actor_id in {origin.actor_id, receipt.actor_id}:
        try:
            actor = build_tenant_context(session, actor_id)
        except AuthorizationError as exc:
            raise AuthorizationError("PROMOTION_AUTHORIZATION_REVOKED") from exc
        if not set(PROMOTE_PERMISSIONS) <= actor.platform_permissions:
            raise AuthorizationError("PROMOTION_AUTHORIZATION_REVOKED")
        _require_independent(session, actor, eligible)
    return eligible, origin


def _audit(
    session: Session,
    context: TenantContext,
    definition_id: uuid.UUID,
    operation_id: uuid.UUID,
    action: str,
    **metadata: Any,
) -> None:
    AuditService.record(
        session,
        context,
        event_type=f"feedback.{action}",
        entity_type="prompt_selection",
        entity_id=str(definition_id),
        request_id=str(operation_id),
        metadata={"production_activation": False, **metadata},
        require_audit_permission=False,
    )


def _denied(
    session: Session,
    context: TenantContext,
    definition_id: uuid.UUID,
    payload: _EnvironmentCommand,
    request: dict[str, Any],
    command: str,
    reason: str,
) -> FeedbackOperation:
    return common.record(
        session,
        context,
        payload.operation_id,
        command,
        request,
        entity_kind=DENIAL,
        entity_id=definition_id,
        previous=_optional_latest(session, DENIAL, definition_id),
        state={
            "status": "DENIED",
            "definition_id": str(definition_id),
            "environment": payload.environment,
            "reason_code": reason,
            "selected": None,
            "previous": None,
            "reversion_mode": _reversion_mode(payload),
            "production_activation": False,
        },
        audit_action="prompt_production_denied"
        if payload.environment == "PRODUCTION"
        else "prompt_selection_rejected",
    )


def _reversion_mode(payload: _EnvironmentCommand) -> str | None:
    if not isinstance(payload, RevertInput):
        return None
    return "CLEAR_SELECTION" if payload.clear_selection else "RESTORE_PREVIOUS"


def _write_selection(
    session: Session,
    context: TenantContext,
    definition_id: uuid.UUID,
    payload: _EnvironmentCommand,
    request: dict[str, Any],
    head: FeedbackOperation | None,
    selected: dict[str, Any] | None,
    *,
    command: str,
    authorization: FeedbackOperation | None = None,
) -> FeedbackOperation:
    promoted = command == PROMOTE
    mode = _reversion_mode(payload)
    audit_action = {
        None: "prompt_nonproduction_selected",
        "RESTORE_PREVIOUS": "prompt_selection_reverted",
        "CLEAR_SELECTION": "prompt_selection_cleared",
    }[mode]
    return common.record(
        session,
        context,
        payload.operation_id,
        command,
        request,
        entity_kind=SELECTION,
        entity_id=definition_id,
        previous=head,
        state={
            "status": "SELECTED" if promoted else "REVERTED",
            "definition_id": str(definition_id),
            "environment": NONPRODUCTION,
            "selected": selected,
            "previous": head.state["selected"] if head else None,
            "prior_selection": _pin(head) if head else None,
            "authorization": _pin(authorization) if authorization else None,
            "reversion_mode": mode,
            "rationale": payload.rationale,
            "production_activation": False,
        },
        audit_action=audit_action,
    )


def _request(candidate_id: uuid.UUID, payload: _EnvironmentCommand) -> dict[str, Any]:
    return {"candidate_id": str(candidate_id), **payload.model_dump(mode="json")}


def promote(
    session: Session,
    context: TenantContext,
    settings: Settings,
    candidate_id: uuid.UUID,
    payload: PromotionInput,
) -> FeedbackOperation:
    trusted = common.require_platform(session, context, *PROMOTE_PERMISSIONS)
    request = _request(candidate_id, payload)
    repeated = common.operation(session, trusted, payload.operation_id, PROMOTE, request)
    if repeated is not None:
        return repeated
    row, prompt = _candidate(session, candidate_id)
    definition_id = prompt.prompt_definition_id
    _lock_definition(session, definition_id)
    _audit(
        session,
        trusted,
        definition_id,
        payload.operation_id,
        PROMOTE,
        environment=payload.environment,
    )
    if payload.environment == "PRODUCTION":
        _audit(
            session,
            trusted,
            definition_id,
            payload.operation_id,
            "prompt_promotion_eligibility_evaluated",
            environment=payload.environment,
            status="DENIED",
            reason_code=PRODUCTION_BLOCKER,
        )
        return _denied(
            session, trusted, definition_id, payload, request, PROMOTE, PRODUCTION_BLOCKER
        )
    head = _head(session, definition_id)
    selected = head.state.get("selected") if head else None
    reason = None
    eligible = None
    if not validation_available(session, settings):
        reason = "PROMOTION_VALIDATION_ENVIRONMENT_REQUIRED"
    elif not _matches_head(head, payload.expected_selection):
        reason = "PROMOTION_SELECTION_STALE"
    elif isinstance(selected, dict) and selected.get("candidate") == _pin(row):
        reason = "PROMOTION_ALREADY_SELECTED"
    else:
        try:
            eligible = _eligible(session, trusted, row, prompt, payload.readiness)
        except (AuthorizationError, ValueError):
            reason = "PROMOTION_NOT_ELIGIBLE"
        else:
            if not _independent(session, trusted, eligible):
                reason = "PROMOTION_INDEPENDENT_AUTHORITY_REQUIRED"
    _audit(
        session,
        trusted,
        definition_id,
        payload.operation_id,
        "prompt_promotion_eligibility_evaluated",
        environment=payload.environment,
        status="DENIED" if reason else "ELIGIBLE",
        reason_code=reason,
    )
    if reason is not None:
        return _denied(session, trusted, definition_id, payload, request, PROMOTE, reason)
    assert eligible is not None
    _audit(
        session,
        trusted,
        definition_id,
        payload.operation_id,
        "prompt_promotion_authorized",
        environment=payload.environment,
        candidate_id=str(row.id),
        readiness_checksum=eligible.readiness.checksum,
    )
    return _write_selection(
        session, trusted, definition_id, payload, request, head, eligible.evidence, command=PROMOTE
    )


def _require_withdrawal(
    session: Session,
    context: TenantContext,
    head: FeedbackOperation,
    row: GovernedChangeCandidate,
    prompt: PromptVersion,
    *,
    lock: bool,
) -> None:
    selected = head.state.get("selected")
    if not isinstance(selected, dict) or selected.get("candidate") != _pin(row):
        raise FeedbackConflict("PROMOTION_SELECTION_CANDIDATE_MISMATCH")
    # Withdrawal must remain possible when current promotion authority has been
    # revoked. Historical proof and a new independent actor are still required.
    current = _eligible(
        session,
        context,
        row,
        prompt,
        Pin.model_validate(selected["readiness"]),
        current=False,
        lock=lock,
    )
    if current.evidence != selected:
        raise FeedbackConflict("PROMOTION_SELECTION_INTEGRITY_INVALID")
    _require_independent(session, context, current)


def _restore_previous(
    session: Session, context: TenantContext, head: FeedbackOperation, *, lock: bool
) -> tuple[dict[str, Any] | None, FeedbackOperation | None]:
    prior = _previous(session, head)
    if prior is None or prior.state.get("selected") is None:
        return None, None
    restored, origin = _active(session, context, prior, lock=lock)
    _require_independent(session, context, restored)
    return restored.evidence, origin


def revert(
    session: Session,
    context: TenantContext,
    settings: Settings,
    candidate_id: uuid.UUID,
    payload: RevertInput,
) -> FeedbackOperation:
    trusted = common.require_platform(session, context, *PROMOTE_PERMISSIONS)
    request = _request(candidate_id, payload)
    repeated = common.operation(session, trusted, payload.operation_id, REVERT, request)
    if repeated is not None:
        return repeated
    row, prompt = _candidate(session, candidate_id)
    definition_id = prompt.prompt_definition_id
    _lock_definition(session, definition_id)
    if payload.environment == "PRODUCTION":
        return _denied(
            session, trusted, definition_id, payload, request, REVERT, PRODUCTION_BLOCKER
        )
    if not validation_available(session, settings):
        return _denied(
            session,
            trusted,
            definition_id,
            payload,
            request,
            REVERT,
            "PROMOTION_VALIDATION_ENVIRONMENT_REQUIRED",
        )
    head = _head(session, definition_id)
    if head is None or not _matches_head(head, payload.expected_selection):
        return _denied(
            session, trusted, definition_id, payload, request, REVERT, "PROMOTION_SELECTION_STALE"
        )
    try:
        _require_withdrawal(session, trusted, head, row, prompt, lock=True)
        selected, origin = (
            (None, None)
            if payload.clear_selection
            else _restore_previous(session, trusted, head, lock=True)
        )
    except AuthorizationError:
        return _denied(
            session,
            trusted,
            definition_id,
            payload,
            request,
            REVERT,
            "PROMOTION_INDEPENDENT_AUTHORITY_REQUIRED",
        )
    except ValueError:
        return _denied(
            session,
            trusted,
            definition_id,
            payload,
            request,
            REVERT,
            "PROMOTION_REVERSION_NOT_ELIGIBLE",
        )
    return _write_selection(
        session,
        trusted,
        definition_id,
        payload,
        request,
        head,
        selected,
        command=REVERT,
        authorization=origin,
    )


def resolve_nonproduction_selection(
    session: Session,
    context: TenantContext,
    settings: Settings,
    candidate_id: uuid.UUID,
    selection: Pin,
) -> tuple[FeedbackOperation, PromptVersion]:
    trusted = common.require_platform(session, context, *VALIDATE_PERMISSIONS)
    require_validation_environment(session, settings)
    row, prompt = _candidate(session, candidate_id)
    _lock_definition(session, prompt.prompt_definition_id, read=True)
    head = _head(session, prompt.prompt_definition_id)
    if head is None or not _matches_head(head, selection):
        raise FeedbackConflict("PROMOTION_SELECTION_STALE")
    selected = head.state.get("selected")
    if not isinstance(selected, dict) or selected.get("candidate") != _pin(row):
        raise FeedbackConflict("PROMOTION_SELECTION_CANDIDATE_MISMATCH")
    eligible, _ = _active(session, trusted, head, lock=True)
    return head, eligible.prompt


def _safe_selection(value: dict[str, Any] | None) -> dict[str, Any] | None:
    return {key: value[key] for key in ("candidate", "prompt")} if value is not None else None


def selection_summary(row: FeedbackOperation) -> dict[str, Any]:
    common.verify_receipt(row)
    return {
        **_pin(row),
        "revision": row.revision,
        "status": row.state["status"],
        "environment": row.state["environment"],
        "actor_id": str(row.actor_id),
        "created_at": row.created_at.astimezone(timezone.utc).isoformat(),
        "selected": _safe_selection(row.state.get("selected")),
        "previous": _safe_selection(row.state.get("previous")),
        "reversion_mode": row.state.get("reversion_mode"),
        "reason_code": row.state.get("reason_code"),
    }


def promotion_detail(
    session: Session,
    context: TenantContext,
    settings: Settings,
    candidate_id: uuid.UUID,
) -> dict[str, Any]:
    trusted = common.require_platform(session, context, evaluation.READ)
    row, prompt = _candidate(session, candidate_id)
    definition_id = prompt.prompt_definition_id
    head = _head(session, definition_id)
    selected = head.state.get("selected") if head else None
    same_candidate = isinstance(selected, dict) and selected.get("candidate") == _pin(row)
    rows = list(
        session.scalars(
            select(FeedbackOperation)
            .where(
                FeedbackOperation.entity_kind == SELECTION,
                FeedbackOperation.entity_id == definition_id,
            )
            .order_by(FeedbackOperation.revision.desc())
            .limit(HISTORY_LIMIT)
        )
    )
    for history_row in rows:
        _verify_selection(history_row, definition_id)
    current_readiness = readiness._verified_history(session, row)[-1]
    ready = _pin(current_readiness) if current_readiness.action == readiness.REVIEW_ACTION else None
    blockers = []
    actions = []
    available = validation_available(session, settings)
    if not available:
        blockers.append("PROMOTION_VALIDATION_ENVIRONMENT_REQUIRED")
    may_promote = set(PROMOTE_PERMISSIONS) <= trusted.platform_permissions
    if (
        ready is None
        or current_readiness.state["status"] != "APPROVED_FOR_NONPRODUCTION_VALIDATION"
    ):
        blockers.append("PROMOTION_APPROVED_READINESS_REQUIRED")
    elif available and may_promote and not same_candidate:
        try:
            eligible = _eligible(
                session, trusted, row, prompt, Pin.model_validate(ready), lock=False
            )
            _require_independent(session, trusted, eligible)
        except (AuthorizationError, ValueError):
            blockers.append("PROMOTION_AUTHORITY_OR_EVIDENCE_UNAVAILABLE")
        else:
            actions.append("AUTHORIZE_AND_SELECT")
    if available and head is not None and same_candidate:
        try:
            _active(session, trusted, head, lock=False)
        except (AuthorizationError, ValueError):
            blockers.append("PROMOTION_CURRENT_SELECTION_UNAVAILABLE")
        else:
            if set(VALIDATE_PERMISSIONS) <= trusted.platform_permissions:
                actions.append("VALIDATE")
        if may_promote:
            try:
                _require_withdrawal(session, trusted, head, row, prompt, lock=False)
            except (AuthorizationError, ValueError):
                blockers.append("PROMOTION_REVERSION_NOT_ELIGIBLE")
            else:
                try:
                    _restore_previous(session, trusted, head, lock=False)
                except (AuthorizationError, ValueError):
                    blockers.append("PROMOTION_REVERSION_NOT_ELIGIBLE")
                    actions.append("CLEAR_SELECTION")
                else:
                    actions.append("REVERT")
    return {
        "prompt_definition_id": str(definition_id),
        "environment": NONPRODUCTION,
        "baseline": row.body["target"],
        "readiness": ready,
        "current_selection": selection_summary(head) if head else None,
        "history": [selection_summary(item) for item in rows],
        "allowed_actions": actions,
        "blockers": blockers,
        "production_readiness": "NOT_READY",
        "production_blocker": PRODUCTION_BLOCKER,
    }
