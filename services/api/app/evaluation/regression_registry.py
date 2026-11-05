"""Promote measured failures and approved observations into governed suites."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.authorization.membership import require_active_tenant_permission
from app.evaluation import service as foundation
from app.evaluation.contracts import Contract, EvaluationCase, validate_case_collection
from app.evaluation.expert_service import _operation
from app.evaluation.metrics import evaluate_case
from app.evaluation.prompt_policy import ADAPTER as PROMPT_POLICY_ADAPTER
from app.evaluation.prompt_policy import CHECK_FAILURES as PROMPT_POLICY_CHECK_FAILURES
from app.evaluation.replay import SOURCE_KEYS, load_pinned_observation
from app.evaluation.split_integrity import case_split_keys, guard_regression, lock_splits
from app.models import (
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationRun,
)
from app.models.expert_evaluation import EvaluationReference
from app.writing_assessment.service import canonical_checksum

CASE_ORIGIN = "DYNAMIC_REGRESSION_CASE"
SUITE_ORIGIN = "DYNAMIC_REGRESSION_SUITE"
REGRESSION_ORIGINS = {CASE_ORIGIN, SUITE_ORIGIN}
PURPOSE = "DYNAMIC_REGRESSION"
CHECKSUM_PATTERN = r"^[a-f0-9]{64}$"
REFERENCE_KEYS = (
    "reference_id",
    "reference_checksum",
    "adjudication_id",
    "adjudication_checksum",
)
METHOD_KEYS = (
    "evaluation_method",
    "evaluation_method_version",
    "implementation_version",
    "metric_configuration",
    "metric_configuration_checksum",
)

# These are measured Phase 1 checks and their corresponding failure categories.
TECHNICAL_FAILURES = {
    **{check: {category} for check, category in PROMPT_POLICY_CHECK_FAILURES.items()},
    "schema_compliance": {"INVALID_OUTPUT_SCHEMA"},
    "evidence_identity": {"EVIDENCE_IDENTITY_MISMATCH"},
    "immutable_provenance": {"STALE_OR_MISMATCHED_PROVENANCE"},
    "authority_policy": {"PROMPT_INJECTION_POLICY_VIOLATION"},
    "prompt_injection_policy": {"PROMPT_INJECTION_POLICY_VIOLATION"},
    "language_compliance": {"UNSUPPORTED_LANGUAGE"},
    "audio_identity": {"AUDIO_IDENTITY_MISMATCH"},
    "segment_timing": {"UNSUPPORTED_SEGMENT_OUTPUT", "INVALID_SEGMENT_TIMING"},
    "criterion_coverage": {"MISSING_OR_UNKNOWN_CRITERIA"},
    "score_bounds": {"OUT_OF_RANGE_OR_MISSING_SCORE"},
    "modality_compliance": {"UNSUPPORTED_MODEL_MODALITY"},
    "evidence_validity": {"INVALID_EVIDENCE_QUOTE_OR_SPAN"},
    "source_identity": {"CITATION_OR_SOURCE_VERSION_MISMATCH"},
    "source_authorization": {"UNAUTHORIZED_SOURCE_EXPOSURE"},
    "tenant_isolation": {"CROSS_TENANT_CONTAMINATION"},
    "citation_validity": {"CITATION_OR_SOURCE_VERSION_MISMATCH"},
    "citation_quote_support": {"UNSUPPORTED_CITATION_QUOTE"},
    "citation_labelled_support": {"REFERENCE_LABEL_REJECTS_CITATION"},
}
TechnicalCheck = Literal[
    "prompt_channel_isolation",
    "prompt_request_schema",
    "prompt_request_authority",
    "prompt_request_input_integrity",
    "schema_compliance",
    "evidence_identity",
    "immutable_provenance",
    "authority_policy",
    "prompt_injection_policy",
    "language_compliance",
    "audio_identity",
    "segment_timing",
    "criterion_coverage",
    "score_bounds",
    "modality_compliance",
    "evidence_validity",
    "source_identity",
    "source_authorization",
    "tenant_isolation",
    "citation_validity",
    "citation_quote_support",
    "citation_labelled_support",
]
RegistryAction = Literal["submit", "approve", "reject", "publish"]


class ArtifactPin(Contract):
    id: uuid.UUID
    checksum: str = Field(pattern=CHECKSUM_PATTERN)


class PromotionRequest(Contract):
    operation_id: uuid.UUID
    source_run: ArtifactPin
    source_result: ArtifactPin
    failed_check: TechnicalCheck | None = None


class SuiteVersionRequest(Contract):
    operation_id: uuid.UUID
    code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
    case_pins: list[ArtifactPin] = Field(min_length=1, max_length=100)
    predecessor: ArtifactPin | None = None


class RegistryTransitionRequest(Contract):
    expected_checksum: str = Field(pattern=CHECKSUM_PATTERN)
    review_reference: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$"
    )


@dataclass(frozen=True)
class _PromotionSource:
    version: EvaluationDatasetVersion
    organisation_id: uuid.UUID | None
    case: dict[str, Any]
    run: EvaluationRun
    result: EvaluationCaseResult
    maturity: str
    reference_pin: dict[str, str] | None
    rights: dict[str, Any]
    split_keys: set[str]


def cohort_fingerprint(case: dict[str, Any]) -> str:
    normalized = EvaluationCase.model_validate(case).model_dump(mode="json")
    return canonical_checksum({key: normalized[key] for key in ("kind", "input", "reference")})


def _owner(session: Session, version: EvaluationDatasetVersion) -> EvaluationDataset:
    owner = session.get(EvaluationDataset, version.dataset_id)
    if owner is None:
        raise foundation.EvaluationConflict("REGRESSION_DATASET_PROVENANCE_INVALID")
    return owner


def _scope_access(
    session: Session, context: TenantContext, organisation_id: uuid.UUID | None, kind: str
) -> None:
    if organisation_id is not None:
        scoped = build_tenant_context(session, context.user_id, organisation_id)
        permission = "speech_transcription.read" if kind == "TRANSCRIPTION" else "subjective.review"
        require_active_tenant_permission(session, scoped, permission, organisation_id)


def _case_in_run(version: EvaluationDatasetVersion, result: EvaluationCaseResult) -> dict[str, Any]:
    matched = [case for case in version.cases if case["case_id"] == result.case_id]
    if len(matched) != 1:
        raise foundation.EvaluationConflict("REGRESSION_SOURCE_CASE_UNAVAILABLE")
    return matched[0]


def _is_published(version: EvaluationDatasetVersion) -> bool:
    return (
        version.status == "PUBLISHED"
        and version.approved_by is not None
        and version.approved_by != version.created_by
        and version.approved_at is not None
        and bool(version.provenance_review_reference)
        and version.published_by is not None
        and version.published_at is not None
    )


def measured_failure_keys(result: dict[str, Any]) -> list[str]:
    """Return only named failures backed by an actual nonzero measured denominator."""
    checks = result.get("checks", {})
    metrics = result.get("metrics", {})
    categories = set(result.get("failure_categories", []))
    failures = []
    for name, expected_categories in TECHNICAL_FAILURES.items():
        metric = metrics.get(name + "_rate", {})
        numerator, denominator = metric.get("numerator"), metric.get("denominator")
        if (
            checks.get(name) == "FAIL"
            and categories.intersection(expected_categories)
            and type(numerator) is int
            and type(denominator) is int
            and 0 <= numerator < denominator
        ):
            failures.append(name)
    return sorted(failures)


def _contains_candidate_binding(case: dict[str, Any]) -> bool:
    """A synthetic marker may name a fixture, but cannot grant candidate data use."""
    for value in (case["input"], case["reference"]):
        identity = value["evidence_identity"]
        for key in (
            "candidate_user_id",
            "candidate_response_id",
            "candidate_response_revision_id",
            "response_revision_id",
            "candidate_media_asset_id",
            "attempt_id",
            "recording_session_id",
        ):
            if key in identity and not identity[key].casefold().startswith(
                ("synthetic-", "synthetic:")
            ):
                return True
    return False


def _source(
    session: Session, context: TenantContext | None, payload: PromotionRequest
) -> _PromotionSource:
    run = session.get(EvaluationRun, payload.source_run.id)
    failed_check = payload.failed_check
    if (
        run is None
        or run.status != "COMPLETED"
        or run.completed_at is None
        or run.checksum != payload.source_run.checksum
    ):
        error = (
            "REGRESSION_COMPLETED_FAILED_RUN_REQUIRED"
            if failed_check is not None
            else "REGRESSION_COMPLETED_REFERENCE_RUN_REQUIRED"
        )
        raise foundation.EvaluationConflict(error)
    version = foundation._version(session, run.dataset_version_id)
    owner = _owner(session, version)
    if context is not None:
        _scope_access(session, context, owner.organisation_id, version.skill)
    if not _is_published(version) or version.checksum != run.dataset_checksum:
        raise foundation.EvaluationConflict("REGRESSION_SOURCE_DATASET_PIN_MISMATCH")
    adapter = run.configuration.get("execution_adapter")
    origin = version.provenance.get("origin")
    if (origin, adapter) not in {
        ("ORIGINAL_SYNTHETIC", "SYNTHETIC_OBSERVATION_REPLAY_V1"),
        ("ORIGINAL_SYNTHETIC", PROMPT_POLICY_ADAPTER),
        ("GOVERNED_REFERENCE", "PINNED_EXECUTION_REPLAY_V1"),
    }:
        raise AuthorizationError("REGRESSION_APPROVED_SOURCE_PROCESSING_REQUIRED")
    if adapter == PROMPT_POLICY_ADAPTER and run.configuration.get("change_candidate") is not None:
        raise AuthorizationError("REGRESSION_PROMPT_POLICY_BASELINE_REQUIRED")
    if failed_check is not None and run.hard_gate != "FAIL":
        raise foundation.EvaluationConflict("REGRESSION_COMPLETED_FAILED_RUN_REQUIRED")
    if failed_check is None and (
        origin != "GOVERNED_REFERENCE"
        or version.evidence_class != "EXPERT_LABELLED"
        or run.hard_gate != "PASS"
    ):
        raise ValueError("REGRESSION_APPROVED_REFERENCE_OBSERVATION_REQUIRED")
    results = foundation.verify_run(session, run)
    result = next((row for row in results if row.id == payload.source_result.id), None)
    if result is None or result.checksum != payload.source_result.checksum:
        raise foundation.EvaluationConflict("REGRESSION_SOURCE_RESULT_PIN_MISMATCH")
    if failed_check is not None and (
        result.result.get("hard_fail") is not True
        or failed_check not in measured_failure_keys(result.result)
    ):
        raise ValueError("REGRESSION_MEASURED_TECHNICAL_FAILURE_REQUIRED")
    if failed_check is None and (
        result.result.get("hard_fail") is not False
        or result.result.get("checks", {}).get("schema_compliance") != "PASS"
    ):
        raise ValueError("REGRESSION_APPROVED_REFERENCE_OBSERVATION_REQUIRED")
    case = _case_in_run(version, result)
    parsed = EvaluationCase.model_validate(case)
    if parsed.split != run.configuration.get("dataset_split") or parsed.kind != version.skill:
        raise foundation.EvaluationConflict("REGRESSION_SOURCE_CASE_SCOPE_MISMATCH")
    reference_pin = None
    keys = case_split_keys(case)
    if origin == "ORIGINAL_SYNTHETIC":
        if (
            run.execution_class != "TEST_ONLY"
            or version.evidence_class != "TECHNICAL_TEST"
            or version.provenance.get("source_legal_status") != "OWNED_ORIGINAL"
            or version.provenance.get("retention_classification") != "SYNTHETIC_TEST"
            or parsed.reference.label_kind != "SYNTHETIC_EXPECTATION"
            or parsed.execution_job_id is not None
            or (parsed.synthetic_observation is None) != (adapter == PROMPT_POLICY_ADAPTER)
            or _contains_candidate_binding(parsed.model_dump(mode="json"))
        ):
            raise AuthorizationError("REGRESSION_APPROVED_SOURCE_PROCESSING_REQUIRED")
        observation = parsed.synthetic_observation
        receipt = (
            result.execution_reference  # Foundation verified the actual native request measurement.
            if adapter == PROMPT_POLICY_ADAPTER
            else {"execution_class": "TEST_ONLY", "model_execution": "NOT_RUN", "adapter": adapter}
        )
        maturity = "SYNTHETIC_TEST"
        rights = {
            key: version.provenance.get(key)
            for key in ("source_reference", "source_legal_status", "retention_classification")
        }
    else:
        from app.evaluation.expert_comparison import _population

        # An approved reference and its credentials are immutable. Reading them
        # without row locks lets overlapping suites verify in any case order.
        population = _population(session, version, context, lock_evidence=False)
        reference = population.source
        reference_pin = {key: str(version.provenance[key]) for key in REFERENCE_KEYS}
        keys.update(
            {"content:" + reference.content_checksum, "lineage:" + reference.lineage_checksum}
        )
        maturity = version.evidence_class
        rights = {
            key: reference.body.get(key)
            for key in (
                "origin",
                "source_reference",
                "rights_approval_reference",
                "processing_approval_reference",
                "retention_classification",
            )
        }
        rights["approval_reference"] = reference.approval_reference
        case = parsed.model_dump(mode="json")
        observation = None
        receipt = result.execution_reference
        if context is not None:
            case, observation, receipt, execution_class = load_pinned_observation(
                session, context, version, case, run.configuration
            )
            if execution_class != run.execution_class:
                raise foundation.EvaluationConflict("REGRESSION_SOURCE_EXECUTION_CLASS_MISMATCH")
    if canonical_checksum(case) != result.input_checksum or receipt != result.execution_reference:
        raise foundation.EvaluationConflict("REGRESSION_SOURCE_RECEIPT_MISMATCH")
    if observation is not None and (
        canonical_checksum(observation) != result.observation_checksum
        or evaluate_case(
            case,
            observation,
            evidence_class=version.evidence_class,
            metric_configuration=run.configuration["metric_configuration"],
        )
        != result.result
    ):
        raise foundation.EvaluationConflict("REGRESSION_SOURCE_MEASUREMENT_MISMATCH")
    return _PromotionSource(
        version, owner.organisation_id, case, run, result, maturity, reference_pin, rights, keys
    )


def _required_checks(source: _PromotionSource, failed_check: str | None) -> list[str]:
    if source.run.configuration.get("execution_adapter") == PROMPT_POLICY_ADAPTER:
        return sorted(PROMPT_POLICY_CHECK_FAILURES)
    case = EvaluationCase.model_validate(source.case)
    checks = {"schema_compliance", "evidence_identity", "immutable_provenance", "authority_policy"}
    if case.input.adversarial:
        checks.add("prompt_injection_policy")
    if case.kind == "TRANSCRIPTION":
        checks.update({"language_compliance", "audio_identity"})
        if case.reference.segments_supported:
            checks.add("segment_timing")
    if case.kind in {"WRITING", "SPEAKING"}:
        checks.update(
            {"criterion_coverage", "score_bounds", "modality_compliance", "evidence_validity"}
        )
        if case.kind == "SPEAKING":
            checks.add("audio_identity")
    if case.kind == "RETRIEVAL":
        checks.update(
            key
            for key, status in source.result.result["checks"].items()
            if key in TECHNICAL_FAILURES and status in {"PASS", "FAIL"}
        )
    if failed_check is not None:
        checks.add(failed_check)
    return sorted(checks)


def _snapshot(case: dict[str, Any], case_id: str) -> dict[str, Any]:
    normalized = EvaluationCase.model_validate(case).model_dump(
        mode="json", exclude={"synthetic_observation", "execution_job_id"}
    )
    normalized["case_id"] = case_id
    return normalized


def _case_metadata(source: _PromotionSource, payload: PromotionRequest) -> dict[str, Any]:
    failed = payload.failed_check is not None
    return {
        "origin": CASE_ORIGIN,
        "schema_version": "1",
        "purpose": PURPOSE,
        "source_basis": "MEASURED_TECHNICAL_FAILURE"
        if failed
        else "APPROVED_REFERENCE_OBSERVATION",
        "operation_id": str(payload.operation_id),
        "request_checksum": canonical_checksum(payload.model_dump(mode="json")),
        "source": {
            "run_id": str(source.run.id),
            "run_checksum": source.run.checksum,
            "result_id": str(source.result.id),
            "result_checksum": source.result.checksum,
            "dataset_version_id": str(source.version.id),
            "dataset_checksum": source.version.checksum,
            "case_id": source.result.case_id,
            "case_checksum": source.result.input_checksum,
            "observation_checksum": source.result.observation_checksum,
            "execution_class": source.run.execution_class,
        },
        "split": source.case["split"],
        "source_scope": {key: source.run.configuration.get(key) for key in SOURCE_KEYS},
        "evidence_maturity": source.maturity,
        "reference_pin": source.reference_pin,
        "source_rights": source.rights,
        "cohort_fingerprint": cohort_fingerprint(source.case),
        "split_keys": sorted(source.split_keys),
        "expectation": {
            "rule": "MEASURED_TECHNICAL_INVARIANT_V1" if failed else "ADJUDICATED_REFERENCE_V1",
            "failed_check": payload.failed_check,
        },
        "required_checks": _required_checks(source, payload.failed_check),
        **{key: source.run.configuration[key] for key in METHOD_KEYS},
    }


def _operation_version(
    session: Session,
    context: TenantContext,
    operation_id: uuid.UUID,
    origin: str,
    request_checksum: str,
) -> EvaluationDatasetVersion | None:
    _operation(session, operation_id)
    previous = session.scalar(
        select(EvaluationDatasetVersion).where(
            EvaluationDatasetVersion.provenance["operation_id"].as_string() == str(operation_id)
        )
    )
    if previous is not None and (
        previous.created_by != context.user_id
        or previous.provenance.get("origin") != origin
        or previous.provenance.get("request_checksum") != request_checksum
    ):
        raise foundation.EvaluationConflict("REGRESSION_OPERATION_CONFLICT")
    return previous


def _new_dataset(
    session: Session, context: TenantContext, code: str, organisation_id: uuid.UUID | None
) -> EvaluationDataset:
    if (
        session.scalar(select(EvaluationDataset.id).where(EvaluationDataset.code == code))
        is not None
    ):
        raise foundation.EvaluationConflict("REGRESSION_DATASET_CODE_CONFLICT")
    owner = EvaluationDataset(
        code=code,
        organisation_id=organisation_id,
        created_by=context.user_id,
        created_at=foundation.utcnow(),
    )
    session.add(owner)
    session.flush()
    return owner


def _new_version(
    session: Session,
    context: TenantContext,
    owner: EvaluationDataset,
    scope: EvaluationDatasetVersion,
    version_number: int,
    provenance: dict[str, Any],
    cases: list[dict[str, Any]],
) -> EvaluationDatasetVersion:
    validate_case_collection(cases)
    row = EvaluationDatasetVersion(
        dataset_id=owner.id,
        version_number=version_number,
        status="DRAFT",
        assessment_family=scope.assessment_family,
        language=scope.language,
        cefr_level=scope.cefr_level,
        skill=scope.skill,
        provenance=provenance,
        cases=cases,
        case_count=len(cases),
        evidence_class=provenance["evidence_maturity"],
        checksum=canonical_checksum({"provenance": provenance, "cases": cases}),
        created_by=context.user_id,
        created_at=foundation.utcnow(),
    )
    session.add(row)
    session.flush()
    foundation._audit(session, context, "regression_version_created", row)
    return row


def promote_case(
    session: Session, context: TenantContext, payload: PromotionRequest
) -> EvaluationDatasetVersion:
    trusted = foundation.require(session, context, foundation.RUN, foundation.MANAGE)
    request_checksum = canonical_checksum(payload.model_dump(mode="json"))
    previous = _operation_version(
        session, trusted, payload.operation_id, CASE_ORIGIN, request_checksum
    )
    if previous is not None:
        _verify_case(session, previous, trusted)
        return previous
    lock_splits(session)
    source = _source(session, trusted, payload)
    provenance = _case_metadata(source, payload)
    guard_regression(session, source.split_keys, provenance["split"])
    stable_key = canonical_checksum(
        {
            "organisation_id": str(source.organisation_id) if source.organisation_id else None,
            "assessment_family": source.version.assessment_family,
            "source_scope": provenance["source_scope"],
            "cohort_fingerprint": provenance["cohort_fingerprint"],
        }
    )
    code = "regression-case-" + stable_key
    if (
        session.scalar(select(EvaluationDataset.id).where(EvaluationDataset.code == code))
        is not None
    ):
        raise foundation.EvaluationConflict("REGRESSION_FAILURE_ALREADY_PROMOTED")
    owner = _new_dataset(session, trusted, code, source.organisation_id)
    return _new_version(
        session, trusted, owner, source.version, 1, provenance, [_snapshot(source.case, code)]
    )


def _verify_case(
    session: Session, version: EvaluationDatasetVersion, context: TenantContext | None = None
) -> None:
    version = foundation._version(session, version.id)
    provenance = version.provenance
    if (
        provenance.get("origin") != CASE_ORIGIN
        or len(version.cases) != 1
        or version.version_number != 1
    ):
        raise foundation.EvaluationConflict("REGRESSION_CASE_PROVENANCE_INVALID")
    source_pin = provenance["source"]
    payload = PromotionRequest.model_validate(
        {
            "operation_id": provenance["operation_id"],
            "source_run": {"id": source_pin["run_id"], "checksum": source_pin["run_checksum"]},
            "source_result": {
                "id": source_pin["result_id"],
                "checksum": source_pin["result_checksum"],
            },
            "failed_check": provenance["expectation"]["failed_check"],
        }
    )
    source = _source(session, context, payload)
    owner = _owner(session, version)
    if (
        provenance != _case_metadata(source, payload)
        or version.cases != [_snapshot(source.case, owner.code)]
        or owner.organisation_id != source.organisation_id
        or version.evidence_class != source.maturity
        or _scope(version) != _scope(source.version)
    ):
        raise foundation.EvaluationConflict("REGRESSION_CASE_PROVENANCE_INVALID")


def _scope(version: EvaluationDatasetVersion) -> tuple[str, str, str | None, str]:
    return version.assessment_family, version.language, version.cefr_level, version.skill


def _published_cases(
    session: Session, pins: list[ArtifactPin], context: TenantContext | None = None
) -> list[EvaluationDatasetVersion]:
    versions = []
    for pin in pins:
        case = foundation._version(session, pin.id)
        if case.checksum != pin.checksum or not _is_published(case):
            raise foundation.EvaluationConflict("REGRESSION_PUBLISHED_CASE_PIN_REQUIRED")
        _verify_case(session, case, context)
        versions.append(case)
    return versions


def _suite_metadata(session: Session, versions: list[EvaluationDatasetVersion]) -> dict[str, Any]:
    first = versions[0]
    shared_keys = ("split", "source_scope", "evidence_maturity", *METHOD_KEYS)
    owner = _owner(session, first)
    if any(
        _scope(case) != _scope(first)
        or _owner(session, case).organisation_id != owner.organisation_id
        or any(case.provenance.get(key) != first.provenance.get(key) for key in shared_keys)
        for case in versions
    ):
        raise foundation.EvaluationConflict("REGRESSION_SUITE_CASE_SCOPE_MISMATCH")
    if len({case.provenance["cohort_fingerprint"] for case in versions}) != len(versions):
        raise foundation.EvaluationConflict("REGRESSION_SUITE_DUPLICATE_CASE")
    seen_evidence: set[str] = set()
    for case in versions:
        keys = set(case.provenance["split_keys"])
        if seen_evidence.intersection(keys):
            raise foundation.EvaluationConflict("REGRESSION_SUITE_DUPLICATE_LINEAGE")
        seen_evidence.update(keys)
    return {
        "origin": SUITE_ORIGIN,
        "schema_version": "1",
        "purpose": PURPOSE,
        "case_pins": [
            {
                "case_id": version.cases[0]["case_id"],
                "case_version_id": str(version.id),
                "checksum": version.checksum,
                "required_checks": version.provenance["required_checks"],
                "cohort_fingerprint": version.provenance["cohort_fingerprint"],
                "reference_pin": version.provenance["reference_pin"],
            }
            for version in versions
        ],
        **{key: first.provenance[key] for key in shared_keys},
    }


def _suite_request_checksum(payload: SuiteVersionRequest, dataset_id: uuid.UUID | None) -> str:
    return canonical_checksum(
        {
            "request": payload.model_dump(mode="json"),
            "dataset_id": str(dataset_id) if dataset_id is not None else None,
        }
    )


def create_suite(
    session: Session,
    context: TenantContext,
    payload: SuiteVersionRequest,
    dataset_id: uuid.UUID | None = None,
) -> EvaluationDatasetVersion:
    trusted = foundation.require(session, context, foundation.RUN, foundation.MANAGE)
    if bool(dataset_id) != bool(payload.predecessor):
        raise ValueError("REGRESSION_SUITE_PREDECESSOR_REQUIRED")
    request_checksum = _suite_request_checksum(payload, dataset_id)
    previous = _operation_version(
        session, trusted, payload.operation_id, SUITE_ORIGIN, request_checksum
    )
    if previous is not None:
        _verify_suite(session, previous, trusted)
        return previous
    lock_splits(session)
    cases = _published_cases(session, payload.case_pins, trusted)
    case_owner = _owner(session, cases[0])
    provenance = _suite_metadata(session, cases)
    version_number = 1
    if dataset_id is None:
        owner = _new_dataset(session, trusted, payload.code, case_owner.organisation_id)
    else:
        existing = session.scalar(
            select(EvaluationDataset).where(EvaluationDataset.id == dataset_id).with_for_update()
        )
        if (
            existing is None
            or existing.code != payload.code
            or existing.organisation_id != case_owner.organisation_id
        ):
            raise foundation.EvaluationConflict("REGRESSION_SUITE_DATASET_SCOPE_MISMATCH")
        owner = existing
        latest = session.scalar(
            select(EvaluationDatasetVersion)
            .where(EvaluationDatasetVersion.dataset_id == owner.id)
            .order_by(EvaluationDatasetVersion.version_number.desc())
            .limit(1)
        )
        assert payload.predecessor is not None
        if (
            latest is None
            or latest.provenance.get("origin") != SUITE_ORIGIN
            or latest.id != payload.predecessor.id
            or latest.checksum != payload.predecessor.checksum
            or latest.status not in {"PUBLISHED", "REJECTED"}
            or _scope(latest) != _scope(cases[0])
            or latest.provenance.get("split") != provenance["split"]
        ):
            raise foundation.EvaluationConflict("REGRESSION_SUITE_PREDECESSOR_CONFLICT")
        _verify_suite(session, latest)
        version_number = latest.version_number + 1
    provenance.update(
        {
            "operation_id": str(payload.operation_id),
            "request_checksum": request_checksum,
            "predecessor": payload.predecessor.model_dump(mode="json")
            if payload.predecessor
            else None,
        }
    )
    return _new_version(
        session,
        trusted,
        owner,
        cases[0],
        version_number,
        provenance,
        [case.cases[0] for case in cases],
    )


def _verify_suite(
    session: Session, version: EvaluationDatasetVersion, context: TenantContext | None = None
) -> list[EvaluationDatasetVersion]:
    version = foundation._version(session, version.id)
    provenance = version.provenance
    if provenance.get("origin") != SUITE_ORIGIN:
        raise foundation.EvaluationConflict("REGRESSION_SUITE_PROVENANCE_INVALID")
    owner = _owner(session, version)
    pins = [
        ArtifactPin(id=pin["case_version_id"], checksum=pin["checksum"])
        for pin in provenance["case_pins"]
    ]
    if not 1 <= len(pins) <= 100:
        raise foundation.EvaluationConflict("REGRESSION_SUITE_PROVENANCE_INVALID")
    versions = _published_cases(session, pins, context)
    metadata = _suite_metadata(session, versions)
    raw_predecessor = provenance.get("predecessor")
    predecessor = ArtifactPin.model_validate(raw_predecessor) if raw_predecessor else None
    if predecessor:
        prior = foundation._version(session, predecessor.id)
        if (
            prior.checksum != predecessor.checksum
            or prior.dataset_id != owner.id
            or prior.version_number != version.version_number - 1
            or prior.provenance.get("origin") != SUITE_ORIGIN
            or prior.status not in {"PUBLISHED", "REJECTED"}
        ):
            raise foundation.EvaluationConflict("REGRESSION_SUITE_PREDECESSOR_CONFLICT")
    elif version.version_number != 1:
        raise foundation.EvaluationConflict("REGRESSION_SUITE_PREDECESSOR_CONFLICT")
    payload = SuiteVersionRequest(
        operation_id=provenance["operation_id"],
        code=owner.code,
        case_pins=pins,
        predecessor=predecessor,
    )
    if (
        any(provenance.get(key) != value for key, value in metadata.items())
        or provenance.get("request_checksum")
        != _suite_request_checksum(payload, owner.id if predecessor else None)
        or version.cases != [case.cases[0] for case in versions]
        or _scope(version) != _scope(versions[0])
        or owner.organisation_id != _owner(session, versions[0]).organisation_id
        or version.evidence_class != metadata["evidence_maturity"]
    ):
        raise foundation.EvaluationConflict("REGRESSION_SUITE_PROVENANCE_INVALID")
    return versions


def verify_regression_suite(session: Session, version: EvaluationDatasetVersion) -> None:
    if not _is_published(version):
        raise foundation.EvaluationConflict("REGRESSION_PUBLISHED_SUITE_REQUIRED")
    _verify_suite(session, version)


def resolve_regression_suite(
    session: Session,
    context: TenantContext,
    version_id: uuid.UUID,
    checksum: str,
    *,
    for_execution: bool = True,
) -> EvaluationDatasetVersion:
    permissions = (
        (foundation.RUN, foundation.MANAGE)
        if for_execution
        else (foundation.READ, foundation.MANAGE, foundation.REVIEW)
    )
    trusted = foundation.require(session, context, *permissions)
    version = foundation._version(session, version_id)
    if version.checksum != checksum or not _is_published(version):
        raise foundation.EvaluationConflict("REGRESSION_PUBLISHED_SUITE_PIN_REQUIRED")
    cases = _verify_suite(session, version, trusted)
    if for_execution and trusted.user_id in _registry_actors(version, cases):
        raise AuthorizationError("REGRESSION_INDEPENDENT_EXECUTOR_REQUIRED")
    return version


def regression_review_excluded_actor_ids(
    session: Session, version: EvaluationDatasetVersion
) -> set[uuid.UUID]:
    return _registry_actors(version, _verify_suite(session, version))


def _registry_actors(
    version: EvaluationDatasetVersion, cases: list[EvaluationDatasetVersion]
) -> set[uuid.UUID]:
    return {
        actor
        for row in [version, *cases]
        for actor in (row.created_by, row.approved_by, row.published_by)
        if actor is not None
    }


def regression_reference_sources(
    session: Session, version: EvaluationDatasetVersion
) -> list[EvaluationReference]:
    """Resolve the exact governed reference records for authorized policy services."""
    if not _is_published(version) or version.evidence_class != "EXPERT_LABELLED":
        raise foundation.EvaluationConflict("REGRESSION_GENUINE_REFERENCE_SUITE_REQUIRED")
    cases = _verify_suite(session, version)
    sources = []
    for case in cases:
        pin = case.provenance["reference_pin"]
        if pin is None:
            raise foundation.EvaluationConflict("REGRESSION_GENUINE_REFERENCE_SUITE_REQUIRED")
        reference = session.get(EvaluationReference, uuid.UUID(pin["reference_id"]))
        if (
            reference is None
            or reference.evidence_class != "APPROVED_REFERENCE"
            or reference.checksum != pin["reference_checksum"]
        ):
            raise foundation.EvaluationConflict("REGRESSION_GENUINE_REFERENCE_SUITE_REQUIRED")
        sources.append(reference)
    return sources


def _transition_replay(
    row: EvaluationDatasetVersion, actor: uuid.UUID, action: RegistryAction, reference: str | None
) -> bool:
    if action == "submit":
        return row.submitted_at is not None and row.created_by == actor
    if action == "approve":
        return (
            row.approved_at is not None
            and row.approved_by == actor
            and row.provenance_review_reference == reference
        )
    if action == "reject":
        return (
            row.status == "REJECTED"
            and row.rejected_by == actor
            and row.rejection_review_reference == reference
        )
    return row.status == "PUBLISHED" and row.published_by == actor


def transition_regression_version(
    session: Session,
    context: TenantContext,
    version_id: uuid.UUID,
    action: RegistryAction,
    payload: RegistryTransitionRequest,
) -> EvaluationDatasetVersion:
    reviewing = action in {"approve", "reject"}
    permissions = (
        (foundation.READ, foundation.REVIEW, foundation.MANAGE)
        if reviewing
        else (foundation.RUN, foundation.MANAGE)
    )
    trusted = foundation.require(session, context, *permissions)
    row = foundation._version(session, version_id, lock=True)
    origin = row.provenance.get("origin")
    if origin not in REGRESSION_ORIGINS:
        raise ValueError("REGRESSION_VERSION_REQUIRED")
    if row.checksum != payload.expected_checksum:
        raise foundation.EvaluationConflict("REGRESSION_VERSION_CHECKSUM_MISMATCH")
    if reviewing != bool(payload.review_reference):
        raise ValueError("REGRESSION_REVIEW_REFERENCE_REQUIRED_FOR_DECISION")
    excluded_reviewers = {row.created_by}
    if origin == CASE_ORIGIN:
        _verify_case(session, row, trusted)
    else:
        excluded_reviewers.update(case.created_by for case in _verify_suite(session, row, trusted))
    if _transition_replay(row, trusted.user_id, action, payload.review_reference):
        return row
    expected, target = {
        "submit": ("DRAFT", "REVIEW_REQUIRED"),
        "approve": ("REVIEW_REQUIRED", "APPROVED"),
        "reject": ("REVIEW_REQUIRED", "REJECTED"),
        "publish": ("APPROVED", "PUBLISHED"),
    }[action]
    if row.status != expected:
        raise foundation.EvaluationConflict("REGRESSION_VERSION_STATE_CONFLICT")
    if reviewing and trusted.user_id in excluded_reviewers:
        raise AuthorizationError("REGRESSION_INDEPENDENT_REVIEW_REQUIRED")
    if action == "submit":
        if row.created_by != trusted.user_id:
            raise AuthorizationError("REGRESSION_AUTHOR_SUBMISSION_REQUIRED")
        row.submitted_at = foundation.utcnow()
    elif action == "approve":
        row.approved_at, row.approved_by = foundation.utcnow(), trusted.user_id
        row.provenance_review_reference = payload.review_reference
    elif action == "reject":
        row.rejected_at, row.rejected_by = foundation.utcnow(), trusted.user_id
        row.rejection_review_reference = payload.review_reference
    else:
        if row.approved_by is None or row.approved_by == row.created_by:
            raise foundation.EvaluationConflict("REGRESSION_INDEPENDENT_REVIEW_REQUIRED")
        row.published_at, row.published_by = foundation.utcnow(), trusted.user_id
    row.status = target
    session.flush()
    foundation._audit(session, trusted, "regression_version_" + action, row)
    return row


def regression_summary(
    session: Session, version: EvaluationDatasetVersion, context: TenantContext | None = None
) -> dict[str, Any]:
    provenance = version.provenance
    origin = provenance.get("origin")
    if origin not in REGRESSION_ORIGINS:
        raise ValueError("REGRESSION_VERSION_REQUIRED")
    owner = _owner(session, version)
    actions = []
    if context is not None:
        trusted = build_tenant_context(session, context.user_id)
        try:
            _scope_access(session, trusted, owner.organisation_id, version.skill)
        except AuthorizationError:
            pass
        else:
            permissions = trusted.platform_permissions
            if {foundation.RUN, foundation.MANAGE} <= permissions:
                if version.status == "DRAFT" and version.created_by == trusted.user_id:
                    actions.append("submit")
                if version.status == "APPROVED":
                    actions.append("publish")
            if (
                {foundation.READ, foundation.REVIEW, foundation.MANAGE} <= permissions
                and version.status == "REVIEW_REQUIRED"
                and version.created_by != trusted.user_id
            ):
                case_authors = {
                    case.created_by
                    for case in session.scalars(
                        select(EvaluationDatasetVersion).where(
                            EvaluationDatasetVersion.id.in_(
                                [
                                    uuid.UUID(pin["case_version_id"])
                                    for pin in provenance.get("case_pins", [])
                                ]
                            )
                        )
                    )
                }
                if trusted.user_id not in case_authors:
                    actions.extend(("approve", "reject"))
    source = provenance.get("source", {})
    return {
        **foundation.version_summary(session, version),
        "origin": origin,
        "organisation_id": str(owner.organisation_id) if owner.organisation_id else None,
        "kind": version.skill,
        "purpose": provenance["purpose"],
        "split": provenance["split"],
        "evidence_maturity": version.evidence_class,
        "source_basis": provenance.get("source_basis"),
        "required_checks": provenance.get("required_checks", []),
        "case_pins": [
            {key: pin[key] for key in ("case_id", "case_version_id", "checksum", "required_checks")}
            for pin in provenance.get("case_pins", [])
        ],
        "source": {
            key: source[key]
            for key in ("run_id", "run_checksum", "result_id", "result_checksum", "case_id")
        }
        if source
        else None,
        "allowed_actions": actions,
        "created_by": str(version.created_by),
        "approved_by": str(version.approved_by) if version.approved_by else None,
        "published_by": str(version.published_by) if version.published_by else None,
        "rejected_by": str(version.rejected_by) if version.rejected_by else None,
        "rejected_at": version.rejected_at.isoformat() if version.rejected_at else None,
        "production_activation": False,
    }
