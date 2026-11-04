"""Scorer selection, governed scale and public-contract units; real audio is separate."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.authorization import TenantContext
from app.models import (
    CandidateResponse,
    CandidateResponseRevision,
    ExamVersionItemBinding,
    ItemScore,
    OrganisationMembership,
    QuestionBankItem,
    QuestionBankItemVersion,
    ResultVersion,
    SubjectiveAssessment,
    SubjectiveAssessmentVersion,
)
from app.models.enums import (
    ExamOriginType,
    PackMediaRequirement,
    PackResponseMode,
    QuestionBankLifecycleStatus,
    QuestionBankOriginType,
    QuestionBankRightsStatus,
    QuestionBankScoringPolicy,
    QuestionBankSkill,
    QuestionBankTaskType,
)
from app.models.enums import SubjectiveAssessmentStatus as Status
from app.scoring.service import ScoringError, ScoringService
from app.speaking_assessment.governance import HumanSpeakingService
from app.writing_assessment.service import RubricError
from tests.integration_support.result_correction_scenario import (
    build_governed_result_correction_scenario,
)


def version(number, status, *, root=None, binding=None, predecessor=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        version_number=number,
        status=status,
        assessment_origin="HUMAN",
        candidate_media_asset_id=uuid.uuid4(),
        subjective_assessment_id=root or uuid.uuid4(),
        exam_version_item_binding_id=binding or uuid.uuid4(),
        supersedes_version_id=predecessor,
    )


@pytest.mark.parametrize(
    "state",
    [
        Status.CREATED,
        Status.REVIEW_REQUIRED,
        Status.IN_REVIEW,
        Status.REJECTED,
        Status.CHANGES_REQUESTED,
    ],
)
def test_initial_unapproved_subjective_evidence_never_becomes_scoreable(state):
    assert ScoringService._current_approved_subjective_versions([version(1, state)]) == {}


@pytest.mark.parametrize(
    "state",
    [
        Status.CREATED,
        Status.REVIEW_REQUIRED,
        Status.IN_REVIEW,
        Status.REJECTED,
        Status.CHANGES_REQUESTED,
    ],
)
def test_pending_or_rejected_descendant_never_falls_back_to_older_approval(state):
    approved = version(1, Status.APPROVED)
    successor = version(
        2,
        state,
        root=approved.subjective_assessment_id,
        binding=approved.exam_version_item_binding_id,
        predecessor=approved.id,
    )
    for rows in ([approved, successor], [successor, approved]):
        with pytest.raises(ScoringError, match="UNRESOLVED"):
            ScoringService._current_approved_subjective_versions(rows)


def test_authoritative_descendant_requires_one_approval_and_exact_previous_version():
    first = version(1, Status.SUPERSEDED)
    rejected = version(
        2,
        Status.REJECTED,
        root=first.subjective_assessment_id,
        binding=first.exam_version_item_binding_id,
        predecessor=first.id,
    )
    approved = version(
        3,
        Status.APPROVED,
        root=first.subjective_assessment_id,
        binding=first.exam_version_item_binding_id,
        predecessor=rejected.id,
    )
    assert ScoringService._current_approved_subjective_versions([approved, first, rejected]) == {
        str(first.exam_version_item_binding_id): approved
    }
    first.status = Status.APPROVED
    with pytest.raises(ScoringError, match="multiple approved"):
        ScoringService._current_approved_subjective_versions([first, rejected, approved])
    first.status = Status.SUPERSEDED
    approved.supersedes_version_id = uuid.uuid4()
    with pytest.raises(ScoringError, match="lineage"):
        ScoringService._current_approved_subjective_versions([first, rejected, approved])


def test_ambiguous_roots_or_version_numbers_fail_closed():
    first = version(1, Status.APPROVED)
    duplicate = version(
        1,
        Status.APPROVED,
        root=first.subjective_assessment_id,
        binding=first.exam_version_item_binding_id,
    )
    with pytest.raises(ScoringError, match="duplicate version"):
        ScoringService._current_approved_subjective_versions([first, duplicate])
    duplicate.version_number = 2
    duplicate.subjective_assessment_id = uuid.uuid4()
    with pytest.raises(ScoringError, match="conflicting assessment roots"):
        ScoringService._current_approved_subjective_versions([first, duplicate])


@pytest.mark.parametrize("state", [Status.CREATED, Status.REVIEW_REQUIRED, Status.IN_REVIEW])
def test_approved_descendant_cannot_hide_an_unresolved_active_ancestor(state):
    ancestor = version(1, state)
    approved = version(
        2,
        Status.APPROVED,
        root=ancestor.subjective_assessment_id,
        binding=ancestor.exam_version_item_binding_id,
        predecessor=ancestor.id,
    )
    with pytest.raises(ScoringError, match="unresolved active ancestor"):
        ScoringService._current_approved_subjective_versions([ancestor, approved])


def test_writing_ancestor_lineage_is_left_for_exact_correction_validation():
    first = version(1, Status.SUPERSEDED)
    rejected = version(
        2,
        Status.REJECTED,
        root=first.subjective_assessment_id,
        binding=first.exam_version_item_binding_id,
        predecessor=first.id,
    )
    approved = version(
        3,
        Status.APPROVED,
        root=first.subjective_assessment_id,
        binding=first.exam_version_item_binding_id,
        predecessor=first.id,
    )
    approved.candidate_media_asset_id = None
    assert ScoringService._current_approved_subjective_versions([first, rejected, approved]) == {
        str(first.exam_version_item_binding_id): approved
    }
    approved.candidate_media_asset_id = uuid.uuid4()
    with pytest.raises(ScoringError, match="Speaking successor"):
        ScoringService._current_approved_subjective_versions([first, rejected, approved])


def rubric(method="SUM", **policy):
    return SimpleNamespace(
        criteria=[
            {"code": "A", "max_points": "3", "weight": "0.5"},
            {"code": "B", "max_points": "3", "weight": "0.5"},
        ],
        aggregation_policy={"method": method, **policy},
    )


def test_speaking_maximum_is_the_exact_published_aggregation_scale():
    assert HumanSpeakingService.scoring_maximum(rubric()) == Decimal("6")
    assert HumanSpeakingService.scoring_maximum(rubric("WEIGHTED_SUM")) == Decimal("3")
    assert HumanSpeakingService.scoring_maximum(
        rubric("DECLARED_MAPPING", mapping={"A:0,B:0": "0", "A:3,B:3": "12.2500"})
    ) == Decimal("12.2500")


@pytest.mark.parametrize(
    "mapping",
    [
        {},
        {"A:3": "6"},
        {"A:9,B:3": "6"},
        {"A:1.00001,B:3": "6"},
        {"A:3,B:3": "NaN"},
        {"A:3,B:3": "6.00001"},
        {"A:3,B:3": "-1"},
        {"UNKNOWN:3,B:3": "6"},
    ],
)
def test_invalid_declared_mapping_does_not_invent_a_score_scale(mapping):
    with pytest.raises((RubricError, ArithmeticError)):
        HumanSpeakingService.scoring_maximum(rubric("DECLARED_MAPPING", mapping=mapping))


@pytest.mark.parametrize("maximum", [Decimal("0"), Decimal("5"), Decimal("100")])
def test_speaking_item_maximum_cannot_silently_rescale_a_published_rubric(monkeypatch, maximum):
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        exam_version_id=uuid.uuid4(),
        organisation_id=uuid.uuid4(),
        candidate_user_id=uuid.uuid4(),
    )
    item = SimpleNamespace(id=uuid.uuid4(), skill=QuestionBankSkill.SPEAKING)
    source = SimpleNamespace(
        attempt_id=attempt.id,
        exam_version_id=attempt.exam_version_id,
        question_bank_item_version_id=item.id,
        assessment=attempt,
        exam_version_item_binding_id=uuid.uuid4(),
        candidate_response_id=uuid.uuid4(),
        candidate_response_revision_id=uuid.uuid4(),
        response_checksum="a" * 64,
        rubric_version_id=uuid.uuid4(),
    )
    response = {
        "binding_id": str(source.exam_version_item_binding_id),
        "response_id": str(source.candidate_response_id),
        "response_revision_id": str(source.candidate_response_revision_id),
        "payload_checksum": source.response_checksum,
    }
    monkeypatch.setattr(HumanSpeakingService, "validate_approved_evidence", lambda *_args: {})
    session = SimpleNamespace(get=lambda *_args: rubric())
    with pytest.raises(RubricError, match="maximum does not match"):
        HumanSpeakingService.approved_scoring_evidence(
            session, source, attempt, item, response, maximum
        )


def test_speaking_public_projection_strips_private_audio_and_review_fields():
    component = {
        "skill": "SPEAKING",
        "assessment_origin": "HUMAN",
        "assessment_mode": "HUMAN_ONLY",
        "evidence_mode": "AUDIO",
        "approved_score": "5.0000",
        "maximum_points": "6.0000",
        "candidate_media_asset_id": "PRIVATE_AUDIO_ID",
        "audio_sha256": "PRIVATE_SHA",
        "rationale": "PRIVATE_RATIONALE",
        "evidence": [{"start_ms": 0, "end_ms": 1000}],
    }
    run = SimpleNamespace(
        item_scores=[
            SimpleNamespace(
                exam_version_item_binding_id=uuid.uuid4(), scoring_metadata={"component": component}
            )
        ]
    )
    public = ScoringService.assessment_components(run)
    assert public[0]["skill"] == "SPEAKING" and public[0]["evidence_mode"] == "AUDIO"
    assert "PRIVATE" not in str(public) and "start_ms" not in str(public)
    component["evidence_mode"] = "TRANSCRIPT"
    assert ScoringService.assessment_components(run) == []


@pytest.mark.parametrize("missing_speaking", [False, True])
def test_actual_scorer_keeps_objective_writing_and_speaking_components(
    session, monkeypatch, missing_speaking
):
    """SQLite engine orchestration only; audio/approval validation is a stubbed seam.

    The required PostgreSQL/MinIO test supplies genuine audio and human approvals.
    This unit checks that the existing scorer persists every mixed component and
    cannot declare a complete result when one required component is absent.
    """
    scenario = build_governed_result_correction_scenario(session)
    attempt = scenario.attempt
    task = scenario.exam_version.modules[0].sections[0].tasks[0]
    original = task.item_bindings[0]
    now = datetime.now(timezone.utc)
    bindings = [original]
    for index, skill in enumerate((QuestionBankSkill.WRITING, QuestionBankSkill.SPEAKING), 1):
        item = QuestionBankItem(
            code="UNIT-MIXED-" + uuid.uuid4().hex,
            scope_type="ORGANISATION_PRIVATE",
            organisation_id=scenario.organisation.id,
            status=QuestionBankLifecycleStatus.PUBLISHED,
            created_by_user_id=scenario.reviewer.id,
        )
        session.add(item)
        session.flush()
        versioned_item = QuestionBankItemVersion(
            item_id=item.id,
            version_label="1",
            status=QuestionBankLifecycleStatus.PUBLISHED,
            assessment_family=scenario.objective_item_version_i1.assessment_family,
            language="de",
            skill=skill,
            task_type=QuestionBankTaskType.EXTENDED_WRITING
            if index == 1
            else QuestionBankTaskType.ORAL_RESPONSE,
            response_mode=PackResponseMode.TEXT if index == 1 else PackResponseMode.AUDIO,
            media_requirement=PackMediaRequirement.NONE,
            content={"kind": "UNIT_ONLY"},
            scoring_policy=QuestionBankScoringPolicy.RUBRIC_REQUIRED,
            max_points=Decimal("6"),
            scoring_metadata={},
            rights_status=QuestionBankRightsStatus.PRUFAI_ORIGINAL,
            origin_type=QuestionBankOriginType.HUMAN_AUTHORED,
            created_at=now,
            checksum=uuid.uuid4().hex * 2,
        )
        session.add(versioned_item)
        session.flush()
        binding = ExamVersionItemBinding(
            exam_version_task_id=task.id,
            slot_index=index,
            question_bank_item_version_id=versioned_item.id,
            origin_type=ExamOriginType.HUMAN_AUTHORED,
            created_at=now,
        )
        session.add(binding)
        session.flush()
        bindings.append(binding)
        payload = {"text": "unit Writing"} if index == 1 else {"media_asset_id": str(uuid.uuid4())}
        response = CandidateResponse(
            attempt_id=attempt.id,
            exam_version_item_binding_id=binding.id,
            current_revision=1,
            current_payload=payload,
            finalized_at=now,
        )
        session.add(response)
        session.flush()
        revision = CandidateResponseRevision(
            candidate_response_id=response.id,
            attempt_id=attempt.id,
            revision_number=1,
            client_operation_id=uuid.uuid4(),
            payload=payload,
            payload_checksum=uuid.uuid4().hex * 2,
            received_at=now,
        )
        session.add(revision)
        session.flush()
        if missing_speaking and skill is QuestionBankSkill.SPEAKING:
            continue
        assessment = SubjectiveAssessment(
            attempt_id=attempt.id,
            exam_version_id=attempt.exam_version_id,
            exam_version_item_binding_id=binding.id,
            question_bank_item_version_id=versioned_item.id,
            candidate_user_id=attempt.candidate_user_id,
            organisation_id=attempt.organisation_id,
            created_at=now,
        )
        session.add(assessment)
        session.flush()
        session.add(
            SubjectiveAssessmentVersion(
                subjective_assessment_id=assessment.id,
                version_number=1,
                assessment_origin="HUMAN",
                attempt_id=attempt.id,
                exam_version_id=attempt.exam_version_id,
                exam_version_item_binding_id=binding.id,
                question_bank_item_version_id=versioned_item.id,
                candidate_response_id=response.id,
                candidate_response_revision_id=revision.id,
                rubric_version_id=scenario.rubric_version.id,
                standard_pack_version_id=scenario.standard_pack_version.id,
                status=Status.APPROVED,
                response_checksum=revision.payload_checksum,
                rubric_checksum=scenario.rubric_version.checksum,
                approved_score=Decimal(3 + index),
                checksum=uuid.uuid4().hex * 2,
                created_at=now,
            )
        )
        session.flush()
    attempt.runtime_plan = {
        "standard_pack_version_id": str(scenario.standard_pack_version.id),
        "modules": [
            {
                "module_id": str(task.section.module.id),
                "sections": [
                    {
                        "section_id": str(task.section.id),
                        "tasks": [
                            {
                                "task_id": str(task.id),
                                "items": [
                                    {
                                        "binding_id": str(binding.id),
                                        "item_version_id": str(
                                            binding.question_bank_item_version_id
                                        ),
                                    }
                                    for binding in bindings
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    session.flush()
    session.expire(attempt, ["responses"])
    validated = []

    def evidence(_session, source, _attempt, item, _response):
        validated.append(item.skill)
        return {
            "subjective_assessment_checksum": source.checksum,
            "component": {
                "skill": item.skill.value,
                "assessment_origin": "HUMAN",
                "assessment_mode": "HUMAN_ONLY",
                "evidence_mode": "AUDIO" if item.skill is QuestionBankSkill.SPEAKING else "TEXT",
                "approved_score": str(source.approved_score),
                "maximum_points": "6",
                "subjective_assessment_version_id": str(source.id),
            },
        }

    monkeypatch.setattr(ScoringService, "_human_scoring_evidence", staticmethod(evidence))
    membership = session.scalar(
        select(OrganisationMembership).where(
            OrganisationMembership.user_id == scenario.reviewer.id,
            OrganisationMembership.organisation_id == scenario.organisation.id,
        )
    )
    context = TenantContext(
        scenario.reviewer.id,
        scenario.organisation.id,
        membership.id,
        frozenset({"scoring.run", "result.read"}),
    )
    run = ScoringService.score(session, context, attempt.id)
    session.flush()
    scores = list(session.scalars(select(ItemScore).where(ItemScore.scoring_run_id == run.id)))
    result = session.scalar(select(ResultVersion).where(ResultVersion.scoring_run_id == run.id))
    assert len(scores) == 3 and result.raw_maximum_points == Decimal("13")
    assert result.raw_awarded_points == Decimal("4" if missing_speaking else "9")
    assert result.status.value == ("INCOMPLETE" if missing_speaking else "REVIEW_REQUIRED")
    assert result.outcome.value == "UNDETERMINED"
    assert set(validated) == (
        {QuestionBankSkill.WRITING}
        if missing_speaking
        else {QuestionBankSkill.WRITING, QuestionBankSkill.SPEAKING}
    )
