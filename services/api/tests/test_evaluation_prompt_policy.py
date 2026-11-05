"""Offline prompt policy evidence comes from the actual native request builder."""

import copy
import uuid

import pytest

from app.evaluation import prompt_policy
from app.evaluation.metrics import aggregate_case_results
from app.evaluation.regression_comparison import measure_required_check
from app.evaluation.regression_registry import measured_failure_keys
from app.models import PromptVersion


def _case(suffix: str) -> dict:
    canary = "PRUFAI_SYNTHETIC_CANARY_" + suffix * 32
    identity = {"synthetic_fixture_id": "synthetic:" + canary}
    return {
        "case_id": "synthetic-prompt-policy-" + suffix,
        "kind": "WRITING",
        "split": "TEST",
        "input": {
            "language": "de",
            "modality": "TEXT",
            "adversarial": True,
            "text": canary,
            "evidence_identity": identity,
        },
        "reference": {
            "label_kind": "SYNTHETIC_EXPECTATION",
            "language": "de",
            "criteria": [{"code": "TASK", "max_points": "4"}],
            "evidence_identity": dict(identity),
        },
    }


def _prompt(instructions: str | None, *, system: str = "Use the pinned schema.") -> PromptVersion:
    payload = {
        "system_template": system,
        "instruction_template": instructions,
        "configuration": {},
        "input_schema_version": "1",
        "output_schema_version": "1",
    }
    return PromptVersion(
        id=uuid.uuid4(), checksum=prompt_policy.prompt_checksum(payload), **payload
    )


def test_clearing_legacy_instructions_changes_measured_native_request_for_two_cases():
    cases = [_case("1"), _case("2")]
    canaries = [case["input"]["text"] for case in cases]
    baseline = _prompt("\n".join(canaries))
    candidate = _prompt(None)
    outputs: list[list[dict]] = [[], []]
    for case in cases:
        receipts = []
        for side, artifact in enumerate((baseline, candidate)):
            _, observation, receipt, result = prompt_policy.measure_prompt_request(case, artifact)
            outputs[side].append(result)
            receipts.append(receipt)
            assert receipt["prompt_checksum"] == artifact.checksum
            assert receipt["prompt_version_id"] == str(artifact.id)
            assert receipt["request_checksum"] == observation["request_checksum"]
            assert receipt["execution_class"] == "TEST_ONLY"
            assert receipt["model_execution"] == "NOT_RUN"
            assert "provider_request_id" not in receipt
            assert "score_bounds_rate" not in result["metrics"]
            assert all(canary not in str((observation, receipt, result)) for canary in canaries)
            assert measure_required_check(result, "prompt_channel_isolation", case) == {
                "status": "PASS" if side else "FAIL",
                "numerator": side,
                "denominator": 1,
                "reason_code": None if side else "REQUIRED_CHECK_FAILED",
            }
            assert measured_failure_keys(result) == ([] if side else ["prompt_channel_isolation"])
        assert receipts[0]["request_checksum"] != receipts[1]["request_checksum"]
    for side, results in enumerate(outputs):
        aggregate = aggregate_case_results(results)
        metric = aggregate["metrics"]["prompt_channel_isolation_rate"]
        assert metric["numerator"] == str(side * 2)
        assert metric["denominator"] == "2"
        assert aggregate["hard_fail"] is (not side)


def test_clearing_legacy_instructions_does_not_hide_canary_in_preserved_system_template():
    case = _case("3")
    _, _, _, result = prompt_policy.measure_prompt_request(
        case, _prompt(None, system=case["input"]["text"])
    )
    assert result["hard_fail"] is True
    assert measured_failure_keys(result) == ["prompt_channel_isolation"]


@pytest.mark.parametrize(
    "field",
    ["extra_text", "private_identity", "synthetic_observation", "execution_job_id", "source_text"],
)
def test_offline_run_rejects_noncanary_data_and_caller_execution_evidence(field):
    case = _case("4")
    if field == "extra_text":
        case["input"]["text"] += " private text"
    elif field == "private_identity":
        case["input"]["evidence_identity"]["candidate_user_id"] = str(uuid.uuid4())
    elif field == "source_text":
        case["reference"]["text"] = "private text"
    else:
        case[field] = {} if field == "synthetic_observation" else str(uuid.uuid4())
    with pytest.raises(ValueError, match="PROMPT_POLICY_SYNTHETIC_CANARY_CASE_REQUIRED"):
        prompt_policy.measure_prompt_request(case, _prompt(None))


def test_offline_request_checks_observe_actual_schema_and_authority_configuration(monkeypatch):
    actual_builder = prompt_policy.build_request

    def changed_builder(input_, snapshot):
        request = copy.deepcopy(actual_builder(input_, snapshot))
        request["tools"] = [{"type": "forbidden"}]
        request["store"] = True
        request["text"]["format"]["strict"] = False
        return request

    monkeypatch.setattr(prompt_policy, "build_request", changed_builder)
    _, _, _, result = prompt_policy.measure_prompt_request(_case("5"), _prompt(None))
    assert measured_failure_keys(result) == ["prompt_request_authority", "prompt_request_schema"]
    assert result["hard_fail"] is True
