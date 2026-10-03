from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.authorization import TenantContext
from app.models import Base
from app.models.enums import (
    AssessmentFamily,
    CandidateAttemptStatus,
    ExamVersionStatus,
    PackMediaRequirement,
    PackResponseMode,
    QuestionBankLifecycleStatus,
    QuestionBankRightsStatus,
    QuestionBankTaskType,
)
from app.runtime.service import CandidateRuntimeService, RuntimeExpired


def _context(user_id: uuid.UUID | None = None) -> TenantContext:
    return TenantContext(user_id or uuid.uuid4(), uuid.uuid4(), None)


def _attempt(
    status: CandidateAttemptStatus = CandidateAttemptStatus.IN_PROGRESS,
) -> SimpleNamespace:
    user_id = uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        candidate_user_id=user_id,
        organisation_id=uuid.uuid4(),
        status=status,
        deadline_at=None,
        runtime_plan={"modules": []},
        session=None,
        responses=[],
    )


def test_supported_typed_response_validation() -> None:
    item = {"content": {"options": [{"option_id": "a"}, {"option_id": "b"}]}}
    CandidateRuntimeService._validate_payload(item, "SELECT_ONE", {"option_id": "a"})
    CandidateRuntimeService._validate_payload(item, "SELECT_MULTIPLE", {"option_ids": ["a", "b"]})
    CandidateRuntimeService._validate_payload({}, "TEXT", {"text": "plain text"})


def test_response_validation_rejects_unknown_options_and_unbounded_text() -> None:
    item = {"content": {"options": [{"option_id": "a"}]}}
    with pytest.raises(ValueError, match="SELECT_ONE"):
        CandidateRuntimeService._validate_payload(item, "SELECT_ONE", {"option_id": "unknown"})
    with pytest.raises(ValueError, match="text"):
        CandidateRuntimeService._validate_payload({}, "TEXT", {"text": "x" * 4001})


def test_runtime_binding_lookup_is_attempt_scoped() -> None:
    binding_id = uuid.uuid4()
    attempt = _attempt()
    attempt.runtime_plan = {
        "modules": [
            {
                "sections": [
                    {
                        "tasks": [
                            {
                                "items": [{"binding_id": str(binding_id), "content": {}}],
                                "response_mode": "TEXT",
                            }
                        ]
                    }
                ]
            }
        ]
    }
    item, mode = CandidateRuntimeService._binding_payload(attempt, binding_id)
    assert item["binding_id"] == str(binding_id)
    assert mode == "TEXT"
    with pytest.raises(ValueError, match="not part"):
        CandidateRuntimeService._binding_payload(attempt, uuid.uuid4())


def test_expiry_is_server_authoritative_and_blocks_writes() -> None:
    attempt = _attempt()
    attempt.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    CandidateRuntimeService.expire_if_due(
        SimpleNamespace(flush=lambda: None), _context(attempt.candidate_user_id), attempt
    )
    assert attempt.status is CandidateAttemptStatus.EXPIRED


def test_expired_attempt_is_not_writable() -> None:
    attempt = _attempt(CandidateAttemptStatus.EXPIRED)
    assert attempt.status is not CandidateAttemptStatus.IN_PROGRESS
    with pytest.raises(RuntimeExpired):
        raise RuntimeExpired("attempt is not writable")


def test_assignment_window_uses_server_time_only() -> None:
    from app.models import CandidateExamAssignment
    from app.models.enums import CandidateAssignmentStatus

    now = datetime.now(timezone.utc)
    assignment = CandidateExamAssignment(
        organisation_id=uuid.uuid4(),
        candidate_user_id=uuid.uuid4(),
        exam_version_id=uuid.uuid4(),
        status=CandidateAssignmentStatus.ASSIGNED,
        available_from=now + timedelta(minutes=1),
        available_until=now + timedelta(minutes=2),
        maximum_attempts=1,
        created_at=now,
    )
    assert not CandidateRuntimeService._available(assignment, now)
    assert CandidateRuntimeService._available(assignment, now + timedelta(minutes=1, seconds=1))
    assert not CandidateRuntimeService._available(assignment, now + timedelta(minutes=3))


def test_candidate_ownership_is_required() -> None:
    attempt = _attempt()
    with pytest.raises(RuntimeError, match="ownership"):
        CandidateRuntimeService._require_candidate(_context(), attempt.candidate_user_id)


def test_candidate_runtime_tables_are_registered(session) -> None:
    table_names = set(Base.metadata.tables)
    assert {
        "candidate_exam_assignments",
        "candidate_exam_attempts",
        "candidate_exam_sessions",
        "candidate_responses",
        "candidate_response_revisions",
    } <= table_names


def test_attempt_statuses_are_controlled() -> None:
    assert {status.value for status in CandidateAttemptStatus} == {
        "CREATED",
        "READY",
        "IN_PROGRESS",
        "SUBMITTED",
        "EXPIRED",
        "CANCELLED",
        "INVALIDATED",
    }
    assert ExamVersionStatus.DRAFT.value == "DRAFT"


def test_runtime_plan_is_delivery_safe_and_rejects_unsupported_modes() -> None:
    item_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    spec = SimpleNamespace(response_mode=PackResponseMode.SELECT_ONE)
    item = SimpleNamespace(
        id=item_id,
        status=QuestionBankLifecycleStatus.ACTIVE,
        rights_status=QuestionBankRightsStatus.PRUFAI_ORIGINAL,
        rights_expires_at=None,
        version_label="1",
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        task_type=QuestionBankTaskType.MULTIPLE_CHOICE,
        response_mode=PackResponseMode.SELECT_ONE,
        media_requirement=PackMediaRequirement.NONE,
        content={
            "prompt": "Safe",
            "options": [{"option_id": "a", "display_text": "A"}],
            "correct_option_ids": ["a"],
            "solution": "hidden",
            "correct_option": "a",
            "correct_answer": "a",
            "answer_key": {"a": 1},
            "scoring_key": {"a": 1},
            "reviewer_notes": "internal",
            "ai_critique": "internal",
            "internal_validation": {"status": "PASS"},
            "rule_evaluation": {"status": "PASS"},
            "rights_notes": "internal",
            "private_s3_url": "https://s3.internal/object",
        },
    )
    module_id, section_id, task_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    version = SimpleNamespace(
        id=uuid.uuid4(),
        status=ExamVersionStatus.PUBLISHED,
        classification=SimpleNamespace(value="PRACTICE"),
        standard_pack_version_id=uuid.uuid4(),
        modules=[
            SimpleNamespace(
                id=module_id,
                standard_pack_module_id=module_id,
                sections=[
                    SimpleNamespace(
                        id=section_id,
                        standard_pack_section_id=section_id,
                        tasks=[
                            SimpleNamespace(
                                id=task_id,
                                standard_pack_task_spec_id=task_id,
                                item_bindings=[
                                    SimpleNamespace(
                                        id=binding_id, question_bank_item_version_id=item_id
                                    )
                                ],
                            )
                        ],
                    )
                ],
            )
        ],
    )

    class FakeSession:
        def get(self, model, identity):
            from app.models import (
                QuestionBankItemVersion,
                StandardPackModule,
                StandardPackSection,
                StandardPackTaskSpecification,
            )

            if model is QuestionBankItemVersion:
                return item
            if model is StandardPackTaskSpecification:
                return spec
            if model is StandardPackModule:
                return SimpleNamespace(
                    time_limit_seconds=None, navigation_mode=SimpleNamespace(value="LINEAR")
                )
            if model is StandardPackSection:
                return SimpleNamespace(
                    time_limit_seconds=None, navigation_mode=SimpleNamespace(value="LINEAR")
                )
            raise AssertionError(model)

    plan = CandidateRuntimeService._safe_plan(FakeSession(), version)
    serialized = json.dumps(plan)
    for protected in (
        "correct_option_ids",
        "correct_option",
        "correct_answer",
        "answer_key",
        "scoring_key",
        "solution",
        "reviewer_notes",
        "ai_critique",
        "internal_validation",
        "rule_evaluation",
        "rights_notes",
        "private_s3_url",
    ):
        assert protected not in serialized

    version.classification = SimpleNamespace(value="OFFICIAL_CERTIFICATION")
    with pytest.raises(ValueError, match="official certification"):
        CandidateRuntimeService._safe_plan(FakeSession(), version)

    version.classification = SimpleNamespace(value="PRACTICE")
    spec.response_mode = PackResponseMode.LIVE_ORAL
    with pytest.raises(ValueError, match="RUNTIME.UNSUPPORTED_RESPONSE_MODE"):
        CandidateRuntimeService._safe_plan(FakeSession(), version)
