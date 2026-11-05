"""Bounded replay measurements and governance, with no assessment write authority."""

from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.evaluation.contracts import validate_case_collection
from app.evaluation.metrics import METHOD_VERSION, aggregate_case_results, evaluate_case
from app.evaluation.schemas import DatasetVersionInput, RunConfiguration, RunRequest
from app.models import (
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationReview,
    EvaluationRun,
    Organisation,
    QuestionBankItemCefrLevel,
    QuestionBankItemVersion,
    QuestionBankStandardPackBinding,
    RubricVersion,
    StandardPackVersion,
)
from app.question_bank.service import validate_item_payload
from app.standard_packs.service import StandardPackService
from app.writing_assessment.service import RubricService, canonical_checksum

READ = "standards.evaluation.read"
RUN = "standards.evaluation.run"
MANAGE = "ai.manage"
REVIEW = "ai.review"
LIMITATIONS = [
    "Original synthetic references; no examiner consensus or pilot calibration.",
    "Technical checks and arithmetic do not establish language-assessment validity.",
    "No empirical deployment threshold or production activation is configured.",
    "Population uncertainty is not estimated from synthetic arithmetic fixtures.",
]


class EvaluationConflict(ValueError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def require(session: Session, context: TenantContext, *permissions: str) -> TenantContext:
    if context.organisation_id is not None:
        raise AuthorizationError("EVALUATION_PLATFORM_CONTEXT_REQUIRED")
    trusted = build_tenant_context(session, context.user_id)
    if not set(permissions) <= trusted.platform_permissions:
        raise AuthorizationError("EVALUATION_PERMISSION_REQUIRED")
    return trusted


def _audit(session: Session, context: TenantContext, event: str, row: Any) -> None:
    AuditService.record(
        session,
        context,
        event_type="evaluation." + event,
        entity_type=type(row).__name__,
        entity_id=str(row.id),
        metadata={
            "checksum": getattr(row, "checksum", None),
            "case_count": getattr(row, "case_count", None),
            "production_activation": False,
        },
        require_audit_permission=False,
    )


def _version(
    session: Session, identifier: uuid.UUID, *, lock: bool = False
) -> EvaluationDatasetVersion:
    query = select(EvaluationDatasetVersion).where(EvaluationDatasetVersion.id == identifier)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(query)
    if row is None:
        raise ValueError("EVALUATION_DATASET_NOT_FOUND")
    actual = canonical_checksum({"provenance": row.provenance, "cases": row.cases})
    if actual != row.checksum or row.case_count != len(row.cases):
        raise EvaluationConflict("EVALUATION_DATASET_CHECKSUM_MISMATCH")
    return row


def version_summary(session: Session, row: EvaluationDatasetVersion) -> dict[str, Any]:
    dataset = session.get(EvaluationDataset, row.dataset_id)
    assert dataset is not None
    return {
        "id": str(row.id),
        "dataset_id": str(row.dataset_id),
        "code": dataset.code,
        "version_number": row.version_number,
        "status": row.status,
        "assessment_family": row.assessment_family,
        "language": row.language,
        "cefr_level": row.cefr_level,
        "skill": row.skill,
        "evidence_class": row.evidence_class,
        "checksum": row.checksum,
        "case_count": row.case_count,
        "approval_status": row.status,
        "created_at": row.created_at.isoformat(),
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "published_at": row.published_at.isoformat() if row.published_at else None,
    }


def create_dataset_version(
    session: Session,
    context: TenantContext,
    payload: DatasetVersionInput,
    dataset_id: uuid.UUID | None = None,
) -> EvaluationDatasetVersion:
    trusted = require(session, context, RUN, MANAGE)
    value = payload.model_dump(mode="json")
    if len(json.dumps(value, allow_nan=False).encode()) > 1_000_000:
        raise ValueError("EVALUATION_DATASET_TOO_LARGE")
    validate_case_collection(value["cases"])
    from app.evaluation.split_integrity import guard_import

    guard_import(session, value["cases"])
    if payload.organisation_id and session.get(Organisation, payload.organisation_id) is None:
        raise ValueError("EVALUATION_ORGANISATION_NOT_FOUND")
    if dataset_id is None:
        if session.scalar(
            select(EvaluationDataset.id).where(EvaluationDataset.code == payload.code)
        ):
            raise EvaluationConflict("EVALUATION_DATASET_CODE_CONFLICT")
        dataset = EvaluationDataset(
            code=payload.code,
            organisation_id=payload.organisation_id,
            created_by=trusted.user_id,
            created_at=utcnow(),
        )
        session.add(dataset)
        session.flush()
    else:
        existing_dataset = session.scalar(
            select(EvaluationDataset).where(EvaluationDataset.id == dataset_id).with_for_update()
        )
        if existing_dataset is None:
            raise ValueError("EVALUATION_DATASET_NOT_FOUND")
        dataset = existing_dataset
        if dataset.code != payload.code or dataset.organisation_id != payload.organisation_id:
            raise EvaluationConflict("EVALUATION_DATASET_IDENTITY_CONFLICT")
    provenance = {key: item for key, item in value.items() if key != "cases"}
    row = EvaluationDatasetVersion(
        dataset_id=dataset.id,
        version_number=1
        + (
            session.scalar(
                select(func.max(EvaluationDatasetVersion.version_number)).where(
                    EvaluationDatasetVersion.dataset_id == dataset.id
                )
            )
            or 0
        ),
        status="DRAFT",
        assessment_family=payload.assessment_family,
        language=payload.language,
        cefr_level=payload.cefr_level,
        skill=payload.skill,
        provenance=provenance,
        cases=value["cases"],
        checksum=canonical_checksum({"provenance": provenance, "cases": value["cases"]}),
        case_count=len(value["cases"]),
        evidence_class="TECHNICAL_TEST",
        created_by=trusted.user_id,
        created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    _audit(session, trusted, "dataset_version_created", row)
    return row


def transition_dataset(
    session: Session,
    context: TenantContext,
    identifier: uuid.UUID,
    action: str,
    expected_checksum: str,
    review_reference: str | None = None,
) -> EvaluationDatasetVersion:
    permissions = (READ, MANAGE, REVIEW) if action == "approve" else (RUN, MANAGE)
    trusted = require(session, context, *permissions)
    row = _version(session, identifier, lock=True)
    if row.checksum != expected_checksum:
        raise EvaluationConflict("EVALUATION_DATASET_CHECKSUM_MISMATCH")
    source, target = {
        "submit": ("DRAFT", "REVIEW_REQUIRED"),
        "approve": ("REVIEW_REQUIRED", "APPROVED"),
        "publish": ("APPROVED", "PUBLISHED"),
    }[action]
    if row.status != source:
        raise EvaluationConflict("EVALUATION_DATASET_STATE_CONFLICT")
    if row.provenance.get("origin") == "GOVERNED_REFERENCE":
        from app.evaluation.expert_service import validate_dataset_transition

        validate_dataset_transition(session, context, row, action)
    if action == "submit":
        row.submitted_at = utcnow()
    elif action == "approve":
        if row.created_by == trusted.user_id or not review_reference:
            raise AuthorizationError("EVALUATION_INDEPENDENT_APPROVAL_REQUIRED")
        row.approved_at, row.approved_by = utcnow(), trusted.user_id
        row.provenance_review_reference = review_reference
    else:
        if not row.approved_by or row.approved_by == row.created_by:
            raise EvaluationConflict("EVALUATION_INDEPENDENT_APPROVAL_REQUIRED")
        row.published_at, row.published_by = utcnow(), trusted.user_id
    row.status = target
    session.flush()
    _audit(session, trusted, "dataset_" + action, row)
    return row


def validate_configuration(
    session: Session,
    dataset: EvaluationDatasetVersion,
    request: RunConfiguration,
) -> dict[str, Any]:
    value = request.model_dump(mode="json")
    pack = session.get(StandardPackVersion, request.standard_pack_version_id)
    if (
        pack is None
        or not pack.configuration_checksum
        or pack.configuration_checksum != request.standard_pack_checksum
        or StandardPackService.checksum(pack) != request.standard_pack_checksum
        or pack.standard_pack.assessment_family.value != dataset.assessment_family
        or pack.standard_pack.language != dataset.language
        or dataset.cefr_level is not None
        and dataset.cefr_level not in pack.supported_levels
        or pack.status.value not in {"PUBLISHED", "ACTIVE", "RETIRED", "SUPERSEDED"}
    ):
        raise EvaluationConflict("EVALUATION_STANDARD_PACK_PIN_MISMATCH")
    if request.rubric_version_id:
        rubric = session.get(RubricVersion, request.rubric_version_id)
        if (
            rubric is None
            or rubric.checksum != request.rubric_checksum
            or canonical_checksum(RubricService.checksum_payload(rubric)) != rubric.checksum
            or rubric.standard_pack_version_id != pack.id
            or rubric.assessment_family.value != dataset.assessment_family
            or rubric.skill.value != dataset.skill
            or rubric.language != dataset.language
            or dataset.cefr_level is not None
            and dataset.cefr_level not in rubric.cefr_levels
            or rubric.status.value
            not in {"APPROVED", "PUBLISHED", "ACTIVE", "RETIRED", "SUPERSEDED"}
        ):
            raise EvaluationConflict("EVALUATION_RUBRIC_PIN_MISMATCH")
    if request.question_bank_item_version_id:
        item = session.get(QuestionBankItemVersion, request.question_bank_item_version_id)
        if item is None or item.checksum != request.item_checksum:
            raise EvaluationConflict("EVALUATION_ITEM_PIN_MISMATCH")
        owner = session.get(EvaluationDataset, dataset.dataset_id)
        levels = list(
            session.scalars(
                select(QuestionBankItemCefrLevel.cefr_level).where(
                    QuestionBankItemCefrLevel.item_version_id == item.id
                )
            )
        )
        if (
            item.assessment_family.value != dataset.assessment_family
            or item.language != dataset.language
            or dataset.cefr_level is not None
            and dataset.cefr_level not in levels
            or dataset.skill in {"WRITING", "SPEAKING"}
            and (item.skill is None or item.skill.value != dataset.skill)
            or item.status.value not in {"APPROVED", "PUBLISHED", "ACTIVE", "RETIRED", "SUPERSEDED"}
            or item.item.scope_type == "ORGANISATION_PRIVATE"
            and (owner is None or owner.organisation_id != item.item.organisation_id)
        ):
            raise EvaluationConflict("EVALUATION_ITEM_SCOPE_MISMATCH")
        # Historical item checksums encode Decimal scale before Numeric(12,4)
        # persistence. Accept only numerically identical representable scales;
        # never rewrite the stored historical checksum or tolerate value drift.
        points = (
            [None]
            if item.max_points is None
            else [
                item.max_points.quantize(Decimal(10) ** -scale)
                for scale in range(5)
                if item.max_points.quantize(Decimal(10) ** -scale) == item.max_points
            ]
        )
        checksums = {
            validate_item_payload(
                assessment_family=item.assessment_family,
                exam_family=item.exam_family,
                language=item.language,
                cefr_levels=levels,
                skill=item.skill,
                task_type=item.task_type,
                response_mode=item.response_mode,
                media_requirement=item.media_requirement,
                content=item.content,
                scoring_policy=item.scoring_policy,
                max_points=value,
                scoring_metadata=item.scoring_metadata,
                rights_status=item.rights_status,
                licence_reference=item.licence_reference,
                rubric_reference=item.rubric_reference,
            ).checksum
            for value in points
        }
        if request.item_checksum not in checksums:
            raise EvaluationConflict("EVALUATION_ITEM_CONTENT_CHECKSUM_MISMATCH")
        if not session.scalar(
            select(QuestionBankStandardPackBinding.id).where(
                QuestionBankStandardPackBinding.item_version_id == item.id,
                QuestionBankStandardPackBinding.standard_pack_version_id == pack.id,
            )
        ):
            raise EvaluationConflict("EVALUATION_ITEM_PACK_BINDING_MISMATCH")
    value["metric_configuration_checksum"] = canonical_checksum(value["metric_configuration"])
    value["implementation_version"] = METHOD_VERSION
    value["not_applicable_reasons"] = {
        key: (
            "NO_MODEL_EXECUTED_SYNTHETIC_OBSERVATION_REPLAY"
            if request.execution_adapter == "SYNTHETIC_OBSERVATION_REPLAY_V1"
            else "NO_SUCH_ARTIFACT_IN_PINNED_EXECUTION_PURPOSE"
        )
        for key in (
            "model_profile_id",
            "model_version",
            "prompt_version_id",
            "agent_version_id",
            "workflow_version_id",
            "profile_version",
        )
        if value[key] is None
    }
    for key in ("rubric_version_id", "question_bank_item_version_id"):
        if value[key] is None:
            value["not_applicable_reasons"][key] = "TECHNICAL_REFERENCE_ARITHMETIC_ONLY"
    return value


def validate_case_configuration(
    session: Session, case: dict[str, Any], pins: dict[str, Any]
) -> None:
    if pins["rubric_version_id"]:
        rubric = session.get(RubricVersion, uuid.UUID(pins["rubric_version_id"]))
        assert rubric is not None
        expected = {str(c["code"]): Decimal(str(c["max_points"])) for c in rubric.criteria}
        actual = {
            str(c["code"]): Decimal(str(c["max_points"])) for c in case["reference"]["criteria"]
        }
        if actual != expected:
            raise EvaluationConflict("EVALUATION_REFERENCE_RUBRIC_CRITERIA_MISMATCH")
    aliases = {
        "item_version_id": "question_bank_item_version_id",
        "item_version_checksum": "item_checksum",
    }
    keys = {
        "standard_pack_version_id",
        "standard_pack_checksum",
        "rubric_version_id",
        "rubric_checksum",
        "question_bank_item_version_id",
        "item_checksum",
    } | set(aliases)
    for identity in (case["input"]["evidence_identity"], case["reference"]["evidence_identity"]):
        for key, value in identity.items():
            pin = aliases.get(key, key)
            if key in keys and value != pins.get(pin):
                raise EvaluationConflict("EVALUATION_CASE_CONFIGURATION_PIN_MISMATCH")


def _run_checksum(run: EvaluationRun, results: list[EvaluationCaseResult]) -> str:
    return canonical_checksum(
        {
            "run_id": str(run.id),
            "dataset_version_id": str(run.dataset_version_id),
            "dataset_checksum": run.dataset_checksum,
            "configuration_checksum": run.configuration_checksum,
            "execution_class": run.execution_class,
            "evidence_class": run.evidence_class,
            "status": run.status,
            "hard_gate": run.hard_gate,
            "metrics": run.metrics,
            "limitations": run.limitations,
            "case_count": run.case_count,
            "failure_count": run.failure_count,
            "results": [
                {"case_id": r.case_id, "checksum": r.checksum}
                for r in sorted(results, key=lambda r: r.case_id)
            ],
        }
    )


def verify_run(session: Session, run: EvaluationRun) -> list[EvaluationCaseResult]:
    version = _version(session, run.dataset_version_id)
    if canonical_checksum(run.configuration) != run.configuration_checksum:
        raise EvaluationConflict("EVALUATION_RUN_PROVENANCE_BROKEN")
    if run.configuration.get("execution_adapter") == "GOVERNED_REFERENCE_CALIBRATION_V1":
        from app.evaluation.expert_comparison import verify_calibration_configuration

        verify_calibration_configuration(session, run, version)
    else:
        configured = RunConfiguration.model_validate(
            {
                key: value
                for key, value in run.configuration.items()
                if key in RunConfiguration.model_fields
            }
        )
        if validate_configuration(session, version, configured) != run.configuration:
            raise EvaluationConflict("EVALUATION_RUN_PROVENANCE_BROKEN")
    results = list(
        session.scalars(select(EvaluationCaseResult).where(EvaluationCaseResult.run_id == run.id))
    )
    if (
        len(results) != run.case_count
        or any(
            canonical_checksum(
                {
                    "input_checksum": r.input_checksum,
                    "observation_checksum": r.observation_checksum,
                    "execution_reference": r.execution_reference,
                    "result": r.result,
                }
            )
            != r.checksum
            for r in results
        )
        or _run_checksum(run, results) != run.checksum
    ):
        raise EvaluationConflict("EVALUATION_RUN_PROVENANCE_BROKEN")
    return results


def execute_run(session: Session, context: TenantContext, request: RunRequest) -> EvaluationRun:
    trusted = require(session, context, RUN, MANAGE)
    request_checksum = canonical_checksum(request.model_dump(mode="json"))
    # Serialize duplicate operation IDs on PostgreSQL without a distributed runner.
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        from sqlalchemy import text

        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": request.operation_id.int % (2**63 - 1)},
        )
    previous = session.scalar(
        select(EvaluationRun).where(EvaluationRun.operation_id == request.operation_id)
    )
    if previous:
        if previous.request_checksum != request_checksum or previous.created_by != trusted.user_id:
            raise EvaluationConflict("EVALUATION_OPERATION_CONFLICT")
        verify_run(session, previous)
        return previous
    dataset = _version(session, request.dataset_version_id, lock=True)
    if dataset.status != "PUBLISHED" or dataset.checksum != request.dataset_checksum:
        raise EvaluationConflict("EVALUATION_PUBLISHED_DATASET_REQUIRED")
    pins = validate_configuration(session, dataset, request.configuration)
    # Select the registered split before executing any case, never post-filter results.
    cases = copy.deepcopy(
        [case for case in dataset.cases if case["split"] == pins["dataset_split"]]
    )
    if not cases:
        raise ValueError("EVALUATION_SELECTED_SPLIT_EMPTY")
    measurements: list[dict[str, Any]] = []
    prepared: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    execution_classes: set[str] = set()
    edit_budget = 0
    for case in cases:
        validate_case_configuration(session, case, pins)
        if pins["execution_adapter"] == "SYNTHETIC_OBSERVATION_REPLAY_V1":
            if case.get("execution_job_id") or case.get("synthetic_observation") is None:
                raise ValueError("EVALUATION_SYNTHETIC_OBSERVATION_REQUIRED")
            observation = case["synthetic_observation"]
            receipt = {
                "execution_class": "TEST_ONLY",
                "model_execution": "NOT_RUN",
                "adapter": pins["execution_adapter"],
            }
            execution_class = "TEST_ONLY"
        else:
            from app.evaluation.replay import load_pinned_observation

            case, observation, receipt, execution_class = load_pinned_observation(
                session, trusted, dataset, case, pins
            )
        execution_classes.add(execution_class)
        if case["kind"] == "TRANSCRIPTION":
            reference_text = case["reference"].get("text") or ""
            hypothesis = observation.get("text") or ""
            if isinstance(hypothesis, str):
                edit_budget += len(reference_text) * len(hypothesis)
                if edit_budget > 5_000_000:
                    raise ValueError("EVALUATION_MEASUREMENT_BUDGET_EXCEEDED")
        result = evaluate_case(
            case,
            observation,
            evidence_class=dataset.evidence_class,
            metric_configuration=pins["metric_configuration"],
        )
        measurements.append(result)
        prepared.append((case, observation, receipt, result))
    if len(execution_classes) != 1:
        raise ValueError("EVALUATION_MIXED_EXECUTION_CLASSES")
    aggregate = aggregate_case_results(measurements)
    row = EvaluationRun(
        operation_id=request.operation_id,
        dataset_version_id=dataset.id,
        dataset_checksum=dataset.checksum,
        request_checksum=request_checksum,
        configuration=pins,
        configuration_checksum=canonical_checksum(pins),
        metrics={},
        limitations=list(LIMITATIONS),
        execution_class=execution_classes.pop(),
        evidence_class=dataset.evidence_class,
        status="RUNNING",
        hard_gate="PENDING",
        case_count=len(cases),
        failure_count=0,
        created_by=trusted.user_id,
        created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    rows = []
    for case, observation, receipt, result in prepared:
        data = {
            "input_checksum": canonical_checksum(case),
            "observation_checksum": canonical_checksum(observation),
            "execution_reference": receipt,
            "result": result,
        }
        item_result = EvaluationCaseResult(
            run_id=row.id,
            case_id=case["case_id"],
            **data,
            checksum=canonical_checksum(data),
            created_at=utcnow(),
        )
        session.add(item_result)
        rows.append(item_result)
    session.flush()  # append only while the persisted parent is RUNNING
    row.metrics = aggregate["metrics"]
    row.failure_count = sum(bool(result["hard_fail"]) for result in measurements)
    row.hard_gate = "FAIL" if aggregate["hard_fail"] else "PASS"
    row.status, row.completed_at = "COMPLETED", utcnow()
    row.checksum = _run_checksum(row, rows)
    session.flush()
    _audit(session, trusted, "run_completed", row)
    return row


def _get_run(session: Session, identifier: uuid.UUID) -> EvaluationRun:
    row = session.get(EvaluationRun, identifier)
    if row is None:
        raise ValueError("EVALUATION_RUN_NOT_FOUND")
    verify_run(session, row)
    return row


def review_run(
    session: Session,
    context: TenantContext,
    identifier: uuid.UUID,
    expected_checksum: str,
    decision: str,
    review_reference: str,
) -> EvaluationReview:
    trusted = require(session, context, READ, MANAGE, REVIEW)
    row = _get_run(session, identifier)
    version = _version(session, row.dataset_version_id)
    if row.checksum != expected_checksum:
        raise EvaluationConflict("EVALUATION_RUN_CHECKSUM_MISMATCH")
    if trusted.user_id in {row.created_by, version.created_by}:
        raise AuthorizationError("EVALUATION_INDEPENDENT_REVIEW_REQUIRED")
    if decision == "ACCEPTED" and row.hard_gate != "PASS":
        raise EvaluationConflict("EVALUATION_HARD_FAIL_CANNOT_BE_APPROVED")
    review = EvaluationReview(
        run_id=row.id,
        run_checksum=expected_checksum,
        reviewer_user_id=trusted.user_id,
        decision=decision,
        review_reference=review_reference,
        created_at=utcnow(),
    )
    session.add(review)
    session.flush()
    _audit(session, trusted, "run_reviewed", review)
    return review


def _review_rows(session: Session, run: EvaluationRun) -> list[EvaluationReview]:
    return list(
        session.scalars(
            select(EvaluationReview)
            .where(EvaluationReview.run_id == run.id)
            .order_by(EvaluationReview.created_at, EvaluationReview.id)
        )
    )


def run_summary(session: Session, row: EvaluationRun) -> dict[str, Any]:
    reviews = _review_rows(session, row)
    return {
        "id": str(row.id),
        "checksum": row.checksum,
        "dataset_version_id": str(row.dataset_version_id),
        "dataset_checksum": row.dataset_checksum,
        "status": row.status,
        "hard_gate": row.hard_gate,
        "execution_class": row.execution_class,
        "evidence_class": row.evidence_class,
        "case_count": row.case_count,
        "failure_count": row.failure_count,
        "created_at": row.created_at.isoformat(),
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "review_status": reviews[-1].decision if reviews else "PENDING",
    }


def run_detail(session: Session, context: TenantContext, identifier: uuid.UUID) -> dict[str, Any]:
    require(session, context, READ)
    row = _get_run(session, identifier)
    results = verify_run(session, row)
    metrics = [
        {
            **value,
            "name": name,
            "numerator": float(value["numerator"]) if value.get("numerator") is not None else None,
            "denominator": float(value["denominator"])
            if value.get("denominator") is not None
            else None,
        }
        for name, value in sorted(row.metrics.items())
    ]
    pins = {
        key: value
        for key, value in row.configuration.items()
        if key
        not in {
            "metric_configuration",
            "not_applicable_reasons",
            "pins",
            "request",
            "side_receipts",
        }
    }
    pins.update(row.configuration.get("pins", {}))
    config = row.configuration["metric_configuration"]
    pins.update(
        metric_schema_version=config["schema_version"],
        metric_normalization=config["normalization"],
        metric_retrieval_k=config["retrieval_k"],
    )
    comparison = None
    if row.configuration.get("execution_adapter") == "GOVERNED_REFERENCE_CALIBRATION_V1":
        full: dict[str, Any] = next(
            (
                result.result["calibration_summary"]
                for result in results
                if result.result.get("calibration_summary")
            ),
            {},
        )
        comparison = {
            key: full.get(key)
            for key in (
                "comparison_kind",
                "left",
                "right",
                "empirical_thresholds",
                "quality_claim",
                "uncertainty",
                "limitations",
                "production_activation",
            )
        }
        comparison["criteria"] = [
            {
                key: criterion.get(key)
                for key in (
                    "criterion_code",
                    "expected_count",
                    "sample_size",
                    "disagreement_distribution",
                )
            }
            for criterion in full.get("criteria", [])
        ]
    return {
        "run": run_summary(session, row),
        "comparison": comparison,
        "pins": pins,
        "metrics": metrics,
        "failures": [
            {"case_id": r.case_id, "failure_categories": r.result["failure_categories"]}
            for r in results
            if r.result["hard_fail"]
        ],
        "reviews": [
            {
                "id": str(r.id),
                "decision": r.decision,
                "reviewer_user_id": str(r.reviewer_user_id),
                "created_at": r.created_at.isoformat(),
            }
            for r in _review_rows(session, row)
        ],
        "limitations": row.limitations,
    }


def list_metadata(
    session: Session, context: TenantContext, kind: str, offset: int, limit: int
) -> dict[str, Any]:
    require(session, context, READ)
    cls = EvaluationDatasetVersion if kind == "datasets" else EvaluationRun
    total = session.scalar(select(func.count()).select_from(cls)) or 0
    rows = session.scalars(
        select(cls).order_by(cls.created_at.desc(), cls.id).offset(offset).limit(limit)
    )
    items = [
        version_summary(session, row)
        if isinstance(row, EvaluationDatasetVersion)
        else run_summary(session, row)
        if isinstance(row, EvaluationRun)
        else {}
        for row in rows
    ]
    return {"items": items, "total": total, "offset": offset, "limit": limit}


def compare_runs(
    session: Session, context: TenantContext, left_id: uuid.UUID, right_id: uuid.UUID
) -> dict[str, Any]:
    require(session, context, READ)
    left, right = _get_run(session, left_id), _get_run(session, right_id)
    left_dataset, right_dataset = (
        _version(session, left.dataset_version_id),
        _version(session, right.dataset_version_id),
    )
    if (
        left_dataset.dataset_id != right_dataset.dataset_id
        or left_dataset.skill != right_dataset.skill
        or left_dataset.assessment_family != right_dataset.assessment_family
        or left_dataset.language != right_dataset.language
        or left_dataset.cefr_level != right_dataset.cefr_level
        or left.configuration["dataset_split"] != right.configuration["dataset_split"]
        or left.configuration["evaluation_method_version"]
        != right.configuration["evaluation_method_version"]
        or left.configuration["metric_configuration_checksum"]
        != right.configuration["metric_configuration_checksum"]
        or left.execution_class != right.execution_class
    ):
        raise EvaluationConflict("EVALUATION_COMPARISON_INCOMPATIBLE")
    differences = []
    for name in sorted(set(left.metrics) | set(right.metrics)):
        a, b = left.metrics.get(name, {}), right.metrics.get(name, {})
        av, bv = a.get("value"), b.get("value")
        differences.append(
            {
                "name": name,
                "left": av,
                "right": bv,
                "difference": bv - av
                if isinstance(av, (int, float)) and isinstance(bv, (int, float))
                else None,
                "left_sample_size": a.get("sample_size", 0),
                "right_sample_size": b.get("sample_size", 0),
                "evidence_class": left.evidence_class,
                "limitations": [
                    "Descriptive comparison; no empirical pass threshold or causal claim."
                ],
            }
        )
    return {
        "left_run_id": str(left.id),
        "right_run_id": str(right.id),
        "metrics": differences,
        "same_case_population": left.dataset_checksum == right.dataset_checksum,
        "changed_configuration_pins": [
            key
            for key in left.configuration
            if left.configuration.get(key) != right.configuration.get(key)
        ],
        "comparison_limitations": [
            "Changed datasets are different cohorts; "
            "deltas are descriptive and not paired agreement.",
            "Changed rubrics or items may change score scales; "
            "no accuracy improvement is inferred.",
        ],
        "quality_claim": "NOT_ESTABLISHED",
        "production_activation": False,
    }
