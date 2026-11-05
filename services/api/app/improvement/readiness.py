"""Seal measured comparison evidence and one independent readiness decision."""

from __future__ import annotations

import uuid
from datetime import timezone
from typing import Any, Literal

from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError, TenantContext
from app.evaluation import regression_review
from app.evaluation import service as evaluation
from app.evaluation.contracts import Contract
from app.evaluation.prompt_policy import execute_prompt_policy_candidate
from app.evaluation.regression_comparison import (
    RegressionComparisonInput,
    execute_regression_comparison,
    regression_projection,
)
from app.evaluation.regression_registry import ArtifactPin
from app.feedback.common import (
    FeedbackConflict,
    Pin,
    latest,
    operation,
    record,
    require_platform,
    verify_receipt,
)
from app.improvement import candidates
from app.models.evaluation import EvaluationReview, EvaluationRun
from app.models.feedback import FeedbackOperation, GovernedChangeCandidate, ImprovementProposal

KIND = "CHANGE_CANDIDATE"
EVALUATE_ACTION = "change_candidate_evaluated"
REVIEW_ACTION = "change_candidate_reviewed"
EVALUATE_PERMISSIONS = {evaluation.READ, evaluation.RUN, evaluation.MANAGE}
REVIEW_PERMISSIONS = regression_review.REVIEW_PERMISSIONS


class EvaluateCandidateInput(Contract):
    operation_id: uuid.UUID
    expected_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReviewCandidateInput(EvaluateCandidateInput):
    decision: Literal["APPROVE_FOR_NONPRODUCTION_VALIDATION", "NOT_READY"]
    rationale: str = Field(min_length=8, max_length=500)

    @field_validator("rationale")
    @classmethod
    def clean_rationale(cls, value: str) -> str:
        if len(value.strip()) < 8:
            raise ValueError("IMPROVEMENT_READINESS_RATIONALE_REQUIRED")
        return value.strip()


def _pin(run: EvaluationRun | FeedbackOperation) -> dict[str, str]:
    if run.checksum is None:
        raise FeedbackConflict("IMPROVEMENT_UNSEALED_RUN")
    return {"id": str(run.id), "checksum": run.checksum}


def _get(
    session: Session, identifier: uuid.UUID, *, lock: bool = False
) -> GovernedChangeCandidate | None:
    statement = select(GovernedChangeCandidate).where(
        GovernedChangeCandidate.id == identifier, GovernedChangeCandidate.organisation_id.is_(None)
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return session.scalar(statement)


def _command(
    session: Session,
    context: TenantContext,
    identifier: uuid.UUID,
    payload: EvaluateCandidateInput,
    action: str,
    allowed_states: set[str],
) -> tuple[GovernedChangeCandidate, FeedbackOperation, dict[str, Any], bool]:
    request = {"candidate_id": str(identifier), **payload.model_dump(mode="json")}
    prior = operation(session, context, payload.operation_id, action, request)
    row = _get(session, identifier, lock=True)
    if row is None:
        raise FeedbackConflict("IMPROVEMENT_CANDIDATE_UNAVAILABLE")
    candidates.verify_change_candidate(session, row)
    if prior is not None:
        if prior.entity_kind != KIND or prior.entity_id != row.id:
            raise FeedbackConflict("IMPROVEMENT_OPERATION_CONFLICT")
        _state_projection(row, prior)
        return row, prior, request, True
    receipt = latest(session, KIND, row.id)
    _state_projection(row, receipt)
    if (
        receipt.checksum != payload.expected_checksum
        or receipt.state["status"] not in allowed_states
    ):
        raise FeedbackConflict("IMPROVEMENT_CANDIDATE_STATE_CONFLICT")
    candidates.require_current_candidate_authority(session, context, row)
    return row, receipt, request, False


def _comparison_evidence(run: EvaluationRun, report: dict[str, Any]) -> dict[str, Any]:
    differences = []
    for case in report["case_findings"]:
        left = {check["code"] for check in case["checks"] if check["baseline"]["status"] == "FAIL"}
        right = {
            check["code"] for check in case["checks"] if check["candidate"]["status"] == "FAIL"
        }
        # Retain other safe source failure categories as well as required check failures.
        left.update(case["baseline_failure_categories"])
        right.update(case["candidate_failure_categories"])
        differences.append(
            {
                "case_id": case["case_id"],
                "new_failures": sorted(right - left),
                "resolved_failures": sorted(left - right),
                "unchanged_failures": sorted(left & right),
            }
        )
    return {
        "baseline_run": report["baseline_run"],
        "candidate_run": report["candidate_run"],
        "comparison_run": _pin(run),
        "comparison_status": report["comparison_status"],
        "technical_status": report["technical_status"],
        "empirical_status": report["empirical_status"],
        "case_differences": differences,
        "missing_case_count": len(
            set(report["missing_baseline_case_ids"]) | set(report["missing_candidate_case_ids"])
        ),
    }


def _result_status(report: dict[str, Any]) -> str:
    if report["comparison_status"] == "NOT_COMPARABLE":
        return "NOT_COMPARABLE"
    if report["technical_status"] == "FAIL":
        return "TECHNICAL_GATE_FAILED"
    if (
        report["comparison_status"] != "COMPARABLE"
        or report["technical_status"] != "PASS"
        or not report["complete"]
        or report["paired_case_count"] != report["expected_case_count"]
    ):
        return "NOT_READY"
    return "HUMAN_REVIEW_REQUIRED"


def evaluate_candidate(
    session: Session,
    context: TenantContext,
    identifier: uuid.UUID,
    payload: EvaluateCandidateInput,
) -> FeedbackOperation:
    trusted = require_platform(session, context, *EVALUATE_PERMISSIONS)
    row, previous, request, replay = _command(
        session, trusted, identifier, payload, EVALUATE_ACTION, {"AWAITING_EVALUATION"}
    )
    if replay:
        return previous
    baseline = Pin.model_validate(row.body["baseline_run"])
    candidate = execute_prompt_policy_candidate(
        session,
        trusted,
        operation_id=uuid.uuid5(payload.operation_id, "prompt-policy-candidate"),
        change_candidate_id=row.id,
        change_candidate_checksum=row.checksum,
        baseline_run=baseline,
    )
    run = execute_regression_comparison(
        session,
        trusted,
        RegressionComparisonInput(
            operation_id=uuid.uuid5(payload.operation_id, "prompt-policy-comparison"),
            suite_version=ArtifactPin.model_validate(row.body["suite_version"]),
            baseline_run=ArtifactPin(id=baseline.id, checksum=baseline.checksum),
            candidate_run=ArtifactPin.model_validate(_pin(candidate)),
            dimension="PROMPT",
        ),
    )
    report = regression_projection(run, evaluation.verify_run(session, run))
    return record(
        session,
        trusted,
        payload.operation_id,
        EVALUATE_ACTION,
        request,
        entity_kind=KIND,
        entity_id=row.id,
        previous=previous,
        state={
            "status": _result_status(report),
            "candidate_checksum": row.checksum,
            "production_readiness": "NOT_READY",
            "evaluation": _comparison_evidence(run, report),
        },
    )


def _linked_comparison(
    session: Session, row: GovernedChangeCandidate, receipt: FeedbackOperation
) -> tuple[EvaluationRun, dict[str, Any]]:
    snapshot = receipt.state.get("evaluation")
    if not isinstance(snapshot, dict):
        raise FeedbackConflict("IMPROVEMENT_COMPARISON_UNAVAILABLE")
    exact = Pin.model_validate(snapshot["comparison_run"])
    run = evaluation._get_run(session, exact.id)
    if run.checksum != exact.checksum:
        raise FeedbackConflict("IMPROVEMENT_COMPARISON_CHECKSUM_MISMATCH")
    report = regression_projection(run, evaluation.verify_run(session, run))
    candidate_pin = Pin.model_validate(report["candidate_run"])
    candidate_run = evaluation._get_run(session, candidate_pin.id)
    if (
        candidate_run.checksum != candidate_pin.checksum
        or candidate_run.configuration.get("change_candidate")
        != {
            "id": str(row.id),
            "checksum": row.checksum,
        }
        or report["dimension"] != "PROMPT"
        or report["baseline_run"] != row.body["baseline_run"]
        or report["suite_version"] != row.body["suite_version"]
        or snapshot != _comparison_evidence(run, report)
    ):
        raise FeedbackConflict("IMPROVEMENT_COMPARISON_LINK_MISMATCH")
    return run, report


def _can_review(
    session: Session, context: TenantContext, row: GovernedChangeCandidate, run: EvaluationRun
) -> bool:
    proposal = session.get(ImprovementProposal, row.proposal_version_id)
    approval = session.get(FeedbackOperation, uuid.UUID(row.body["approval"]["id"]))
    return bool(
        proposal is not None
        and approval is not None
        and context.user_id not in {row.created_by, proposal.created_by, approval.actor_id}
        and regression_review._can_review(session, context, run)
    )


def review_candidate(
    session: Session,
    context: TenantContext,
    identifier: uuid.UUID,
    payload: ReviewCandidateInput,
) -> FeedbackOperation:
    trusted = require_platform(session, context, *REVIEW_PERMISSIONS)
    row, previous, request, replay = _command(
        session,
        trusted,
        identifier,
        payload,
        REVIEW_ACTION,
        {"HUMAN_REVIEW_REQUIRED", "TECHNICAL_GATE_FAILED", "NOT_COMPARABLE", "NOT_READY"},
    )
    if replay:
        return previous
    run, report = _linked_comparison(session, row, previous)
    if not _can_review(session, trusted, row, run):
        raise AuthorizationError("IMPROVEMENT_INDEPENDENT_REVIEW_REQUIRED")
    approve = payload.decision == "APPROVE_FOR_NONPRODUCTION_VALIDATION"
    if approve and (_result_status(report) != "HUMAN_REVIEW_REQUIRED" or run.hard_gate != "PASS"):
        raise FeedbackConflict("IMPROVEMENT_NONPRODUCTION_VALIDATION_NOT_ELIGIBLE")
    review = regression_review.record_review(
        session,
        trusted,
        run.id,
        regression_review.ComparisonReviewInput(
            operation_id=payload.operation_id,
            expected_checksum=_pin(run)["checksum"],
            decision="ACCEPTED" if approve else "CHANGES_REQUIRED",
            review_reference=f"improvement:{row.id}:{payload.operation_id}",
        ),
        governed_improvement=True,
    )
    return record(
        session,
        trusted,
        payload.operation_id,
        REVIEW_ACTION,
        request,
        entity_kind=KIND,
        entity_id=row.id,
        previous=previous,
        state={
            **previous.state,
            "status": "APPROVED_FOR_NONPRODUCTION_VALIDATION" if approve else "NOT_READY",
            "decision": payload.decision,
            "rationale": payload.rationale,
            "evaluation_review_id": str(review.id),
        },
    )


def receipt_summary(row: FeedbackOperation) -> dict[str, Any]:
    return {
        **_pin(row),
        "revision": row.revision,
        "action": row.action,
        "actor_id": str(row.actor_id),
        "created_at": row.created_at.astimezone(timezone.utc).isoformat(),
        "status": row.state["status"],
        "decision": row.state.get("decision"),
    }


def _state_projection(row: GovernedChangeCandidate, receipt: FeedbackOperation) -> dict[str, Any]:
    if (
        receipt.state["candidate_checksum"] != row.checksum
        or receipt.state["production_readiness"] != "NOT_READY"
    ):
        raise FeedbackConflict("IMPROVEMENT_RECEIPT_LINK_MISMATCH")
    return {
        "status": receipt.state["status"],
        "receipt": _pin(receipt),
        "production_readiness": "NOT_READY",
        "evaluation": receipt.state.get("evaluation"),
    }


def candidate_summary(
    session: Session, context: TenantContext, row: GovernedChangeCandidate
) -> dict[str, Any]:
    return {
        **candidates.candidate_summary(session, context, row),
        **_state_projection(row, latest(session, KIND, row.id)),
    }


def _verified_history(session: Session, row: GovernedChangeCandidate) -> list[FeedbackOperation]:
    receipts = list(
        session.scalars(
            select(FeedbackOperation)
            .where(FeedbackOperation.entity_kind == KIND, FeedbackOperation.entity_id == row.id)
            .order_by(FeedbackOperation.revision)
        )
    )
    if not receipts or len(receipts) > 3:
        raise FeedbackConflict("IMPROVEMENT_HISTORY_UNAVAILABLE")
    previous_checksum = None
    actions = (candidates.CREATED, EVALUATE_ACTION, REVIEW_ACTION)
    for expected, (receipt, action) in enumerate(zip(receipts, actions, strict=False), start=1):
        verify_receipt(receipt)
        _state_projection(row, receipt)
        if (
            receipt.revision != expected
            or receipt.action != action
            or receipt.previous_checksum != previous_checksum
        ):
            raise FeedbackConflict("IMPROVEMENT_HISTORY_PREDECESSOR_MISMATCH")
        previous_checksum = receipt.checksum
    return receipts


def candidate_detail(
    session: Session, context: TenantContext, row: GovernedChangeCandidate
) -> dict[str, Any]:
    trusted = require_platform(session, context, evaluation.READ)
    immutable = candidates.candidate_summary(session, trusted, row)
    receipts = _verified_history(session, row)
    current = receipts[-1]
    state = _state_projection(row, current)
    history = [receipt_summary(receipt) for receipt in receipts]
    comparison = None
    allowed_actions: list[str] = []
    authority_current = True
    if current.action != REVIEW_ACTION:
        try:
            candidates.require_current_candidate_authority(session, trusted, row, lock=False)
        except (AuthorizationError, FeedbackConflict):
            authority_current = False
    if current.state["status"] == "AWAITING_EVALUATION":
        if authority_current and EVALUATE_PERMISSIONS <= trusted.platform_permissions:
            allowed_actions.append("EVALUATE")
    elif state["evaluation"] is not None:
        run, _ = _linked_comparison(session, row, current)
        comparison = regression_review.comparison_detail(session, trusted, run)
        if current.action == REVIEW_ACTION:
            review = session.get(EvaluationReview, uuid.UUID(current.state["evaluation_review_id"]))
            if (
                review is None
                or review.run_id != run.id
                or review.run_checksum != run.checksum
                or review.operation_id != current.id
                or review.reviewer_user_id != current.actor_id
                or review.decision
                != (
                    "ACCEPTED"
                    if current.state["decision"] == "APPROVE_FOR_NONPRODUCTION_VALIDATION"
                    else "CHANGES_REQUIRED"
                )
            ):
                raise FeedbackConflict("IMPROVEMENT_REVIEW_LINK_MISMATCH")
            if REVIEW_PERMISSIONS <= trusted.platform_permissions:
                history[-1]["rationale"] = current.state["rationale"]
        elif authority_current and _can_review(session, trusted, row, run):
            allowed_actions.append("REVIEW")
    return {
        **immutable,
        **state,
        "history": history,
        "comparison": comparison,
        "allowed_actions": allowed_actions,
        "proposal_authority_current": authority_current
        if current.action != REVIEW_ACTION
        else None,
    }
