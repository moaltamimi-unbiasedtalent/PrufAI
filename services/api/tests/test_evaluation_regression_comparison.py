"""Technical regression decisions on original synthetic observations."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.evaluation import service as foundation
from app.evaluation.metrics import evaluate_case
from app.evaluation.regression_comparison import (
    REPLAY_ADAPTER,
    SYNTHETIC_ADAPTER,
    RegressionComparisonInput,
    _assemble_group,
    _comparison_reason,
    _execution_pin,
    _paired_metrics,
    _repeat_reason,
    _Side,
    case_technical_status,
    gate_decision,
    measure_required_check,
)
from app.models import (
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationRun,
)
from app.writing_assessment.service import canonical_checksum
from tests.test_evaluation_metrics import retrieval, speaking, transcription, writing


def _evaluate(case, observation):
    return evaluate_case(case, observation, evidence_class="SYNTHETIC_TEST")


def _negative_observation(regression):
    if regression in {"wrong_sha", "wrong_span", "transcript_only"}:
        case, observation = speaking()
        if regression == "wrong_sha":
            observation["audio_sha256"] = "e" * 64
        elif regression == "wrong_span":
            observation["criteria"][0]["evidence"][0]["end_ms"] = 1001
        else:
            observation["modality"] = "TRANSCRIPT"
    elif regression in {"wrong_tenant", "fabricated_citation", "stale_source"}:
        case, observation = retrieval()
        if regression == "wrong_tenant":
            observation["retrieved"][0]["organisation_id"] = "synthetic-foreign-tenant"
        elif regression == "fabricated_citation":
            observation["citations"][0]["quote"] = "Unobserved synthetic quote"
        else:
            observation["retrieved"][0]["version_id"] = "synthetic-stale-version"
    else:
        case, observation = writing()
        if regression == "missing_criterion":
            observation["criteria"] = []
        elif regression == "unknown_criterion":
            observation["criteria"][0]["criterion_code"] = "UNDECLARED"
        elif regression == "invalid_score":
            observation["criteria"][0]["proposed_points"] = "4.01"
        elif regression == "invalid_schema":
            observation.pop("criteria")
        elif regression == "stale_provenance":
            case["input"]["evidence_identity"] = {"response_checksum": "e" * 64}
        elif regression == "wrong_response":
            observation["evidence_identity"] = {"response_checksum": "e" * 64}
        elif regression == "wrong_quote":
            observation["criteria"][0]["evidence"][0]["start"] = 1
        elif regression == "unsafe_action":
            case["input"]["adversarial"] = True
            observation["actions"] = ["publish_candidate_result"]
        else:
            raise AssertionError("unknown synthetic regression fixture")
    return case, observation


@pytest.mark.parametrize(
    ("regression", "check", "category"),
    [
        ("missing_criterion", "criterion_coverage", "MISSING_OR_UNKNOWN_CRITERIA"),
        ("unknown_criterion", "criterion_coverage", "MISSING_OR_UNKNOWN_CRITERIA"),
        ("invalid_score", "score_bounds", "OUT_OF_RANGE_OR_MISSING_SCORE"),
        ("wrong_sha", "audio_identity", "AUDIO_IDENTITY_MISMATCH"),
        ("wrong_span", "evidence_validity", "INVALID_EVIDENCE_QUOTE_OR_SPAN"),
        ("transcript_only", "modality_compliance", "UNSUPPORTED_MODEL_MODALITY"),
        ("wrong_tenant", "tenant_isolation", "CROSS_TENANT_CONTAMINATION"),
        ("fabricated_citation", "citation_quote_support", "UNSUPPORTED_CITATION_QUOTE"),
        ("stale_source", "source_identity", "CITATION_OR_SOURCE_VERSION_MISMATCH"),
        ("invalid_schema", "schema_compliance", "INVALID_OUTPUT_SCHEMA"),
        ("stale_provenance", "immutable_provenance", "STALE_OR_MISMATCHED_PROVENANCE"),
        ("wrong_response", "evidence_identity", "EVIDENCE_IDENTITY_MISMATCH"),
        ("wrong_quote", "evidence_validity", "INVALID_EVIDENCE_QUOTE_OR_SPAN"),
        ("unsafe_action", "prompt_injection_policy", "PROMPT_INJECTION_POLICY_VIOLATION"),
    ],
)
def test_measured_negative_observation_cannot_pass(regression, check, category):
    case, observation = _negative_observation(regression)
    result = _evaluate(case, observation)
    measurement = measure_required_check(result, check, case)
    assert measurement == {
        "status": "FAIL",
        "numerator": 0,
        "denominator": 1,
        "reason_code": "REQUIRED_CHECK_FAILED",
    }
    assert category in result["failure_categories"]
    assert case_technical_status(result, [measurement], case) == "FAIL"


@pytest.mark.parametrize(
    "changes",
    [
        {"denominator": 0},
        {"numerator": None},
        {"numerator": True},
        {"numerator": "NaN"},
        {"numerator": -1},
        {"numerator": 2},
        {"numerator": "0.5"},
        {"value": 0},
        {"sample_size": 0},
        {"missing_count": 1},
    ],
)
def test_pass_label_without_valid_measured_counts_is_unavailable(changes):
    case, observation = writing()
    result = _evaluate(case, observation)
    result["metrics"]["score_bounds_rate"].update(changes)
    measurement = measure_required_check(result, "score_bounds", case)
    assert measurement["status"] == "NOT_EVALUATED"
    assert measurement["numerator"] is measurement["denominator"] is None
    assert case_technical_status(result, [measurement], case) == "NOT_EVALUATED"


def test_required_timing_needs_actual_supported_segments():
    case, observation = transcription()
    omitted = _evaluate(case, observation)
    assert omitted["checks"]["segment_timing"] == "PASS"
    assert measure_required_check(omitted, "segment_timing", case)["status"] == "NOT_EVALUATED"
    case["reference"]["segments_supported"] = True
    absent = _evaluate(case, observation)
    assert measure_required_check(absent, "segment_timing", case)["status"] == "NOT_EVALUATED"
    observation["segments"] = [{"start_ms": 0, "end_ms": 1001, "text": "Synthetic"}]
    invalid = measure_required_check(_evaluate(case, observation), "segment_timing", case)
    assert invalid["status"] == "FAIL" and invalid["numerator"] == 0
    observation["segments"][0]["end_ms"] = 1000
    valid = measure_required_check(_evaluate(case, observation), "segment_timing", case)
    assert valid["status"] == "PASS" and valid["numerator"] == valid["denominator"] == 1


def test_missing_case_or_non_adversarial_fixture_cannot_prove_required_check():
    case, observation = writing()
    result = _evaluate(case, observation)
    assert (
        measure_required_check(None, "score_bounds", case)["reason_code"] == "MISSING_CASE_RESULT"
    )
    check = measure_required_check(result, "prompt_injection_policy", case)
    assert check["reason_code"] == "NO_ADVERSARIAL_CASE"
    assert case_technical_status(result, [check], case) == "NOT_EVALUATED"


def test_optional_selection_cannot_waive_a_measured_source_failure():
    case, observation = _negative_observation("invalid_score")
    result = _evaluate(case, observation)
    selected = measure_required_check(result, "schema_compliance", case)
    assert selected["status"] == "PASS"
    assert case_technical_status(result, [selected], case) == "FAIL"


def test_paired_error_preserves_actual_denominator_and_missing_reference_coverage():
    case, observation = writing()
    baseline = EvaluationCaseResult(result=_evaluate(case, observation))
    observation["criteria"][0]["proposed_points"] = "2"
    candidate = EvaluationCaseResult(result=_evaluate(case, observation))
    case["reference"]["criteria"][0]["points"] = None
    missing = EvaluationCaseResult(result=_evaluate(case, observation))
    metrics = _paired_metrics([(baseline, candidate), (missing, candidate)], "SYNTHETIC_TEST")
    error = next(metric for metric in metrics if metric["name"] == "mean_absolute_criterion_error")
    assert error["baseline"]["value"] == 1
    assert error["candidate"]["value"] == 0
    assert error["baseline"]["denominator"] == "1"
    assert error["delta"] == -1
    assert error["paired_case_count"] == error["missing_case_count"] == 1
    assert error["evidence_class"] == "SYNTHETIC_TEST"
    unavailable = _paired_metrics([(missing, missing)], "SYNTHETIC_TEST")
    absent = next(metric for metric in unavailable if metric["name"] == error["name"])
    assert absent["baseline"] is absent["candidate"] is absent["delta"] is None
    assert absent["paired_case_count"] == 0 and absent["missing_case_count"] == 1


@pytest.mark.parametrize(
    ("comparison", "technical", "empirical", "expected"),
    [
        ("COMPARABLE", "PASS", "UNCONFIGURED", "UNCONFIGURED"),
        ("COMPARABLE", "PASS", "NOT_EVALUATED", "NOT_EVALUATED"),
        ("NOT_COMPARABLE", "NOT_EVALUATED", "UNCONFIGURED", "NOT_COMPARABLE"),
        ("NOT_EVALUATED", "NOT_EVALUATED", "UNCONFIGURED", "NOT_EVALUATED"),
        ("COMPARABLE", "NOT_EVALUATED", "PASS", "NOT_EVALUATED"),
        ("NOT_EVALUATED", "FAIL", "UNCONFIGURED", "FAIL"),
        ("COMPARABLE", "PASS", "FAIL", "FAIL"),
        ("COMPARABLE", "PASS", "PASS", "PASS"),
    ],
)
def test_gate_never_converts_unavailable_or_incompatible_evidence_to_pass(
    comparison, technical, empirical, expected
):
    assert gate_decision(comparison, technical, empirical) == expected


def _component(owner, case_id, fingerprint, configuration, *, reference_pin=None):
    provenance = {"origin": "ORIGINAL_SYNTHETIC"}
    receipt = {"execution_class": "TEST_ONLY"}
    if reference_pin:
        provenance = {"origin": "GOVERNED_REFERENCE", **reference_pin}
        receipt["execution_job_id"] = str(uuid.uuid4())
        receipt["provider_request_id"] = str(uuid.uuid4())
    dataset = EvaluationDatasetVersion(
        id=uuid.uuid4(),
        checksum="d" * 64,
        assessment_family="TEST",
        skill="WRITING",
        language="de",
        cefr_level="A2",
        evidence_class="SYNTHETIC_TEST",
        provenance=provenance,
    )
    run = EvaluationRun(
        id=uuid.uuid4(),
        checksum="a" * 64,
        configuration=configuration,
        execution_class="TEST_ONLY",
        evidence_class="SYNTHETIC_TEST",
        status="COMPLETED",
        completed_at=datetime.now(UTC),
    )
    result = EvaluationCaseResult(
        id=uuid.uuid4(),
        case_id=case_id,
        execution_reference=receipt,
        input_checksum="b" * 64,
        observation_checksum="c" * 64,
    )
    return _Side(run, dataset, owner, {fingerprint: {"case_id": case_id}}, {case_id: result}, False)


def test_grouped_repeat_uses_each_component_governed_reference_and_receipt():
    owner = EvaluationDataset(organisation_id=uuid.uuid4())
    method = {
        "evaluation_method": "PROPOSAL_REPLAY",
        "evaluation_method_version": "1",
        "implementation_version": "1",
        "metric_configuration_checksum": "a" * 64,
    }
    configuration = {**method, "dataset_split": "CALIBRATION", "execution_adapter": REPLAY_ADAPTER}
    references = [
        {"reference_id": str(uuid.uuid4()), "adjudication_checksum": checksum * 64}
        for checksum in ("1", "2")
    ]
    left = _assemble_group(
        tuple(
            _component(owner, f"left-{index}", str(index), configuration, reference_pin=reference)
            for index, reference in enumerate(references)
        )
    )
    right = _assemble_group(
        tuple(
            _component(owner, f"right-{index}", str(index), configuration, reference_pin=reference)
            for index, reference in enumerate(references)
        )
    )
    suite = EvaluationDatasetVersion(
        assessment_family="TEST",
        skill="WRITING",
        language="de",
        cefr_level="A2",
        provenance={
            **method,
            "split": "CALIBRATION",
            "source_scope": {},
            "evidence_maturity": "SYNTHETIC_TEST",
            "case_pins": [
                {"cohort_fingerprint": str(index), "reference_pin": reference}
                for index, reference in enumerate(references)
            ],
        },
    )
    assert _comparison_reason(None, suite, owner, left, right, "EXECUTION_REPEAT") is None
    second = right.bindings["1"]
    result = second.result
    pin = _execution_pin(second)
    assert pin["run"]["id"] == str(right.components[1].run.id)
    assert pin["dataset_version"]["id"] == str(right.components[1].dataset.id)
    assert pin["execution_job_id"] == result.execution_reference["execution_job_id"]
    assert pin["receipt_checksum"] == canonical_checksum(result.execution_reference)
    assert "provider_request_id" not in pin
    second.component.dataset.provenance["adjudication_checksum"] = "3" * 64
    assert (
        _comparison_reason(None, suite, owner, left, right, "EXECUTION_REPEAT")
        == "GOVERNED_REFERENCE_PIN_MISMATCH"
    )


@pytest.mark.parametrize(
    ("case_id", "fingerprint", "configuration", "reason"),
    [
        ("case-b", "fp-a", {}, "REGRESSION_GROUP_COHORT_OVERLAP"),
        ("case-a", "fp-b", {}, "REGRESSION_GROUP_COHORT_OVERLAP"),
        (
            "case-b",
            "fp-b",
            {"prompt_version_id": "different"},
            "REGRESSION_GROUP_EXECUTION_CONFIGURATION_MISMATCH",
        ),
    ],
)
def test_group_rejects_overlap_and_mixed_execution_config(
    case_id, fingerprint, configuration, reason
):
    owner = EvaluationDataset(organisation_id=uuid.uuid4())
    with pytest.raises(foundation.EvaluationConflict, match=reason):
        _assemble_group(
            (
                _component(owner, "case-a", "fp-a", {}),
                _component(owner, case_id, fingerprint, configuration),
            )
        )


@pytest.mark.parametrize("reuse", ["execution_job_id", "provider_request_id"])
def test_repeat_rejects_replaying_same_execution_or_provider_request(reuse):
    owner = EvaluationDataset(organisation_id=uuid.uuid4())
    configuration = {"execution_adapter": REPLAY_ADAPTER}
    reference = {"reference_id": str(uuid.uuid4())}
    left = _component(owner, "case-a", "fp-a", configuration, reference_pin=reference)
    right = _component(owner, "case-b", "fp-a", configuration, reference_pin=reference)
    right.results["case-b"].execution_reference[reuse] = left.results["case-a"].execution_reference[
        reuse
    ]
    assert (
        _repeat_reason(_assemble_group((left,)), _assemble_group((right,)))
        == "DISTINCT_REPEAT_EXECUTION_RECEIPTS_REQUIRED"
    )


@pytest.mark.parametrize("adapter", [REPLAY_ADAPTER, SYNTHETIC_ADAPTER])
def test_repeat_requires_identical_pinned_artifacts(adapter):
    owner = EvaluationDataset(organisation_id=uuid.uuid4())
    left = _component(owner, "case-a", "fp-a", {"execution_adapter": adapter, "model_version": "a"})
    right = _component(
        owner,
        "case-b",
        "fp-a",
        {
            "execution_adapter": adapter,
            "model_version": "b" if adapter == REPLAY_ADAPTER else "a",
        },
    )
    assert (
        _repeat_reason(_assemble_group((left,)), _assemble_group((right,)))
        == "IDENTICAL_PINNED_REPEAT_CONFIGURATION_REQUIRED"
    )


@pytest.mark.parametrize("reuse", ["baseline_run", "candidate_run"])
def test_comparison_rejects_reused_source_run_id_across_components(reuse):
    body = {
        key: {"id": str(uuid.uuid4()), "checksum": "a" * 64}
        for key in ("suite_version", "baseline_run", "candidate_run")
    }
    with pytest.raises(ValidationError, match="REGRESSION_DISTINCT_SOURCE_RUNS_REQUIRED"):
        RegressionComparisonInput.model_validate(
            {
                **body,
                "operation_id": str(uuid.uuid4()),
                "dimension": "TECHNICAL_FIXTURE",
                "baseline_additional_runs": [{**body[reuse], "checksum": "b" * 64}],
            }
        )
