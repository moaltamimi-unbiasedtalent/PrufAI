"""Fail-closed policy tests using technical identities only."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.evaluation import regression_thresholds as thresholds
from app.evaluation.regression_registry import ArtifactPin
from app.evaluation.service import EvaluationConflict


def _definition(**changes):
    now = datetime.now(timezone.utc)
    return {
        "operation_id": uuid4(),
        "suite_version": {"id": uuid4(), "checksum": "a" * 64},
        "calibration_study": {"id": uuid4(), "checksum": "b" * 64},
        "metric": "mean_absolute_criterion_error",
        "metric_implementation_version": "fixture-1",
        "metric_configuration_checksum": "c" * 64,
        "basis": "CANDIDATE_VALUE",
        "maximum": "0.125",
        "minimum_paired_cases": 2,
        "effective_from": now,
        "effective_until": now + timedelta(days=2),
        "study_method_reference": "test:policy-shape",
        "rationale": "Synthetic structural fixture only.",
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"maximum": "NaN"},
        {"maximum": "-0.2"},
        {"minimum_paired_cases": 1},
        {"rationale": "  "},
        {"effective_until": datetime(2026, 1, 1)},
        {"metric": "criterion_coverage_rate"},
    ],
)
def test_threshold_definition_rejects_invalid_or_non_empirical_policy(changes):
    with pytest.raises(ValidationError, match="validation error"):
        thresholds.ThresholdDefinitionInput.model_validate(_definition(**changes))


def test_synthetic_suite_cannot_form_a_threshold_study(monkeypatch):
    suite = SimpleNamespace(evidence_class="SYNTHETIC_TEST")
    monkeypatch.setattr(thresholds, "resolve_regression_suite", lambda *args, **kwargs: suite)
    policy = thresholds.ThresholdDefinitionInput.model_validate(_definition())
    with pytest.raises(EvaluationConflict, match="GENUINE_REFERENCE_REQUIRED"):
        thresholds._study(None, None, policy)


def test_policy_absence_is_unconfigured_and_test_receipts_cannot_satisfy_a_policy(monkeypatch):
    policy = thresholds.ThresholdDefinitionInput.model_validate(_definition())
    now = datetime.now(timezone.utc)
    evidence = {
        "suite": SimpleNamespace(
            id=policy.suite_version.id,
            checksum=policy.suite_version.checksum,
            evidence_class="SYNTHETIC_TEST",
        ),
        "baseline": SimpleNamespace(execution_class="TEST_ONLY", evidence_class="SYNTHETIC_TEST"),
        "candidate": SimpleNamespace(execution_class="TEST_ONLY", evidence_class="SYNTHETIC_TEST"),
        "paired_metrics": [],
        "expected_case_count": 2,
        "paired_case_count": 2,
        "evaluated_at": now,
    }
    assert thresholds.evaluate_threshold_policy(None, None, None, **evidence)["status"] == (
        "UNCONFIGURED"
    )
    # This mock tests the applicability guard, not an approval or a study.
    monkeypatch.setattr(thresholds.foundation, "require", lambda *args: None)
    monkeypatch.setattr(
        thresholds,
        "verify_threshold_pin",
        lambda *args: SimpleNamespace(body=policy.model_dump(mode="json"), approved_at=now),
    )
    pin = ArtifactPin(id=uuid4(), checksum="d" * 64)
    denied = thresholds.evaluate_threshold_policy(None, None, pin, **evidence)
    assert denied["status"] == "NOT_EVALUATED"
    assert denied["reason_code"] == "THRESHOLD_SCOPE_OR_EVIDENCE_MISMATCH"
    assert denied["observed_value"] is None
