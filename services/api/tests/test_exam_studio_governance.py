from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.authorization import AuthorizationError, TenantContext
from app.exams import service as exam_service
from app.exams.service import ExamStudioService, ValidationResult
from app.models import Exam, ExamVersion
from app.models.enums import (
    AssessmentClassification,
    ExamVersionStatus,
    QuestionBankLifecycleStatus,
    QuestionBankRightsStatus,
    RuleCategory,
    RuleSeverity,
)


def _context(
    *permissions: str,
    organisation_id: uuid.UUID | None = None,
    platform: bool = False,
) -> TenantContext:
    return TenantContext(
        uuid.uuid4(),
        organisation_id,
        None,
        permissions=frozenset(permissions if not platform else ()),
        platform_permissions=frozenset(permissions if platform else ()),
    )


def _version(status: ExamVersionStatus = ExamVersionStatus.DRAFT) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        checksum="a" * 64,
        standard_pack_version_id=uuid.uuid4(),
        timing_override={},
        navigation_override={},
        modules=[],
        classification=AssessmentClassification.PRACTICE,
        exam=SimpleNamespace(scope_type="PLATFORM_SHARED", organisation_id=None, name="Synthetic"),
    )


def test_item_assignment_revalidates_exact_eligible_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = _version()
    task_id = uuid.uuid4()
    task = SimpleNamespace(
        id=task_id,
        standard_pack_task_spec_id=uuid.uuid4(),
        section=SimpleNamespace(module=SimpleNamespace(exam_version_id=version.id)),
        item_bindings=[],
    )
    spec = SimpleNamespace(id=task.standard_pack_task_spec_id, max_items=1)

    class FakeSession:
        def get(self, model, identity):
            return task if identity == task_id else spec

        def scalar(self, query):
            return None

        def flush(self):
            return None

    monkeypatch.setattr(
        exam_service.QuestionBankService,
        "eligible_bank",
        lambda *args, **kwargs: [SimpleNamespace(id=uuid.uuid4())],
    )
    with pytest.raises(ValueError, match="not eligible"):
        ExamStudioService.assign_item(
            FakeSession(),
            _context("exam.manage", "question_bank.read", platform=True),
            version,
            task_id=task_id,
            item_version_id=uuid.uuid4(),
            slot_index=0,
        )


def test_rights_and_certification_are_independent_blocking_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = _version()
    version.classification = AssessmentClassification.OFFICIAL_CERTIFICATION
    pack = SimpleNamespace(
        id=version.standard_pack_version_id,
        allowed_classifications=["OFFICIAL_CERTIFICATION"],
        modules=[],
        rule_bindings=[],
        configuration_checksum="p" * 64,
    )

    class FakeSession:
        def get(self, model, identity):
            return pack

        def flush(self):
            return None

    version.timing_override = {"seconds": 10}
    version.navigation_override = {"allow_back": False}
    result = ExamStudioService.validate(
        FakeSession(), _context("exam.read", platform=True), version
    )
    assert result.status == "FAIL"
    assert any(issue.code == "EXAM.CLASSIFICATION.CERTIFICATION_GUARD" for issue in result.issues)
    assert any(issue.code == "EXAM.TIMING.OVERRIDE_NOT_AUTHORIZED" for issue in result.issues)
    assert any(issue.code == "EXAM.NAVIGATION.OVERRIDE_NOT_AUTHORIZED" for issue in result.issues)

    module_id = uuid.uuid4()
    pack_module_id = uuid.uuid4()
    section_id = uuid.uuid4()
    task_id = uuid.uuid4()
    spec = SimpleNamespace(
        id=uuid.uuid4(),
        section=SimpleNamespace(id=section_id),
        min_items=1,
        max_items=1,
    )
    item = SimpleNamespace(
        id=uuid.uuid4(),
        status=QuestionBankLifecycleStatus.ACTIVE,
        rights_status=QuestionBankRightsStatus.UNKNOWN,
        rights_expires_at=None,
    )
    task = SimpleNamespace(
        id=task_id,
        standard_pack_task_spec_id=spec.id,
        ordinal=0,
        section=SimpleNamespace(id=section_id),
        item_bindings=[SimpleNamespace(question_bank_item_version_id=item.id, slot_index=0)],
    )
    module = SimpleNamespace(
        id=module_id,
        standard_pack_module_id=pack_module_id,
        ordinal=0,
        sections=[
            SimpleNamespace(
                id=section_id,
                standard_pack_section_id=section_id,
                ordinal=0,
                module=SimpleNamespace(
                    id=module_id,
                    standard_pack_module_id=pack_module_id,
                ),
                tasks=[task],
            )
        ],
    )
    version.modules = [module]
    pack_module = SimpleNamespace(id=pack_module_id, pack_version=pack)
    pack.modules = [SimpleNamespace(id=pack_module_id, required=True)]

    class RightsSession(FakeSession):
        def get(self, model, identity):
            if model is exam_service.StandardPackModule:
                return pack_module
            if model is exam_service.StandardPackTaskSpecification:
                return spec
            if model is exam_service.QuestionBankItemVersion:
                return item
            return super().get(model, identity)

    monkeypatch.setattr(
        exam_service.QuestionBankService,
        "validate_pack_compatibility",
        staticmethod(lambda *args, **kwargs: ValidationResult("PASS", (), "x" * 64)),
    )
    rights_result = ExamStudioService.validate(
        RightsSession(), _context("exam.read", platform=True), version
    )
    assert any(issue.code == "EXAM.RIGHTS.BLOCKED" for issue in rights_result.issues), [
        issue.code for issue in rights_result.issues
    ]


def test_bound_rules_fail_deterministically(monkeypatch: pytest.MonkeyPatch) -> None:
    version = _version()
    definition = SimpleNamespace(id=uuid.uuid4(), code="EXAM.RULE", category=RuleCategory.STRUCTURE)
    rule_version = SimpleNamespace(
        id=uuid.uuid4(),
        version_label="1",
        checksum="r" * 64,
        severity=RuleSeverity.BLOCKING,
        expression={
            "op": "EQ",
            "left": {"fact": "exam.classification"},
            "right": {"value": "MOCK"},
        },
        failure_code="EXAM.RULE.BLOCKED",
        failure_message_template="classification mismatch",
        evidence_bindings=[],
        definition=definition,
    )
    pack = SimpleNamespace(
        id=version.standard_pack_version_id,
        allowed_classifications=["PRACTICE"],
        modules=[],
        rule_bindings=[SimpleNamespace(required=True, rule_version=rule_version)],
        configuration_checksum="p" * 64,
    )

    class FakeSession:
        def get(self, model, identity):
            return pack

        def flush(self):
            return None

    result = ExamStudioService.validate(
        FakeSession(), _context("exam.read", platform=True), version
    )
    assert result.status == "FAIL"
    assert any(issue.origin == "RULE" for issue in result.issues)


def test_review_checksum_and_published_mutability_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    version = _version(ExamVersionStatus.HUMAN_REVIEW)
    review = SimpleNamespace(
        status="PENDING",
        reviewed_checksum="a" * 64,
        decided_by=None,
        decided_at=None,
    )

    class FakeSession:
        def scalar(self, query):
            return review

        def flush(self):
            return None

    monkeypatch.setattr(
        ExamStudioService,
        "validate",
        staticmethod(lambda *args, **kwargs: ValidationResult("PASS", (), version.checksum)),
    )
    version.checksum = "b" * 64
    with pytest.raises(ValueError, match="checksum"):
        ExamStudioService.transition(
            FakeSession(),
            _context("exam.review", platform=True),
            version,
            ExamVersionStatus.APPROVED,
        )
    with pytest.raises(ValueError, match="immutable"):
        ExamStudioService._assert_mutable(_version(ExamVersionStatus.PUBLISHED))


def test_current_and_as_of_resolution_use_effective_dates(session) -> None:
    now = datetime.now(timezone.utc)
    exam = Exam(
        code="RESOLUTION",
        name="Synthetic",
        scope_type="PLATFORM_SHARED",
        created_at=now,
    )
    first = ExamVersion(
        exam=exam,
        version_label="1",
        status=ExamVersionStatus.SUPERSEDED,
        standard_pack_version_id=uuid.uuid4(),
        classification=AssessmentClassification.PRACTICE,
        origin_type="HUMAN_AUTHORED",
        effective_from=now - timedelta(days=10),
        effective_until=now + timedelta(days=1),
        timing_override={},
        navigation_override={},
        created_at=now - timedelta(days=10),
        checksum="1" * 64,
    )
    second = ExamVersion(
        exam=exam,
        version_label="2",
        status=ExamVersionStatus.ACTIVE,
        standard_pack_version_id=uuid.uuid4(),
        classification=AssessmentClassification.PRACTICE,
        origin_type="HUMAN_AUTHORED",
        effective_from=now,
        timing_override={},
        navigation_override={},
        created_at=now,
        checksum="2" * 64,
    )
    session.add_all([exam, first, second])
    session.flush()
    context = _context("exam.read", platform=True)
    assert ExamStudioService.resolve_current(session, context, exam.id, now).id == second.id
    assert (
        ExamStudioService.resolve_as_of(session, context, exam.id, now - timedelta(days=5)).id
        == first.id
    )


def test_tenant_scope_and_ai_proposal_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    organisation = uuid.uuid4()
    with pytest.raises((ValueError, AuthorizationError)):
        ExamStudioService.create_exam(
            SimpleNamespace(add=lambda value: None, flush=lambda: None),
            _context("exam.manage", organisation_id=organisation),
            code="SHARED",
            name="Shared",
            scope_type="PLATFORM_SHARED",
            organisation_id=None,
        )

    version = _version()
    assigned = []
    monkeypatch.setattr(
        ExamStudioService,
        "assign_item",
        staticmethod(
            lambda *args, **kwargs: assigned.append(kwargs) or SimpleNamespace(id=uuid.uuid4())
        ),
    )
    monkeypatch.setattr(
        ExamStudioService,
        "assembly_gaps",
        staticmethod(lambda *args: [{"code": "MISSING_ELIGIBLE_ITEM"}]),
    )
    result = ExamStudioService.propose_ai_assembly(
        SimpleNamespace(),
        _context("exam.manage", platform=True),
        version,
        [{"task_id": str(uuid.uuid4()), "item_version_id": str(uuid.uuid4()), "slot_index": 0}],
        ai_run_id=uuid.uuid4(),
        ai_step_id=uuid.uuid4(),
    )
    assert result["status"] == "DRAFT"
    assert result["gaps"][0]["code"] == "MISSING_ELIGIBLE_ITEM"
    assert assigned[0]["origin_type"].value == "AI_PROPOSED"
