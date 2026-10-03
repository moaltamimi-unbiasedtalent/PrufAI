import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.authorization import AuthorizationService
from app.models import Base
from app.models.enums import (
    ItemScoreStatus,
    PackResponseMode,
    QuestionBankScoringPolicy,
    ScoringRunStatus,
)
from app.scoring.service import ScoringError, ScoringService, checksum, objective_award


def _item(mode: PackResponseMode = PackResponseMode.SELECT_ONE, metadata=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        response_mode=mode,
        scoring_policy=QuestionBankScoringPolicy.OBJECTIVE_KEYED,
        max_points=Decimal("1.25"),
        scoring_metadata=(
            metadata
            if metadata is not None
            else {"unanswered_policy": {"type": "ZERO", "points": "0"}}
        ),
        content={"correct_option_ids": ["b"]},
    )


def test_objective_select_one_is_exact_and_decimal_safe():
    item = _item()
    status, points = objective_award(item, {"option_id": "b"})
    assert status is ItemScoreStatus.SCORED
    assert points == Decimal("1.25")
    assert objective_award(item, {"option_id": "a"})[1] == Decimal("0")


def test_select_multiple_requires_explicit_declared_partial_policy():
    item = _item(PackResponseMode.SELECT_MULTIPLE)
    assert objective_award(item, {"option_ids": ["b"]})[1] == Decimal("1.25")
    partial = _item(
        PackResponseMode.SELECT_MULTIPLE,
        {
            "partial_credit_policy": {
                "type": "DECLARED_TABLE",
                "points_by_correct_count": {"1": "0.5"},
            }
        },
    )
    assert objective_award(partial, {"option_ids": ["b"]})[1] == Decimal("0.5")


def test_declared_table_is_bounded_and_rejects_invalid_configuration():
    bounded = _item(
        PackResponseMode.SELECT_MULTIPLE,
        {
            "unanswered_policy": {"type": "ZERO", "points": "0"},
            "partial_credit_policy": {
                "type": "DECLARED_TABLE",
                "points_by_correct_count": {"1": "2.0"},
            },
        },
    )
    with pytest.raises(ScoringError, match="out of bounds"):
        objective_award(bounded, {"option_ids": ["b"]})

    malformed = _item(
        PackResponseMode.SELECT_MULTIPLE,
        {
            "unanswered_policy": {"type": "ZERO", "points": "0"},
            "partial_credit_policy": {
                "type": "DECLARED_TABLE",
                "points_by_correct_count": {"one": "not-decimal"},
            },
        },
    )
    with pytest.raises(ScoringError, match="malformed"):
        objective_award(malformed, {"option_ids": ["b"]})


def test_constructed_and_unanswered_are_not_falsely_scored():
    item = _item(PackResponseMode.TEXT)
    item.scoring_policy = QuestionBankScoringPolicy.RUBRIC_REQUIRED
    assert objective_award(_item(), None) == (ItemScoreStatus.UNANSWERED, Decimal("0"))
    assert objective_award(item, {"text": "answer"}) == (ItemScoreStatus.NOT_SCORABLE, None)


def test_unanswered_requires_explicit_versioned_zero_policy():
    item = _item(metadata={"unanswered_policy": {"type": "ZERO", "points": "0"}})
    assert objective_award(item, None) == (ItemScoreStatus.UNANSWERED, Decimal("0"))

    missing = _item(metadata={})
    with pytest.raises(ScoringError, match="unanswered policy"):
        objective_award(missing, None)


def test_incorrect_answer_uses_keyed_policy():
    item = _item()
    assert objective_award(item, {"option_id": "a"}) == (
        ItemScoreStatus.SCORED,
        Decimal("0"),
    )


def test_snapshot_checksum_is_reproducible_and_mutable_attempts_are_blocked(monkeypatch):
    assert checksum({"b": 2, "a": 1}) == checksum({"a": 1, "b": 2})
    assert checksum({"a": 1}) != checksum({"a": 2})

    organisation_id = uuid.uuid4()

    class FakeSession:
        def get(self, _model, _identity):
            return SimpleNamespace(status="IN_PROGRESS", organisation_id=organisation_id)

        def scalar(self, _query):
            return None

    monkeypatch.setattr(AuthorizationService, "require_permission", lambda *_args, **_kwargs: None)
    context = SimpleNamespace(organisation_id=organisation_id, user_id=uuid.uuid4())
    with pytest.raises(ScoringError, match="INVALID_ATTEMPT_STATE"):
        ScoringService.score(FakeSession(), context, uuid.uuid4())


def test_failed_scoring_run_is_audited_without_response_content(monkeypatch):
    events = []

    class FakeSession:
        def flush(self):
            return None

    monkeypatch.setattr(
        "app.scoring.service._audit",
        lambda _session, _context, event, _entity, metadata: events.append((event, metadata)),
    )
    run = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        status=ScoringRunStatus.RUNNING,
        failure_category=None,
        failed_at=None,
        scoring_engine_name="prufai_objective_scoring",
        scoring_engine_version="1.0",
    )
    context = SimpleNamespace(user_id=uuid.uuid4(), organisation_id=uuid.uuid4())
    ScoringService.fail_run(FakeSession(), run, "SCORING_CONFIGURATION_INCOMPLETE", context)
    assert run.status is ScoringRunStatus.FAILED
    assert run.failure_category == "SCORING_CONFIGURATION_INCOMPLETE"
    assert events[0][0] == "scoring.run.failed"
    assert "payload" not in events[0][1]
    assert "answer_key" not in events[0][1]


def test_scoring_tables_are_registered(session):
    assert {
        "scoring_runs",
        "scoring_response_snapshots",
        "item_scores",
        "scoring_components",
        "assessment_results",
        "result_versions",
    } <= set(Base.metadata.tables)


def test_candidate_safe_result_projection_has_no_internal_or_response_fields():
    result = SimpleNamespace(id=uuid.uuid4(), attempt_id=uuid.uuid4())
    version = SimpleNamespace(
        id=uuid.uuid4(),
        status=SimpleNamespace(value="RELEASED"),
        raw_awarded_points=Decimal("1.0"),
        raw_maximum_points=Decimal("2.0"),
        outcome=SimpleNamespace(value="UNDETERMINED"),
        released_at=None,
    )
    serialized = str(ScoringService.safe_result(version, result))
    assert "answer_key" not in serialized
    assert "rule_provenance" not in serialized
    assert "payload" not in serialized
