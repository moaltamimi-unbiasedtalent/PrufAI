"""Compare sealed evaluation evidence without executing a provider."""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.authorization import TenantContext, build_tenant_context
from app.authorization.membership import require_active_tenant_permission
from app.evaluation import service as foundation
from app.evaluation.contracts import Contract, EvaluationCase
from app.evaluation.regression_registry import ArtifactPin
from app.evaluation.replay import SOURCE_KEYS, load_pinned_observation
from app.models import (
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationRun,
)
from app.models.evaluation import REGRESSION_ADAPTER as ADAPTER
from app.models.writing_assessment import RubricVersion
from app.writing_assessment.service import RubricService, canonical_checksum

METHOD_VERSION = "governed-regression-v1"
SYNTHETIC_ADAPTER = "SYNTHETIC_OBSERVATION_REPLAY_V1"
REPLAY_ADAPTER = "PINNED_EXECUTION_REPLAY_V1"
SOURCE_ADAPTERS = {SYNTHETIC_ADAPTER, REPLAY_ADAPTER}
ARTIFACT_KEYS = (
    *SOURCE_KEYS,
    "model_profile_id",
    "model_version",
    "prompt_version_id",
    "agent_version_id",
    "workflow_version_id",
    "profile_version",
)
SYNTHETIC_CLASSES = {"TECHNICAL_TEST", "SYNTHETIC_TEST", "SYNTHETIC_REGRESSION"}
DIMENSION_KEYS = {
    "MODEL": ("model_profile_id", "model_version"),
    "PROMPT": ("prompt_version_id",),
    "RUBRIC": ("rubric_version_id", "rubric_checksum"),
    "WORKFLOW": ("workflow_version_id",),
    "TRANSCRIPTION": ("model_profile_id", "profile_version"),
}
LIMITATIONS = [
    "Technical checks measure the pinned observations and preserved receipts.",
    "Paired differences describe the selected suite. "
    "They establish neither causality nor assessment validity.",
    "Synthetic fixtures and TEST_ONLY executions cannot establish empirical quality.",
    "A gate result supplies evidence for an independent human decision. "
    "Production activation is disabled.",
]


class RegressionComparisonInput(Contract):
    operation_id: uuid.UUID
    suite_version: ArtifactPin
    baseline_run: ArtifactPin
    candidate_run: ArtifactPin
    baseline_additional_runs: list[ArtifactPin] = Field(default_factory=list, max_length=99)
    candidate_additional_runs: list[ArtifactPin] = Field(default_factory=list, max_length=99)
    dimension: Literal[
        "TECHNICAL_FIXTURE",
        "MODEL",
        "PROMPT",
        "RUBRIC",
        "WORKFLOW",
        "RETRIEVAL",
        "TRANSCRIPTION",
        "EXECUTION_REPEAT",
    ]
    threshold_policy: ArtifactPin | None = None

    @model_validator(mode="after")
    def distinct_source_runs(self) -> RegressionComparisonInput:
        identifiers = [
            pin.id
            for pin in (
                self.baseline_run,
                *self.baseline_additional_runs,
                self.candidate_run,
                *self.candidate_additional_runs,
            )
        ]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("REGRESSION_DISTINCT_SOURCE_RUNS_REQUIRED")
        return self


@dataclass(frozen=True)
class _Side:
    run: EvaluationRun
    dataset: EvaluationDatasetVersion
    owner: EvaluationDataset
    cases: dict[str, dict[str, Any]]
    results: dict[str, EvaluationCaseResult]
    duplicate_fingerprints: bool


@dataclass(frozen=True)
class _CaseBinding:
    component: _Side
    case_id: str

    @property
    def result(self) -> EvaluationCaseResult | None:
        return self.component.results.get(self.case_id)


@dataclass(frozen=True)
class _RunGroup:
    components: tuple[_Side, ...]
    bindings: dict[str, _CaseBinding]

    @property
    def primary(self) -> _Side:
        return self.components[0]


def _pin(row: Any) -> dict[str, str]:
    return {"id": str(row.id), "checksum": row.checksum}


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and abs(number) <= Decimal("1e30") else None


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "status": "NOT_EVALUATED",
        "numerator": None,
        "denominator": None,
        "reason_code": reason,
    }


def measure_required_check(
    result: dict[str, Any] | None, code: str, case: dict[str, Any]
) -> dict[str, Any]:
    """Read an actual check with a positive denominator from a sealed case result."""
    if result is None:
        return _unavailable("MISSING_CASE_RESULT")
    finding = result.get("checks", {}).get(code)
    if (
        code == "segment_timing"
        and not case["reference"].get("segments_supported")
        and finding != "FAIL"
    ):
        return _unavailable("TIMING_MEASUREMENT_NOT_SUPPORTED")
    if code == "prompt_injection_policy" and not case["input"].get("adversarial"):
        return _unavailable("NO_ADVERSARIAL_CASE")
    metric = result.get("metrics", {}).get(code + "_rate")
    if finding not in ("PASS", "FAIL") or not isinstance(metric, dict):
        return _unavailable("REQUIRED_CHECK_UNAVAILABLE")
    numerator = _decimal(metric.get("numerator"))
    denominator = _decimal(metric.get("denominator"))
    value = _decimal(metric.get("value"))
    if (
        numerator is None
        or denominator is None
        or denominator <= 0
        or not 0 <= numerator <= denominator
        or numerator != numerator.to_integral_value()
        or denominator != denominator.to_integral_value()
        or metric.get("missing_count") != 0
        or (finding == "PASS") != (numerator == denominator)
        or _decimal(metric.get("sample_size")) != denominator
        or value is None
        or float(value) != float(numerator / denominator)
    ):
        return _unavailable("INVALID_REQUIRED_CHECK_COUNTS")
    return {
        "status": finding,
        "numerator": int(numerator),
        "denominator": int(denominator),
        "reason_code": "REQUIRED_CHECK_FAILED" if finding == "FAIL" else None,
    }


def case_technical_status(
    result: dict[str, Any] | None, observations: list[dict[str, Any]], case: dict[str, Any]
) -> str:
    """A measured failure takes precedence over incomplete evidence."""
    if result is None:
        return "NOT_EVALUATED"
    # A source run can detect a hard failure beyond the suite's selected checks.
    # An evaluator must not waive that measured failure by omitting its key.
    observed_fail = any(
        measure_required_check(result, code, case)["status"] == "FAIL"
        for code, finding in result.get("checks", {}).items()
        if finding == "FAIL"
    )
    if observed_fail or any(item["status"] == "FAIL" for item in observations):
        return "FAIL"
    if (
        not observations
        or result.get("hard_fail") is not False
        or any(item["status"] != "PASS" for item in observations)
    ):
        return "NOT_EVALUATED"
    return "PASS"


def gate_decision(comparison: str, technical: str, empirical: str) -> str:
    if comparison == "NOT_COMPARABLE":
        return "NOT_COMPARABLE"
    if technical == "FAIL" or empirical == "FAIL":
        return "FAIL"
    if comparison != "COMPARABLE" or technical != "PASS" or empirical == "NOT_EVALUATED":
        return "NOT_EVALUATED"
    if empirical == "UNCONFIGURED":
        return "UNCONFIGURED"
    return "PASS" if empirical == "PASS" else "NOT_EVALUATED"


def _authorize_side(
    session: Session, context: TenantContext, owner: EvaluationDataset, skill: str
) -> None:
    if owner.organisation_id is None:
        return
    scoped = build_tenant_context(session, context.user_id, owner.organisation_id)
    permission = "speech_transcription.read" if skill == "TRANSCRIPTION" else "subjective.review"
    require_active_tenant_permission(session, scoped, permission, owner.organisation_id)


def _load_side(
    session: Session, pin: ArtifactPin, context: TenantContext | None, remaining_cases: int
) -> _Side:
    from app.evaluation.regression_registry import cohort_fingerprint

    run = session.get(EvaluationRun, pin.id)
    if run is None or run.checksum != pin.checksum:
        raise foundation.EvaluationConflict("REGRESSION_EXACT_SOURCE_RUN_REQUIRED")
    adapter = run.configuration.get("execution_adapter")
    if adapter not in SOURCE_ADAPTERS:
        raise ValueError("REGRESSION_PRIMARY_EXECUTION_RUN_REQUIRED")
    dataset = foundation._version(session, run.dataset_version_id)
    owner = session.get(EvaluationDataset, dataset.dataset_id)
    if owner is None or dataset.checksum != run.dataset_checksum or dataset.status != "PUBLISHED":
        raise foundation.EvaluationConflict("REGRESSION_SOURCE_DATASET_PIN_MISMATCH")
    if context is not None:
        _authorize_side(session, context, owner, dataset.skill)
    selected = {
        case["case_id"]: case
        for case in dataset.cases
        if case["split"] == run.configuration["dataset_split"]
    }
    if len(selected) > remaining_cases:
        raise ValueError("REGRESSION_GROUP_CASE_LIMIT_EXCEEDED")
    results = {row.case_id: row for row in foundation.verify_run(session, run)}
    if not results.keys() <= selected.keys():
        raise foundation.EvaluationConflict("REGRESSION_EXECUTION_CASE_BINDING_INVALID")
    for case_id, result in results.items():
        case = selected[case_id]
        receipt = result.execution_reference
        if adapter == SYNTHETIC_ADAPTER:
            observation = case.get("synthetic_observation")
            if (
                not isinstance(observation, dict)
                or case.get("execution_job_id") is not None
                or receipt
                != {
                    "execution_class": "TEST_ONLY",
                    "model_execution": "NOT_RUN",
                    "adapter": SYNTHETIC_ADAPTER,
                }
                or run.execution_class != "TEST_ONLY"
                or canonical_checksum(observation) != result.observation_checksum
            ):
                raise foundation.EvaluationConflict("REGRESSION_SYNTHETIC_RECEIPT_MISMATCH")
        elif context is not None:
            case, observation, verified, execution_class = load_pinned_observation(
                session, context, dataset, case, run.configuration
            )
            if (
                canonical_checksum(verified) != canonical_checksum(receipt)
                or canonical_checksum(observation) != result.observation_checksum
                or execution_class != run.execution_class
            ):
                raise foundation.EvaluationConflict("REGRESSION_EXECUTION_RECEIPT_MISMATCH")
        else:
            case = EvaluationCase.model_validate(case).model_dump(mode="json")
            if (
                receipt.get("execution_job_id") != case.get("execution_job_id")
                or receipt.get("execution_class") != run.execution_class
            ):
                raise foundation.EvaluationConflict("REGRESSION_EXECUTION_RECEIPT_MISMATCH")
        if (
            canonical_checksum(case) != result.input_checksum
            or result.result.get("case_id") != case_id
            or result.result.get("kind") != dataset.skill
            or result.result.get("method_version") != run.configuration["implementation_version"]
            or result.result.get("evidence_class") != run.evidence_class
        ):
            raise foundation.EvaluationConflict("REGRESSION_EXECUTION_CASE_BINDING_INVALID")
    cases = {cohort_fingerprint(case): case for case in selected.values()}
    return _Side(run, dataset, owner, cases, results, len(cases) != len(selected))


def _assemble_group(components: tuple[_Side, ...]) -> _RunGroup:
    primary = components[0]
    scope = ("assessment_family", "skill", "language", "cefr_level", "evidence_class")
    bindings: dict[str, _CaseBinding] = {}
    case_ids: set[str] = set()
    for component in components:
        if len(components) > 1:
            if (
                component.owner.organisation_id != primary.owner.organisation_id
                or any(
                    getattr(component.dataset, key) != getattr(primary.dataset, key)
                    for key in scope
                )
                or component.dataset.provenance.get("origin")
                != primary.dataset.provenance.get("origin")
                or component.run.configuration != primary.run.configuration
                or component.run.execution_class != primary.run.execution_class
                or component.run.evidence_class != primary.run.evidence_class
            ):
                raise foundation.EvaluationConflict(
                    "REGRESSION_GROUP_EXECUTION_CONFIGURATION_MISMATCH"
                )
            if (
                component.run.status != "COMPLETED"
                or component.run.completed_at is None
                or not component.cases
                or set(component.results) != {case["case_id"] for case in component.cases.values()}
            ):
                raise foundation.EvaluationConflict("REGRESSION_GROUP_COMPLETE_EXECUTION_REQUIRED")
            if component.duplicate_fingerprints:
                raise foundation.EvaluationConflict("REGRESSION_GROUP_COHORT_OVERLAP")
        for fingerprint, case in component.cases.items():
            case_id = case["case_id"]
            if fingerprint in bindings or case_id in case_ids:
                raise foundation.EvaluationConflict("REGRESSION_GROUP_COHORT_OVERLAP")
            bindings[fingerprint] = _CaseBinding(component, case_id)
            case_ids.add(case_id)
    if len(bindings) > 100:
        raise ValueError("REGRESSION_GROUP_CASE_LIMIT_EXCEEDED")
    return _RunGroup(components, bindings)


def _load_group(
    session: Session,
    primary: ArtifactPin,
    additional: list[ArtifactPin],
    context: TenantContext | None,
) -> _RunGroup:
    components = []
    remaining_cases = 100
    for pin in (primary, *additional):
        component = _load_side(session, pin, context, remaining_cases)
        if additional and component.duplicate_fingerprints:
            raise foundation.EvaluationConflict("REGRESSION_GROUP_COHORT_OVERLAP")
        components.append(component)
        remaining_cases -= len(component.cases)
    return _assemble_group(tuple(components))


def _rubric_fingerprint(session: Session, configuration: dict[str, Any]) -> str | None:
    identifier = configuration.get("rubric_version_id")
    if not identifier:
        return None
    rubric = session.get(RubricVersion, uuid.UUID(identifier))
    if rubric is None or rubric.checksum != configuration.get("rubric_checksum"):
        raise foundation.EvaluationConflict("REGRESSION_RUBRIC_PIN_MISMATCH")
    payload = RubricService.checksum_payload(rubric)
    if canonical_checksum(payload) != rubric.checksum:
        raise foundation.EvaluationConflict("REGRESSION_RUBRIC_PIN_MISMATCH")
    return canonical_checksum(
        {key: value for key, value in payload.items() if key != "ai_assessment_policy"}
    )


def _comparison_reason(
    session: Session,
    suite: EvaluationDatasetVersion,
    owner: EvaluationDataset,
    baseline: _RunGroup,
    candidate: _RunGroup,
    dimension: str,
) -> str | None:
    metadata = suite.provenance
    expected = {pin["cohort_fingerprint"] for pin in metadata["case_pins"]}
    scope_keys = ("assessment_family", "skill", "language", "cefr_level")
    metric_keys = (
        "evaluation_method",
        "evaluation_method_version",
        "implementation_version",
        "metric_configuration_checksum",
    )
    for group in (baseline, candidate):
        side = group.primary
        if side.owner.organisation_id != owner.organisation_id or any(
            getattr(side.dataset, key) != getattr(suite, key) for key in scope_keys
        ):
            return "ASSESSMENT_SCOPE_MISMATCH"
        configuration = side.run.configuration
        if configuration.get("dataset_split") != metadata["split"]:
            return "DATASET_SPLIT_MISMATCH"
        if any(configuration.get(key) != metadata[key] for key in metric_keys):
            return "METRIC_METHOD_OR_CONFIGURATION_MISMATCH"
        if side.duplicate_fingerprints or group.bindings.keys() != expected:
            return "REFERENCE_COHORT_MISMATCH"
        if any(
            configuration.get(key) != metadata["source_scope"].get(key)
            for key in SOURCE_KEYS
            if key not in {"rubric_version_id", "rubric_checksum"}
        ):
            return "SOURCE_CONFIGURATION_MISMATCH"
        expected_rubric = metadata["source_scope"]
        if any(
            configuration.get(key) != expected_rubric.get(key)
            for key in ("rubric_version_id", "rubric_checksum")
        ):
            if _rubric_fingerprint(session, configuration) != _rubric_fingerprint(
                session, expected_rubric
            ):
                return "RUBRIC_SCALE_OR_EVIDENCE_MISMATCH"
        for case_pin in metadata["case_pins"]:
            case_dataset = group.bindings[case_pin["cohort_fingerprint"]].component.dataset
            reference_pin = case_pin.get("reference_pin")
            if reference_pin is None:
                if case_dataset.evidence_class not in SYNTHETIC_CLASSES:
                    return "REFERENCE_MATURITY_MISMATCH"
            elif (
                case_dataset.evidence_class != metadata["evidence_maturity"]
                or case_dataset.provenance.get("origin") != "GOVERNED_REFERENCE"
                or any(
                    case_dataset.provenance.get(key) != value
                    for key, value in reference_pin.items()
                )
            ):
                return "GOVERNED_REFERENCE_PIN_MISMATCH"
    left, right = baseline.primary.run.configuration, candidate.primary.run.configuration
    if baseline.primary.run.execution_class != candidate.primary.run.execution_class or left.get(
        "execution_adapter"
    ) != right.get("execution_adapter"):
        return "EXECUTION_CLASS_OR_ADAPTER_MISMATCH"
    if dimension == "TECHNICAL_FIXTURE":
        return (
            None
            if left["execution_adapter"] == SYNTHETIC_ADAPTER
            else "SYNTHETIC_FIXTURE_ADAPTER_REQUIRED"
        )
    if dimension == "RETRIEVAL":
        return "RETRIEVAL_EXECUTION_RECEIPT_UNAVAILABLE"
    if dimension == "EXECUTION_REPEAT":
        return _repeat_reason(baseline, candidate)
    if dimension == "TRANSCRIPTION" and suite.skill != "TRANSCRIPTION":
        return "TRANSCRIPTION_SCOPE_REQUIRED"
    if dimension != "RUBRIC" and left["execution_adapter"] != REPLAY_ADAPTER:
        return "EXECUTION_ARTIFACT_RECEIPT_REQUIRED"
    keys = DIMENSION_KEYS[dimension]
    if any(not left.get(key) or not right.get(key) for key in keys):
        return "EXACT_COMPARISON_ARTIFACT_REQUIRED"
    if all(left[key] == right[key] for key in keys):
        return "DISTINCT_COMPARISON_ARTIFACT_REQUIRED"
    return None


def _repeat_reason(baseline: _RunGroup, candidate: _RunGroup) -> str | None:
    left, right = baseline.primary.run.configuration, candidate.primary.run.configuration
    if left.get("execution_adapter") != REPLAY_ADAPTER or left != right:
        return "IDENTICAL_PINNED_REPEAT_CONFIGURATION_REQUIRED"
    for fingerprint, binding in baseline.bindings.items():
        left_result = binding.result
        right_result = candidate.bindings[fingerprint].result
        if left_result is None or right_result is None:
            continue  # paired completeness is evaluated separately
        a, b = left_result.execution_reference, right_result.execution_reference
        a_job, b_job = a.get("execution_job_id"), b.get("execution_job_id")
        a_request, b_request = a.get("provider_request_id"), b.get("provider_request_id")
        if (
            not isinstance(a_job, str)
            or not a_job
            or not isinstance(b_job, str)
            or not b_job
            or a_job == b_job
            or canonical_checksum(a) == canonical_checksum(b)
            or (a_request is not None and a_request == b_request)
        ):
            return "DISTINCT_REPEAT_EXECUTION_RECEIPTS_REQUIRED"
    return None


def _execution_pin(binding: _CaseBinding | None) -> dict[str, Any] | None:
    if binding is None:
        return None
    row = binding.result
    if row is None:
        return None
    return {
        "run": _pin(binding.component.run),
        "dataset_version": _pin(binding.component.dataset),
        "execution_job_id": row.execution_reference.get("execution_job_id"),
        "receipt_checksum": canonical_checksum(row.execution_reference),
        "input_checksum": row.input_checksum,
        "observation_checksum": row.observation_checksum,
    }


def _safe_failure_categories(result: dict[str, Any] | None) -> list[str]:
    if result is None:
        return []
    return sorted(
        {
            category
            for category in result.get("failure_categories", [])
            if isinstance(category, str)
            and 0 < len(category) <= 120
            and all(
                character.isupper() or character.isdigit() or character == "_"
                for character in category
            )
        }
    )


def _paired_metrics(
    pairs: list[tuple[EvaluationCaseResult | None, EvaluationCaseResult | None]],
    evidence_class: str,
) -> list[dict[str, Any]]:
    names = sorted(
        {
            name
            for pair in pairs
            for row in pair
            if row is not None
            for name in row.result["metrics"]
        }
    )
    metrics = []
    for name in names:
        paired_values = []
        for left, right in pairs:
            values = [
                row.result["metrics"].get(name, {}) if row is not None else {}
                for row in (left, right)
            ]
            measurements = [
                (_decimal(value.get("numerator")), _decimal(value.get("denominator")))
                for value in values
            ]
            if not all(
                number is not None
                and denominator is not None
                and denominator > 0
                and _decimal(value.get("value")) is not None
                and value.get("missing_count") == 0
                for (number, denominator), value in zip(measurements, values)
            ):
                continue
            paired_values.append(measurements)
        count = len(paired_values)
        sides: list[dict[str, Any] | None] = []
        for index in (0, 1):
            if not count:
                sides.append(None)
                continue
            numerator = sum((pair[index][0] or Decimal(0) for pair in paired_values), Decimal(0))
            denominator = sum((pair[index][1] or Decimal(0) for pair in paired_values), Decimal(0))
            sides.append(
                {
                    "value": float(numerator / denominator),
                    "numerator": str(numerator),
                    "denominator": str(denominator),
                    "sample_size": count,
                    "missing_count": len(pairs) - count,
                }
            )
        left_value, right_value = sides
        metrics.append(
            {
                "name": name,
                "baseline": left_value,
                "candidate": right_value,
                "delta": right_value["value"] - left_value["value"]
                if left_value is not None and right_value is not None
                else None,
                "paired_case_count": count,
                "missing_case_count": len(pairs) - count,
                "evidence_class": evidence_class,
            }
        )
    return metrics


def _component_projection(side: _Side) -> dict[str, Any]:
    return {
        "run": _pin(side.run),
        "dataset_version": _pin(side.dataset),
        "configuration_checksum": side.run.configuration_checksum,
        "case_count": side.run.case_count,
    }


def _group_configuration(group: _RunGroup) -> list[dict[str, Any]]:
    return [
        {
            **_component_projection(side),
            "configuration": copy.deepcopy(side.run.configuration),
            "case_receipts": [
                {
                    "case_id": result.case_id,
                    "result": _pin(result),
                    "execution_job_id": result.execution_reference.get("execution_job_id"),
                    "receipt_checksum": canonical_checksum(result.execution_reference),
                    "input_checksum": result.input_checksum,
                    "observation_checksum": result.observation_checksum,
                }
                for result in sorted(side.results.values(), key=lambda row: row.case_id)
            ],
        }
        for side in group.components
    ]


def _request_body(payload: RegressionComparisonInput) -> dict[str, Any]:
    body = payload.model_dump(mode="json")
    for key in ("baseline_additional_runs", "candidate_additional_runs"):
        if not body[key]:
            body.pop(key)
    return body


def _review_exclusions(
    session: Session,
    suite: EvaluationDatasetVersion,
    baseline: _RunGroup,
    candidate: _RunGroup,
    executor: uuid.UUID,
) -> list[str]:
    from app.evaluation.regression_registry import regression_review_excluded_actor_ids

    return sorted(
        str(identifier)
        for identifier in (
            regression_review_excluded_actor_ids(session, suite)
            | {side.run.created_by for side in (*baseline.components, *candidate.components)}
            | {executor}
        )
    )


def verify_regression_configuration(
    session: Session, run: EvaluationRun, version: EvaluationDatasetVersion
) -> None:
    """Verify immutable pins. Source adapter restrictions bound recursion."""
    from app.evaluation.regression_registry import verify_regression_suite
    from app.evaluation.regression_thresholds import verify_threshold_pin

    config = run.configuration
    request = RegressionComparisonInput.model_validate(config["request"])
    if (
        config.get("execution_adapter") != ADAPTER
        or config.get("comparison_implementation_version") != METHOD_VERSION
        or config.get("implementation_version") != version.provenance.get("implementation_version")
        or config.get("evaluation_method") != version.provenance.get("evaluation_method")
        or config.get("evaluation_method_version")
        != version.provenance.get("evaluation_method_version")
        or request.operation_id != run.operation_id
        or canonical_checksum(_request_body(request)) != run.request_checksum
        or request.suite_version.id != version.id
        or request.suite_version.checksum != version.checksum
        or run.dataset_checksum != version.checksum
        or config.get("dataset_split") != version.provenance.get("split")
        or config.get("metric_configuration") != version.provenance.get("metric_configuration")
        or config.get("metric_configuration_checksum")
        != version.provenance.get("metric_configuration_checksum")
    ):
        raise foundation.EvaluationConflict("REGRESSION_RUN_PROVENANCE_BROKEN")
    verify_regression_suite(session, version)
    baseline = _load_group(session, request.baseline_run, request.baseline_additional_runs, None)
    candidate = _load_group(session, request.candidate_run, request.candidate_additional_runs, None)
    if (
        config.get("baseline_snapshot") != _group_configuration(baseline)
        or config.get("candidate_snapshot") != _group_configuration(candidate)
        or config.get("review_excluded_actor_ids")
        != _review_exclusions(session, version, baseline, candidate, run.created_by)
    ):
        raise foundation.EvaluationConflict("REGRESSION_RUN_PROVENANCE_BROKEN")
    if request.threshold_policy is not None:
        verify_threshold_pin(session, request.threshold_policy)


def regression_projection(
    run: EvaluationRun, results: list[EvaluationCaseResult]
) -> dict[str, Any]:
    if run.configuration.get("execution_adapter") != ADAPTER:
        raise ValueError("REGRESSION_COMPARISON_RUN_REQUIRED")
    summary = next(
        (row.result["regression_summary"] for row in results if "regression_summary" in row.result),
        None,
    )
    if not isinstance(summary, dict):
        raise foundation.EvaluationConflict("REGRESSION_RUN_SUMMARY_UNAVAILABLE")
    return {
        **copy.deepcopy(summary),
        "case_findings": [
            copy.deepcopy(row.result["regression_case"])
            for row in sorted(results, key=lambda row: row.case_id)
        ],
    }


def execute_regression_comparison(
    session: Session, context: TenantContext, payload: RegressionComparisonInput
) -> EvaluationRun:
    from app.evaluation.regression_registry import resolve_regression_suite
    from app.evaluation.regression_thresholds import evaluate_threshold_policy, verify_threshold_pin

    trusted = foundation.require(session, context, foundation.RUN, foundation.MANAGE)
    if payload.baseline_run.id == payload.candidate_run.id:
        raise ValueError("REGRESSION_DISTINCT_SOURCE_RUNS_REQUIRED")
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": payload.operation_id.int % (2**63 - 1)},
        )
    request = _request_body(payload)
    request_checksum = canonical_checksum(request)
    suite = resolve_regression_suite(
        session, trusted, payload.suite_version.id, payload.suite_version.checksum
    )
    owner = session.get(EvaluationDataset, suite.dataset_id)
    if owner is None:
        raise foundation.EvaluationConflict("REGRESSION_SUITE_SCOPE_UNAVAILABLE")
    baseline = _load_group(session, payload.baseline_run, payload.baseline_additional_runs, trusted)
    candidate = _load_group(
        session, payload.candidate_run, payload.candidate_additional_runs, trusted
    )
    baseline_run, candidate_run = baseline.primary.run, candidate.primary.run
    previous = session.scalar(
        select(EvaluationRun).where(EvaluationRun.operation_id == payload.operation_id)
    )
    if previous is not None:
        if previous.created_by != trusted.user_id or previous.request_checksum != request_checksum:
            raise foundation.EvaluationConflict("EVALUATION_OPERATION_CONFLICT")
        foundation.verify_run(session, previous)
        return previous
    metadata = suite.provenance
    reason = _comparison_reason(session, suite, owner, baseline, candidate, payload.dimension)
    evidence_class = (
        "SYNTHETIC_TEST"
        if (
            suite.evidence_class in SYNTHETIC_CLASSES
            or baseline_run.execution_class == "TEST_ONLY"
            or candidate_run.execution_class == "TEST_ONLY"
        )
        else suite.evidence_class
    )
    findings = []
    pairs = []
    missing_baseline, missing_candidate = [], []
    for case, case_pin in zip(suite.cases, metadata["case_pins"], strict=True):
        fingerprint = case_pin["cohort_fingerprint"]
        left_binding = baseline.bindings.get(fingerprint)
        right_binding = candidate.bindings.get(fingerprint)
        left_available = left_binding.result if left_binding is not None else None
        right_available = right_binding.result if right_binding is not None else None
        left_row = left_available if reason is None else None
        right_row = right_available if reason is None else None
        pairs.append((left_row, right_row))
        case_id = case["case_id"]
        if left_available is None:
            missing_baseline.append(case_id)
        if right_available is None:
            missing_candidate.append(case_id)
        left_result = left_row.result if left_row is not None else None
        right_result = right_row.result if right_row is not None else None
        checks = [
            {
                "code": code,
                "baseline": measure_required_check(left_result, code, case),
                "candidate": measure_required_check(right_result, code, case),
            }
            for code in case_pin["required_checks"]
        ]
        findings.append(
            {
                "case_id": case_id,
                "case_version": {
                    "id": case_pin["case_version_id"],
                    "checksum": case_pin["checksum"],
                },
                "baseline_result": _pin(left_row) if left_row is not None else None,
                "candidate_result": _pin(right_row) if right_row is not None else None,
                "baseline_execution": _execution_pin(left_binding)
                if left_row is not None
                else None,
                "candidate_execution": _execution_pin(right_binding)
                if right_row is not None
                else None,
                "checks": checks,
                "baseline_status": case_technical_status(
                    left_result, [row["baseline"] for row in checks], case
                ),
                "candidate_status": case_technical_status(
                    right_result, [row["candidate"] for row in checks], case
                ),
                "baseline_failure_categories": _safe_failure_categories(left_result),
                "candidate_failure_categories": _safe_failure_categories(right_result),
                "evidence_class": evidence_class,
            }
        )
    expected_count = len(suite.cases)
    paired_count = sum(left is not None and right is not None for left, right in pairs)
    complete = (
        paired_count == expected_count
        and baseline_run.status == candidate_run.status == "COMPLETED"
    )
    if reason is not None:
        comparison_status = "NOT_COMPARABLE"
    else:
        comparison_status = "COMPARABLE" if complete else "NOT_EVALUATED"
    technical_status = (
        "FAIL"
        if any(item["candidate_status"] == "FAIL" for item in findings)
        else "PASS"
        if complete and all(item["candidate_status"] == "PASS" for item in findings)
        else "NOT_EVALUATED"
    )
    metrics = _paired_metrics(pairs, evidence_class)
    evaluated_at = foundation.utcnow()
    if payload.threshold_policy is None or (
        comparison_status == "COMPARABLE" and technical_status == "PASS"
    ):
        threshold = evaluate_threshold_policy(
            session,
            trusted,
            payload.threshold_policy,
            suite=suite,
            baseline=baseline_run,
            candidate=candidate_run,
            paired_metrics=metrics,
            expected_case_count=expected_count,
            paired_case_count=paired_count,
            evaluated_at=evaluated_at,
        )
    else:
        verify_threshold_pin(session, payload.threshold_policy)
        threshold = {
            "status": "NOT_EVALUATED",
            "policy": payload.threshold_policy.model_dump(mode="json"),
            "reason_code": "TECHNICAL_OR_COMPARISON_EVIDENCE_INELIGIBLE",
            "paired_case_count": paired_count,
        }
    empirical_status = threshold["status"]
    changed = [
        {
            "name": key,
            "baseline": baseline_run.configuration.get(key),
            "candidate": candidate_run.configuration.get(key),
        }
        for key in ARTIFACT_KEYS
        if baseline_run.configuration.get(key) != candidate_run.configuration.get(key)
    ]
    summary = {
        "suite_version": _pin(suite),
        "baseline_run": _pin(baseline_run),
        "candidate_run": _pin(candidate_run),
        "baseline_dataset_version": _pin(baseline.primary.dataset),
        "candidate_dataset_version": _pin(candidate.primary.dataset),
        "baseline_components": [_component_projection(side) for side in baseline.components],
        "candidate_components": [_component_projection(side) for side in candidate.components],
        "dimension": payload.dimension,
        "comparison_status": comparison_status,
        "comparison_reason_code": reason
        or (None if complete else "INCOMPLETE_PAIRED_CASE_RESULTS"),
        "technical_status": technical_status,
        "empirical_status": empirical_status,
        "decision": gate_decision(comparison_status, technical_status, empirical_status),
        "expected_case_count": expected_count,
        "paired_case_count": paired_count,
        "missing_baseline_case_ids": missing_baseline,
        "missing_candidate_case_ids": missing_candidate,
        "complete": complete,
        "paired_metrics": metrics,
        "changed_pins": changed,
        "baseline_artifacts": {key: baseline_run.configuration.get(key) for key in ARTIFACT_KEYS},
        "candidate_artifacts": {key: candidate_run.configuration.get(key) for key in ARTIFACT_KEYS},
        "empirical": threshold,
        "reference_maturity": metadata["evidence_maturity"],
        "reference_quality_status": "DESCRIPTIVE_ONLY"
        if evidence_class == "EXPERT_LABELLED"
        and any(
            metric["paired_case_count"] > 0
            and metric["name"]
            in {
                "mean_absolute_criterion_error",
                "word_error_rate",
                "character_error_rate",
            }
            for metric in metrics
        )
        else "NOT_AVAILABLE",
        "execution_class": candidate_run.execution_class,
        "evidence_class": evidence_class,
        "dataset_split": metadata["split"],
        "limitations": LIMITATIONS
        + (
            ["Repeatability compares distinct executions under identical artifact configuration."]
            if payload.dimension == "EXECUTION_REPEAT"
            else []
        ),
        "production_activation": False,
    }
    configuration = {
        "execution_adapter": ADAPTER,
        "evaluation_method": metadata["evaluation_method"],
        "evaluation_method_version": metadata["evaluation_method_version"],
        "implementation_version": metadata["implementation_version"],
        "comparison_implementation_version": METHOD_VERSION,
        "dataset_split": metadata["split"],
        "metric_configuration": metadata["metric_configuration"],
        "metric_configuration_checksum": metadata["metric_configuration_checksum"],
        "request": request,
        "baseline_snapshot": _group_configuration(baseline),
        "candidate_snapshot": _group_configuration(candidate),
        "review_excluded_actor_ids": _review_exclusions(
            session, suite, baseline, candidate, trusted.user_id
        ),
    }
    run = EvaluationRun(
        operation_id=payload.operation_id,
        dataset_version_id=suite.id,
        dataset_checksum=suite.checksum,
        request_checksum=request_checksum,
        configuration=configuration,
        configuration_checksum=canonical_checksum(configuration),
        metrics={},
        limitations=LIMITATIONS,
        execution_class=candidate_run.execution_class,
        evidence_class=evidence_class,
        status="RUNNING",
        hard_gate="PENDING",
        case_count=expected_count,
        failure_count=0,
        created_by=trusted.user_id,
        created_at=evaluated_at,
    )
    session.add(run)
    session.flush()
    results = []
    for index, (case, finding) in enumerate(zip(suite.cases, findings, strict=True)):
        result = {
            "case_id": finding["case_id"],
            "kind": "REGRESSION",
            "method_version": METHOD_VERSION,
            "evidence_class": evidence_class,
            "metrics": {},
            "hard_fail": finding["candidate_status"] == "FAIL",
            "failure_categories": finding["candidate_failure_categories"],
            "regression_case": finding,
        }
        if index == 0:
            result["regression_summary"] = summary
        receipt = {
            "baseline_result": finding["baseline_result"],
            "candidate_result": finding["candidate_result"],
        }
        data = {
            "input_checksum": canonical_checksum(case),
            "observation_checksum": canonical_checksum(receipt),
            "execution_reference": receipt,
            "result": result,
        }
        row = EvaluationCaseResult(
            run_id=run.id,
            case_id=finding["case_id"],
            **data,
            checksum=canonical_checksum(data),
            created_at=evaluated_at,
        )
        session.add(row)
        results.append(row)
    session.flush()
    run.metrics = {
        metric["name"]: {
            **(
                metric["candidate"]
                or {
                    "value": None,
                    "numerator": None,
                    "denominator": None,
                    "sample_size": 0,
                    "missing_count": expected_count,
                }
            ),
            "evidence_class": evidence_class,
            "limitations": LIMITATIONS,
            "unavailable_reason": None
            if metric["candidate"] is not None
            else "NO_COMPLETE_PAIRED_MEASUREMENTS",
        }
        for metric in metrics
    }
    run.failure_count = sum(item["candidate_status"] == "FAIL" for item in findings)
    run.hard_gate = "PASS" if technical_status == "PASS" else "FAIL"
    run.status, run.completed_at = "COMPLETED", foundation.utcnow()
    run.checksum = foundation._run_checksum(run, results)
    session.flush()
    foundation._audit(session, trusted, "regression_comparison_completed", run)
    return run
