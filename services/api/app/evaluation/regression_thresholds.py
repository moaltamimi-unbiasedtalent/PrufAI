"""Exact, human-approved empirical policies. The registry is empty by default."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError, TenantContext
from app.evaluation import expert_service as expert
from app.evaluation import service as foundation
from app.evaluation.contracts import Contract
from app.evaluation.regression_registry import (
    ArtifactPin,
    regression_reference_sources,
    regression_review_excluded_actor_ids,
    resolve_regression_suite,
)
from app.models.evaluation import (
    REGRESSION_ADAPTER,
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationRun,
    EvaluationThresholdDefinition,
)
from app.writing_assessment.service import canonical_checksum

Reference = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")]
Checksum = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Metric = Literal["mean_absolute_criterion_error", "word_error_rate", "character_error_rate"]


class ThresholdDefinitionInput(Contract):
    operation_id: uuid.UUID
    suite_version: ArtifactPin
    calibration_study: ArtifactPin
    metric: Metric
    metric_implementation_version: str = Field(min_length=1, max_length=100)
    metric_configuration_checksum: Checksum
    basis: Literal["CANDIDATE_VALUE", "PAIRED_DELTA"]
    maximum: Decimal = Field(allow_inf_nan=False, max_digits=14, decimal_places=8)
    minimum_paired_cases: int = Field(ge=2, le=100, strict=True)
    effective_from: datetime
    effective_until: datetime
    study_method_reference: Reference
    rationale: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_window(self) -> ThresholdDefinitionInput:
        if (
            self.effective_from.utcoffset() is None
            or self.effective_until.utcoffset() is None
            or self.effective_until <= self.effective_from
            or self.basis == "CANDIDATE_VALUE"
            and self.maximum < 0
            or not self.rationale.strip()
        ):
            raise ValueError("EVALUATION_THRESHOLD_DEFINITION_INVALID")
        return self


class ThresholdApprovalInput(Contract):
    expected_checksum: Checksum
    qualification: ArtifactPin
    study_review_reference: Reference


def _operation_lock(session: Session, identifier: uuid.UUID) -> None:
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": identifier.int % (2**63 - 1)},
        )


def _row_integrity(row: EvaluationThresholdDefinition) -> None:
    proposal = canonical_checksum({"definition": row.body, "created_by": str(row.created_by)})
    expected = proposal
    if row.status == "APPROVED":
        if (
            row.approval is None
            or row.approved_by is None
            or row.approved_at is None
            or row.approved_by == row.created_by
            or row.approval.get("actor_id") != str(row.approved_by)
            or datetime.fromisoformat(row.approval["approved_at"]) != row.approved_at
        ):
            raise foundation.EvaluationConflict("EVALUATION_THRESHOLD_APPROVAL_INVALID")
        expected = canonical_checksum({"proposal_checksum": proposal, "approval": row.approval})
    elif row.status != "DRAFT" or row.approval is not None:
        raise foundation.EvaluationConflict("EVALUATION_THRESHOLD_STATE_INVALID")
    if (
        proposal != row.proposal_checksum
        or row.checksum != expected
        or str(row.suite_version_id) != row.body.get("suite_version", {}).get("id")
    ):
        raise foundation.EvaluationConflict("EVALUATION_THRESHOLD_PROVENANCE_INVALID")


def verify_threshold_pin(
    session: Session, policy_pin: ArtifactPin | dict[str, Any]
) -> EvaluationThresholdDefinition:
    pin = ArtifactPin.model_validate(policy_pin)
    row = session.get(EvaluationThresholdDefinition, pin.id)
    if row is None or row.status != "APPROVED" or row.checksum != pin.checksum:
        raise foundation.EvaluationConflict("EVALUATION_APPROVED_THRESHOLD_PIN_REQUIRED")
    _row_integrity(row)
    return row


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _ratio(metric: Any) -> Decimal | None:
    if not isinstance(metric, dict) or metric.get("missing_count") != 0:
        return None
    numerator, denominator = _number(metric.get("numerator")), _number(metric.get("denominator"))
    if numerator is None or numerator < 0 or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _study(
    session: Session, context: TenantContext, payload: ThresholdDefinitionInput
) -> tuple[EvaluationDatasetVersion, EvaluationRun]:
    from app.evaluation.regression_comparison import regression_projection

    suite = resolve_regression_suite(
        session,
        context,
        payload.suite_version.id,
        payload.suite_version.checksum,
        for_execution=False,
    )
    if (
        suite.evidence_class != "EXPERT_LABELLED"
        or suite.provenance.get("split") not in {"VALIDATION", "TEST", "HELD_OUT"}
        or suite.case_count < payload.minimum_paired_cases
        or suite.provenance.get("implementation_version") != payload.metric_implementation_version
        or suite.provenance.get("metric_configuration_checksum")
        != payload.metric_configuration_checksum
    ):
        raise foundation.EvaluationConflict("EVALUATION_THRESHOLD_GENUINE_REFERENCE_REQUIRED")
    run = foundation._get_run(session, payload.calibration_study.id)
    if (
        run.checksum != payload.calibration_study.checksum
        or run.configuration.get("execution_adapter") != REGRESSION_ADAPTER
        or run.execution_class != "REAL_PROVIDER"
        or run.evidence_class != "EXPERT_LABELLED"
        or run.case_count < payload.minimum_paired_cases
        or run.hard_gate != "PASS"
    ):
        raise foundation.EvaluationConflict("EVALUATION_THRESHOLD_GENUINE_STUDY_REQUIRED")
    study_suite = resolve_regression_suite(
        session, context, run.dataset_version_id, run.dataset_checksum, for_execution=False
    )
    target_owner = session.get(EvaluationDataset, suite.dataset_id)
    study_owner = session.get(EvaluationDataset, study_suite.dataset_id)
    if (
        target_owner is None
        or study_owner is None
        or target_owner.organisation_id != study_owner.organisation_id
        or study_suite.provenance.get("split") != "CALIBRATION"
        or study_suite.skill != suite.skill
        or study_suite.language != suite.language
        or study_suite.cefr_level != suite.cefr_level
        or study_suite.assessment_family != suite.assessment_family
        or any(
            study_suite.provenance.get(key) != suite.provenance.get(key)
            for key in (
                "source_scope",
                "evaluation_method",
                "evaluation_method_version",
                "implementation_version",
                "metric_configuration_checksum",
            )
        )
    ):
        raise foundation.EvaluationConflict("EVALUATION_THRESHOLD_STUDY_SCOPE_MISMATCH")
    study = regression_projection(run, foundation.verify_run(session, run))
    comparison = next(
        (item for item in study["paired_metrics"] if item["name"] == payload.metric), None
    )
    if (
        run.configuration.get("implementation_version") != payload.metric_implementation_version
        or run.configuration.get("metric_configuration_checksum")
        != payload.metric_configuration_checksum
        or comparison is None
        or comparison.get("paired_case_count") != run.case_count
        or _ratio(comparison.get("baseline")) is None
        or _ratio(comparison.get("candidate")) is None
    ):
        raise foundation.EvaluationConflict("EVALUATION_THRESHOLD_STUDY_MEASUREMENT_REQUIRED")
    return suite, run


def propose_threshold(
    session: Session, context: TenantContext, payload: ThresholdDefinitionInput
) -> EvaluationThresholdDefinition:
    trusted = foundation.require(
        session, context, foundation.READ, foundation.MANAGE, foundation.REVIEW
    )
    _operation_lock(session, payload.operation_id)
    body = payload.model_dump(mode="json")
    checksum = canonical_checksum({"definition": body, "created_by": str(trusted.user_id)})
    previous = session.scalar(
        select(EvaluationThresholdDefinition).where(
            EvaluationThresholdDefinition.operation_id == payload.operation_id
        )
    )
    if previous is not None:
        _row_integrity(previous)
        if previous.created_by != trusted.user_id or previous.proposal_checksum != checksum:
            raise foundation.EvaluationConflict("EVALUATION_OPERATION_CONFLICT")
        return previous
    suite, _ = _study(session, trusted, payload)
    row = EvaluationThresholdDefinition(
        operation_id=payload.operation_id,
        suite_version_id=suite.id,
        status="DRAFT",
        body=body,
        checksum=checksum,
        proposal_checksum=checksum,
        created_by=trusted.user_id,
        created_at=foundation.utcnow(),
    )
    session.add(row)
    session.flush()
    foundation._audit(session, trusted, "threshold_proposed", row)
    return row


def approve_threshold(
    session: Session, context: TenantContext, identifier: uuid.UUID, request: ThresholdApprovalInput
) -> EvaluationThresholdDefinition:
    trusted = foundation.require(
        session, context, foundation.READ, foundation.MANAGE, foundation.REVIEW
    )
    row = session.scalar(
        select(EvaluationThresholdDefinition)
        .where(EvaluationThresholdDefinition.id == identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ValueError("EVALUATION_THRESHOLD_NOT_FOUND")
    _row_integrity(row)
    if request.expected_checksum != row.proposal_checksum:
        raise foundation.EvaluationConflict("EVALUATION_THRESHOLD_CHECKSUM_MISMATCH")
    approval = {
        "actor_id": str(trusted.user_id),
        "qualification": request.qualification.model_dump(mode="json"),
        "study_review_reference": request.study_review_reference,
    }
    if row.status == "APPROVED":
        if row.approval is None or any(
            row.approval.get(key) != value for key, value in approval.items()
        ):
            raise foundation.EvaluationConflict("EVALUATION_THRESHOLD_ALREADY_APPROVED")
        return row
    payload = ThresholdDefinitionInput.model_validate(row.body)
    suite, study = _study(session, trusted, payload)
    excluded = regression_review_excluded_actor_ids(session, suite)
    if (
        trusted.user_id in {row.created_by, study.created_by, *excluded}
        or str(trusted.user_id) in study.configuration["review_excluded_actor_ids"]
    ):
        raise AuthorizationError("EVALUATION_INDEPENDENT_THRESHOLD_APPROVAL_REQUIRED")
    sources = regression_reference_sources(session, suite)
    if not sources or any(source.evidence_class != "APPROVED_REFERENCE" for source in sources):
        raise foundation.EvaluationConflict("EVALUATION_THRESHOLD_GENUINE_REFERENCE_REQUIRED")
    for source in sources:
        expert._qualification(
            session, trusted, source, request.qualification.id, request.qualification.checksum
        )
    approved_at = foundation.utcnow()
    if payload.effective_until <= approved_at:
        raise foundation.EvaluationConflict("EVALUATION_THRESHOLD_EFFECTIVE_WINDOW_EXPIRED")
    row.approved_at = approved_at
    row.approved_by = trusted.user_id
    row.status = "APPROVED"
    row.approval = {**approval, "approved_at": approved_at.isoformat()}
    row.checksum = canonical_checksum(
        {"proposal_checksum": row.proposal_checksum, "approval": row.approval}
    )
    session.flush()
    foundation._audit(session, trusted, "threshold_approved", row)
    return row


def threshold_summary(row: EvaluationThresholdDefinition) -> dict[str, Any]:
    _row_integrity(row)
    return {
        "id": str(row.id),
        "checksum": row.checksum,
        "status": row.status,
        "suite_version": row.body["suite_version"],
        "metric": row.body["metric"],
        "metric_implementation_version": row.body["metric_implementation_version"],
        "metric_configuration_checksum": row.body["metric_configuration_checksum"],
        "basis": row.body["basis"],
        "maximum": row.body["maximum"],
        "minimum_paired_cases": row.body["minimum_paired_cases"],
        "effective_from": row.body["effective_from"],
        "effective_until": row.body["effective_until"],
        "created_by": str(row.created_by),
        "approved_by": str(row.approved_by) if row.approved_by else None,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "production_activation": False,
    }


def threshold_workbench(
    session: Session, context: TenantContext, identifier: uuid.UUID
) -> dict[str, Any]:
    trusted = foundation.require(
        session, context, foundation.READ, foundation.MANAGE, foundation.REVIEW
    )
    row = session.get(EvaluationThresholdDefinition, identifier)
    if row is None:
        raise ValueError("EVALUATION_THRESHOLD_NOT_FOUND")
    _row_integrity(row)
    payload = ThresholdDefinitionInput.model_validate(row.body)
    suite, _ = _study(session, trusted, payload)
    return {
        **threshold_summary(row),
        "proposal_checksum": row.proposal_checksum,
        "scope": {
            "assessment_family": suite.assessment_family,
            "language": suite.language,
            "cefr_level": suite.cefr_level,
            "skill": suite.skill,
            "split": suite.provenance["split"],
            "evidence_maturity": suite.evidence_class,
        },
        "calibration_study": payload.calibration_study.model_dump(mode="json"),
        "study_method_reference": payload.study_method_reference,
        "rationale": payload.rationale,
    }


def evaluate_threshold_policy(
    session: Session,
    context: TenantContext,
    policy_pin: ArtifactPin | None,
    *,
    suite: EvaluationDatasetVersion,
    baseline: EvaluationRun,
    candidate: EvaluationRun,
    paired_metrics: list[dict[str, Any]],
    expected_case_count: int,
    paired_case_count: int,
    evaluated_at: datetime,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "UNCONFIGURED",
        "policy": None,
        "reason_code": "NO_APPROVED_THRESHOLD_PIN",
        "metric_name": None,
        "observed_value": None,
        "required_paired_cases": None,
        "paired_case_count": paired_case_count,
    }
    if policy_pin is None:
        return result
    foundation.require(session, context, foundation.RUN, foundation.MANAGE)
    row = verify_threshold_pin(session, policy_pin)
    policy = ThresholdDefinitionInput.model_validate(row.body)
    result.update(
        status="NOT_EVALUATED",
        policy=policy_pin.model_dump(mode="json"),
        metric_name=policy.metric,
        required_paired_cases=policy.minimum_paired_cases,
    )
    if (
        policy.suite_version.id != suite.id
        or policy.suite_version.checksum != suite.checksum
        or suite.evidence_class != "EXPERT_LABELLED"
        or any(
            run.evidence_class != "EXPERT_LABELLED"
            or run.execution_class != "REAL_PROVIDER"
            or run.configuration.get("implementation_version")
            != policy.metric_implementation_version
            or run.configuration.get("metric_configuration_checksum")
            != policy.metric_configuration_checksum
            for run in (baseline, candidate)
        )
    ):
        return {**result, "reason_code": "THRESHOLD_SCOPE_OR_EVIDENCE_MISMATCH"}
    if (
        row.approved_at is None
        or not max(row.approved_at, policy.effective_from) <= evaluated_at < policy.effective_until
    ):
        return {**result, "reason_code": "THRESHOLD_NOT_EFFECTIVE"}
    metric = next((item for item in paired_metrics if item["name"] == policy.metric), {})
    if (
        paired_case_count != expected_case_count
        or paired_case_count < policy.minimum_paired_cases
        or metric.get("paired_case_count") != paired_case_count
    ):
        return {**result, "reason_code": "INSUFFICIENT_PAIRED_REFERENCE_COVERAGE"}
    baseline_value, candidate_value = (
        _ratio(metric.get("baseline")),
        _ratio(metric.get("candidate")),
    )
    if baseline_value is None or candidate_value is None:
        return {**result, "reason_code": "EMPIRICAL_MEASUREMENT_UNAVAILABLE"}
    value = candidate_value - baseline_value if policy.basis == "PAIRED_DELTA" else candidate_value
    return {
        **result,
        "status": "PASS" if value <= policy.maximum else "FAIL",
        "reason_code": None,
        "observed_value": str(value),
    }
