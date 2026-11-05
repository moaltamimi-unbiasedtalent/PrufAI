"""Append-only human recommendations on exact quality-gate evidence."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError, TenantContext
from app.evaluation import service as foundation
from app.evaluation.contracts import Contract
from app.evaluation.regression_comparison import regression_projection
from app.evaluation.regression_registry import _owner, _scope_access, resolve_regression_suite
from app.evaluation.regression_thresholds import _operation_lock
from app.models.evaluation import REGRESSION_ADAPTER, EvaluationReview, EvaluationRun
from app.writing_assessment.service import canonical_checksum


class ComparisonReviewInput(Contract):
    operation_id: uuid.UUID
    expected_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["ACCEPTED", "REJECTED", "CHANGES_REQUIRED"]
    review_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")


REVIEW_PERMISSIONS = {foundation.READ, foundation.MANAGE, foundation.REVIEW}


def _can_review(session: Session, context: TenantContext, run: EvaluationRun) -> bool:
    excluded = run.configuration.get("review_excluded_actor_ids")
    if (
        not isinstance(excluded, list)
        or run.created_by == context.user_id
        or str(context.user_id) in excluded
        or not REVIEW_PERMISSIONS <= context.platform_permissions
    ):
        return False
    suite = foundation._version(session, run.dataset_version_id)
    try:
        _scope_access(session, context, _owner(session, suite).organisation_id, suite.skill)
    except AuthorizationError:
        return False
    return True


def _regression_run(session: Session, identifier: uuid.UUID) -> EvaluationRun:
    run = foundation._get_run(session, identifier)
    if run.configuration.get("execution_adapter") != REGRESSION_ADAPTER:
        raise foundation.EvaluationConflict("EVALUATION_REGRESSION_RUN_REQUIRED")
    return run


def record_review(
    session: Session,
    context: TenantContext,
    identifier: uuid.UUID,
    payload: ComparisonReviewInput,
) -> EvaluationReview:
    trusted = foundation.require(session, context, *REVIEW_PERMISSIONS)
    run = _regression_run(session, identifier)
    if run.checksum != payload.expected_checksum:
        raise foundation.EvaluationConflict("EVALUATION_RUN_CHECKSUM_MISMATCH")
    foundation.verify_run(session, run)
    if not _can_review(session, trusted, run):
        raise AuthorizationError("EVALUATION_INDEPENDENT_REGRESSION_REVIEW_REQUIRED")
    resolve_regression_suite(
        session, trusted, run.dataset_version_id, run.dataset_checksum, for_execution=False
    )
    if payload.decision == "ACCEPTED" and run.hard_gate != "PASS":
        raise foundation.EvaluationConflict("EVALUATION_HARD_FAIL_CANNOT_BE_APPROVED")
    _operation_lock(session, payload.operation_id)
    request_checksum = canonical_checksum(
        {"run_id": str(run.id), **payload.model_dump(mode="json")}
    )
    previous = session.scalar(
        select(EvaluationReview).where(EvaluationReview.operation_id == payload.operation_id)
    )
    if previous is not None:
        if (
            previous.reviewer_user_id != trusted.user_id
            or previous.request_checksum != request_checksum
        ):
            raise foundation.EvaluationConflict("EVALUATION_OPERATION_CONFLICT")
        return previous
    review = EvaluationReview(
        run_id=run.id,
        run_checksum=payload.expected_checksum,
        operation_id=payload.operation_id,
        request_checksum=request_checksum,
        reviewer_user_id=trusted.user_id,
        decision=payload.decision,
        review_reference=payload.review_reference,
        created_at=foundation.utcnow(),
    )
    session.add(review)
    session.flush()
    foundation._audit(session, trusted, "regression_reviewed", review)
    return review


def review_summary(row: EvaluationReview) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "decision": row.decision,
        "reviewer_user_id": str(row.reviewer_user_id),
        "created_at": row.created_at.isoformat(),
    }


def comparison_detail(
    session: Session, context: TenantContext, run: EvaluationRun
) -> dict[str, Any]:
    trusted = foundation.require(session, context, foundation.READ)
    if run.configuration.get("execution_adapter") != REGRESSION_ADAPTER:
        raise foundation.EvaluationConflict("EVALUATION_REGRESSION_RUN_REQUIRED")
    regression = regression_projection(run, foundation.verify_run(session, run))
    rows = foundation._review_rows(session, run)
    latest = rows[-1].decision if rows else "PENDING"
    ready = (
        latest == "ACCEPTED"
        and run.hard_gate == "PASS"
        and regression["empirical"]["status"] == "PASS"
    )
    return {
        "run": foundation.run_summary(session, run),
        "regression": regression,
        "reviews": [review_summary(row) for row in rows],
        "allowed_actions": ["review"] if _can_review(session, trusted, run) else [],
        "release_recommendation": "ELIGIBLE_FOR_HUMAN_PUBLICATION_REVIEW" if ready else "NOT_READY",
        "production_activation": False,
    }
