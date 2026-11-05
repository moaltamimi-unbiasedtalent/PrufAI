"""The isolated Writing worker executes the real test adapter and validator."""

import json
import uuid

import pytest

from app.authorization import AuthorizationError
from app.config import Settings
from app.feedback.common import FeedbackConflict
from app.improvement import validation
from app.writing_assessment.service import canonical_checksum
from tests.test_evaluation_prompt_policy import _case, _prompt


def _settings(*, adapter_enabled: bool = True) -> Settings:
    return Settings(
        _env_file=None, environment="test", writing_test_adapter_enabled=adapter_enabled
    )


def test_exact_prompt_and_serialized_user_input_reach_the_isolated_writing_path(monkeypatch):
    builder = validation.build_request
    generate = validation.TestOnlyWritingProvider.generate
    validate = validation.WritingExecutionService.validate_proposal
    captured = {}

    def capture_request(input_, snapshot):
        captured["request"] = builder(input_, snapshot)
        return captured["request"]

    def capture_adapter(adapter, input_, **kwargs):
        assert input_ == json.loads(captured["request"]["input"][0]["content"][0]["text"])
        captured["adapter"] = generate(adapter, input_, **kwargs)
        return captured["adapter"]

    def capture_validated(proposal, input_):
        assert proposal == captured["adapter"].proposal
        captured["output"] = validate(proposal, input_)
        return captured["output"]

    monkeypatch.setattr(validation, "build_request", capture_request)
    monkeypatch.setattr(validation.TestOnlyWritingProvider, "generate", capture_adapter)
    monkeypatch.setattr(validation.WritingExecutionService, "validate_proposal", capture_validated)
    prompt = _prompt(None, system="Use the exact isolated technical request schema.")
    case = _case("a")
    receipt = validation._execute_case(case, prompt, uuid.uuid4(), _settings())
    request = captured["request"]
    adapter = captured["adapter"]
    assert (
        request["instructions"] == validation.SYSTEM_BOUNDARY + "\n" + prompt.system_template + "\n"
    )
    assert request["tools"] == [] and request["store"] is False
    assert receipt == {
        "case_id": case["case_id"],
        "case_checksum": canonical_checksum(case),
        "input_checksum": adapter.metadata["input_checksum"],
        "request_checksum": canonical_checksum(request),
        "output_checksum": canonical_checksum(captured["output"]),
        "adapter_receipt_checksum": canonical_checksum(
            {
                "provider_request_id": adapter.provider_request_id,
                "metadata": adapter.metadata,
            }
        ),
        "validated_criteria_count": 1,
    }
    assert case["input"]["text"] not in str(receipt)
    assert prompt.system_template not in str(receipt)
    assert adapter.provider_request_id not in str(receipt)


@pytest.mark.parametrize("fault", ["trusted_channel", "tool_policy", "disabled_adapter"])
def test_unavailable_adapter_and_invalid_native_request_stop_before_execution(monkeypatch, fault):
    case = _case("b")
    settings = _settings(adapter_enabled=fault != "disabled_adapter")
    prompt = _prompt(case["input"]["text"] if fault == "trusted_channel" else None)
    if fault == "tool_policy":
        builder = validation.build_request

        def unsafe_request(input_, snapshot):
            return {**builder(input_, snapshot), "tools": [{"type": "unsafe"}]}

        monkeypatch.setattr(validation, "build_request", unsafe_request)

    def unexpected_adapter(*args, **kwargs):
        raise AssertionError("adapter must not be invoked")

    monkeypatch.setattr(validation.TestOnlyWritingProvider, "generate", unexpected_adapter)
    expected = AuthorizationError if fault == "disabled_adapter" else FeedbackConflict
    with pytest.raises(expected):
        validation._execute_case(case, prompt, uuid.uuid4(), settings)
