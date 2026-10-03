import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.authorization import TenantContext
from app.exams.service import ExamStudioService, exam_checksum
from app.models import (
    StandardPack,
    StandardPackModule,
    StandardPackSection,
    StandardPackTaskSpecification,
)
from app.models.enums import (
    AssessmentClassification,
    AssessmentFamily,
    ExamVersionStatus,
    PackMediaRequirement,
    PackNavigationMode,
    PackResponseMode,
    QuestionBankRightsStatus,
    QuestionBankTaskType,
)


def test_exam_checksum_is_canonical_and_order_sensitive() -> None:
    first = {"standard_pack_version_id": "pack", "items": ["a", "b"]}
    same = {"items": ["a", "b"], "standard_pack_version_id": "pack"}
    changed = {"standard_pack_version_id": "pack", "items": ["b", "a"]}
    assert exam_checksum(first) == exam_checksum(same)
    assert exam_checksum(first) != exam_checksum(changed)


def test_blueprint_materialisation_is_idempotent(session) -> None:
    now = datetime.now(timezone.utc)
    pack = StandardPack(
        code=f"SYNTHETIC_EXAM_{uuid.uuid4().hex[:8]}",
        name="TEST FIXTURE — NOT OFFICIAL STANDARD",
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        created_at=now,
        updated_at=now,
    )
    from app.models import StandardPackVersion

    pack_version = StandardPackVersion(
        standard_pack=pack,
        version_label="1",
        status="ACTIVE",
        allowed_classifications=["PRACTICE"],
        supported_levels=["B1"],
        created_at=now,
    )
    module = StandardPackModule(
        pack_version=pack_version,
        code="READING",
        name="Reading",
        sequence=1,
        navigation_mode=PackNavigationMode.LINEAR,
        created_at=now,
    )
    section = StandardPackSection(module=module, code="A", name="A", sequence=1, created_at=now)
    StandardPackTaskSpecification(
        section=section,
        code="TASK_1",
        name="Task",
        sequence=1,
        task_type="MULTIPLE_CHOICE",
        min_items=1,
        max_items=1,
        response_mode=PackResponseMode.SELECT_ONE,
        media_requirement=PackMediaRequirement.NONE,
        created_at=now,
    )
    session.add(pack)
    session.flush()
    context = TenantContext(
        uuid.uuid4(), None, None, platform_permissions=frozenset({"exam.manage", "exam.read"})
    )
    exam = ExamStudioService.create_exam(
        session, context, code="SYNTHETIC", name="Synthetic", scope_type="PLATFORM_SHARED"
    )
    version = ExamStudioService.create_version(
        session, context, exam, version_label="1", standard_pack_version_id=pack_version.id
    )
    ExamStudioService.materialize_blueprint(session, context, version)
    ExamStudioService.materialize_blueprint(session, context, version)
    assert len(version.modules) == 1
    assert len(version.modules[0].sections) == 1
    assert len(version.modules[0].sections[0].tasks) == 1


def test_delivery_manifest_uses_answer_key_safe_item_projection() -> None:
    item_id = uuid.uuid4()
    task_id = uuid.uuid4()
    version = SimpleNamespace(
        id=uuid.uuid4(),
        checksum="c" * 64,
        status=ExamVersionStatus.PUBLISHED,
        standard_pack_version_id=uuid.uuid4(),
        classification=AssessmentClassification.PRACTICE,
        exam=SimpleNamespace(name="Synthetic", scope_type="PLATFORM_SHARED", organisation_id=None),
        modules=[
            SimpleNamespace(
                standard_pack_module_id=uuid.uuid4(),
                sections=[
                    SimpleNamespace(
                        standard_pack_section_id=uuid.uuid4(),
                        tasks=[
                            SimpleNamespace(
                                standard_pack_task_spec_id=task_id,
                                item_bindings=[
                                    SimpleNamespace(question_bank_item_version_id=item_id)
                                ],
                            )
                        ],
                    )
                ],
            )
        ],
    )
    item = SimpleNamespace(
        id=item_id,
        version_label="1",
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        task_type=QuestionBankTaskType.MULTIPLE_CHOICE,
        response_mode=PackResponseMode.SELECT_ONE,
        media_requirement=PackMediaRequirement.NONE,
        content={
            "kind": "MULTIPLE_CHOICE",
            "prompt": "Synthetic prompt",
            "options": [{"option_id": "a", "display_text": "A", "order": 0}],
            "correct_option_ids": ["a"],
        },
        rights_status=QuestionBankRightsStatus.PRUFAI_ORIGINAL,
    )

    class FakeSession:
        def get(self, model, identity):
            from app.models import QuestionBankItemVersion, StandardPackTaskSpecification

            if model is QuestionBankItemVersion:
                return item
            if model is StandardPackTaskSpecification:
                return SimpleNamespace(response_mode=PackResponseMode.SELECT_ONE)
            raise AssertionError(model)

    context = TenantContext(uuid.uuid4(), None, None, platform_permissions=frozenset({"exam.read"}))
    manifest = ExamStudioService.delivery_manifest(FakeSession(), context, version)
    serialized = str(manifest)
    assert "correct_option_ids" not in serialized
    assert "answer_key" not in serialized
