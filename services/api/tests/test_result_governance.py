import pytest

from app.models.enums import AppealStatus, CorrectionType, ResultVersionStatus
from app.result_governance.service import (
    AppealPolicy,
    ResultGovernanceError,
    ResultGovernanceService,
)


def test_default_policy_is_explicitly_released_only_without_provider_deadline() -> None:
    policy = AppealPolicy()
    assert policy.eligible_result_statuses == frozenset({ResultVersionStatus.RELEASED})
    assert policy.window_days is None


def test_invalid_provider_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        AppealPolicy(window_days=-1)


def test_appeal_lifecycle_does_not_allow_reopening_closed_history() -> None:
    assert AppealStatus.CLOSED not in ResultGovernanceService._TRANSITIONS[AppealStatus.CLOSED]
    assert AppealStatus.UNDER_REVIEW in ResultGovernanceService._TRANSITIONS[AppealStatus.SUBMITTED]
    with pytest.raises(KeyError):
        # This is a fixture guard: unknown statuses must not be treated as permissive.
        ResultGovernanceService._TRANSITIONS["UNKNOWN"]  # type: ignore[index]


def test_governance_error_is_a_value_error_for_thin_api_mapping() -> None:
    assert issubclass(ResultGovernanceError, ValueError)


def test_correction_categories_are_controlled() -> None:
    assert {item.value for item in CorrectionType} == {
        "OBJECTIVE_RESCORING",
        "WRITING_REASSESSMENT",
        "SPEAKING_REASSESSMENT",
        "TRANSCRIPTION_REASSESSMENT",
    }
