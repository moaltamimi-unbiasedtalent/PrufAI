"""Focused domain checks; real PostgreSQL/normal-JWT acceptance is separate."""

from __future__ import annotations

import copy
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.authorization import TenantContext, build_tenant_context
from app.config import Settings
from app.models import AuditEvent, RubricVersion, User, WritingGovernanceOperation
from app.models.enums import RubricVersionStatus
from app.scripts.seed_demo import reconcile_demo
from app.writing_assessment.governance import HumanWritingService, RubricGovernanceService
from app.writing_assessment.service import RubricError, SubjectiveAssessmentService


def rubric():
    return SimpleNamespace(
        criteria=[{"code": "TASK", "max_points": "4"}],
        performance_levels=[{"code": "BASIC"}, {"code": "CLEAR"}],
        evidence_policy={"required": False},
    )


def entry(**changes):
    return {
        "criterion_code": "TASK",
        "level": "CLEAR",
        "points": "3",
        "rationale": "The requested message is understandable.",
        "evidence": [],
        **changes,
    }


def test_human_criteria_are_bounded_decimal_values_and_exact_unicode_evidence():
    entries, values = HumanWritingService.validate_entries(
        rubric(), "Grüße 👋", [entry(evidence=[{"quote": "üße 👋", "start": 2, "end": 7}])]
    )
    assert values == {"TASK": Decimal("3")}
    assert entries[0]["points"] == "3.0000"
    assert entries[0]["evidence"][0]["quote"] == "üße 👋"
    assert "confidence" not in entries[0]


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [entry(), entry()],
        [entry(criterion_code="UNKNOWN")],
        [entry(points="NaN")],
        [entry(points="Infinity")],
        [entry(points="4.0001")],
        [entry(points="-1")],
        [entry(points="1.00001")],
        [entry(level="UNKNOWN")],
        [entry(rationale="")],
        [entry(rationale="x" * 2001)],
        [entry(evidence=[{"quote": "invented", "start": 0, "end": 8}])],
        [entry(evidence=[{"quote": "Grüße", "start": False, "end": 5}])],
        [entry(evidence=[{"storage_key": "private"}])],
    ],
)
def test_human_criteria_reject_invalid_scores_incomplete_coverage_and_fabricated_evidence(entries):
    with pytest.raises(RubricError):
        HumanWritingService.validate_entries(rubric(), "Grüße 👋", entries)


def test_required_human_evidence_is_not_satisfied_by_rationale():
    bound = rubric()
    bound.evidence_policy = {"required": True, "minimum_references": 1}
    with pytest.raises(RubricError, match="required response evidence"):
        HumanWritingService.validate_entries(bound, "Grüße 👋", [entry()])


@pytest.mark.parametrize(
    "policy",
    [
        {},
        {"mode": "AI_ASSISTED", "enabled": True},
        {"mode": "HUMAN_ONLY", "enabled": True},
        {"mode": "HUMAN_ONLY"},
        {"enabled": False},
    ],
)
def test_human_workflow_requires_explicit_opt_in_and_is_never_an_ai_fallback(policy):
    with pytest.raises(RubricError, match="explicitly authorize HUMAN_ONLY"):
        HumanWritingService.assert_human_mode(RubricVersion(ai_assessment_policy=policy))
    HumanWritingService.assert_human_mode(
        RubricVersion(ai_assessment_policy={"mode": "HUMAN_ONLY", "enabled": False})
    )


def test_ai_proposal_route_cannot_reclassify_a_human_origin():
    context = TenantContext(
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), frozenset({"subjective.assess"})
    )
    session = SimpleNamespace(get=lambda *_args: SimpleNamespace(assessment_origin="HUMAN"))
    with pytest.raises(RubricError, match="human assessments cannot accept AI"):
        SubjectiveAssessmentService.propose(
            session, context, uuid.uuid4(), {}, ai_run_id=uuid.uuid4()
        )


def test_authorized_rubric_action_is_audited_without_audit_read_permission(session):
    ids = reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    admin = session.scalar(select(User).where(User.email == "admin@demo.prufai.local"))
    context = TenantContext(
        admin.id,
        uuid.UUID(ids["organisation_id"]),
        None,
        frozenset({"rubric.manage"}),
    )
    version = session.get(RubricVersion, uuid.UUID(ids["writing_rubric_version_id"]))
    operation_id = uuid.uuid4()
    RubricGovernanceService.request_review(
        session,
        context,
        version.id,
        operation_id=operation_id,
        expected_checksum=version.checksum,
    )
    session.commit()
    event = session.scalar(select(AuditEvent).where(AuditEvent.request_id == str(operation_id)))
    assert event is not None and event.event_type == "rubric.review_requested"
    assert event.actor_id == admin.id and event.new_state_hash == version.checksum
    assert event.metadata_json == {"operation_id": str(operation_id)}


def test_rubric_read_api_returns_persisted_language_family_and_levels(session):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.auth.api import Principal, get_principal
    from app.db import get_db_session
    from app.writing_assessment.api import writing_router

    ids = reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    org = uuid.UUID(ids["organisation_id"])
    admin = session.scalar(select(User).where(User.email == "admin@demo.prufai.local"))
    version = session.get(RubricVersion, uuid.UUID(ids["writing_rubric_version_id"]))
    principal = Principal(
        admin, SimpleNamespace(id=uuid.uuid4()), build_tenant_context(session, admin.id, org)
    )
    application = FastAPI()
    application.include_router(writing_router)
    application.dependency_overrides[get_db_session] = lambda: session
    application.dependency_overrides[get_principal] = lambda: principal
    with TestClient(application) as client:
        detail = client.get(
            f"/api/v1/organisations/{org}/rubrics/{version.rubric_id}/versions/{version.id}"
        )
        listing = client.get(f"/api/v1/organisations/{org}/rubrics")
    assert detail.status_code == listing.status_code == 200
    listed_version = next(
        entry
        for rubric in listing.json()
        for entry in rubric["versions"]
        if entry["id"] == str(version.id)
    )
    expected = {
        "language": version.language,
        "assessment_family": version.assessment_family.value,
        "cefr_levels": version.cefr_levels,
    }
    for response in (detail.json(), listed_version):
        assert {key: response[key] for key in expected} == expected


def _published(session):
    ids = reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    org = uuid.UUID(ids["organisation_id"])
    admin = session.scalar(select(User).where(User.email == "admin@demo.prufai.local"))
    reviewer = session.scalar(select(User).where(User.email == "reviewer@demo.prufai.local"))
    author_context = build_tenant_context(session, admin.id, org)
    reviewer_context = build_tenant_context(session, reviewer.id, org)
    version = session.get(RubricVersion, uuid.UUID(ids["writing_rubric_version_id"]))
    operation = uuid.uuid4()
    checksum = version.checksum
    RubricGovernanceService.request_review(
        session, author_context, version.id, operation_id=operation, expected_checksum=checksum
    )
    session.commit()
    audit_count = session.scalar(select(func.count()).select_from(AuditEvent))
    RubricGovernanceService.request_review(
        session, author_context, version.id, operation_id=operation, expected_checksum=checksum
    )
    session.commit()
    assert session.scalar(select(func.count()).select_from(AuditEvent)) == audit_count
    assert session.scalar(select(func.count()).select_from(WritingGovernanceOperation)) == 1
    with pytest.raises(PermissionError, match="independent reviewer"):
        RubricGovernanceService.approve(
            session,
            author_context,
            version.id,
            operation_id=uuid.uuid4(),
            expected_checksum=checksum,
        )
    session.rollback()
    RubricGovernanceService.approve(
        session, reviewer_context, version.id, operation_id=uuid.uuid4(), expected_checksum=checksum
    )
    session.commit()
    RubricGovernanceService.publish(
        session, reviewer_context, version.id, operation_id=uuid.uuid4(), expected_checksum=checksum
    )
    session.commit()
    session.expire_all()
    assert version.status is RubricVersionStatus.PUBLISHED
    return version


@pytest.mark.parametrize("mutation", ["nested", "replacement", "downgrade", "delete"])
def test_published_rubric_history_rejects_nested_replacement_downgrade_and_deletion(
    session, mutation
):
    version = _published(session)
    original = copy.deepcopy(version.criteria)
    if mutation == "nested":
        version.criteria[0]["max_points"] = "999"
    elif mutation == "replacement":
        version.criteria = [{**version.criteria[0], "max_points": "999"}]
    elif mutation == "downgrade":
        version.status = RubricVersionStatus.DRAFT
    else:
        session.delete(version)
    with pytest.raises(ValueError, match="immutable"):
        session.commit()
    session.rollback()
    session.expire_all()
    assert version.criteria == original
    assert version.status is RubricVersionStatus.PUBLISHED


def test_legacy_ai_review_correction_builds_immutable_approved_successor(session):
    """Compatibility of an existing legacy proposal; no inference is performed."""
    from datetime import datetime, timezone

    from app.models import (
        HumanReviewRequest,
        SubjectiveAssessment,
        SubjectiveAssessmentVersion,
        SubjectiveCriterionAssessment,
    )
    from app.models.enums import (
        HumanReviewStatus,
        SubjectiveAssessmentStatus,
        SubjectiveConfidence,
        SubjectiveCriterionStatus,
    )
    from app.runtime.service import CandidateRuntimeService

    ids = reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    rubric_version = _published(session)
    org = uuid.UUID(ids["organisation_id"])
    admin = session.scalar(select(User).where(User.email == "admin@demo.prufai.local"))
    reviewer = session.scalar(select(User).where(User.email == "reviewer@demo.prufai.local"))
    candidate = session.get(User, uuid.UUID(ids["writing_candidate_user_id"]))
    admin_context = build_tenant_context(session, admin.id, org)
    candidate_context = build_tenant_context(session, candidate.id, org)
    assignment = CandidateRuntimeService.create_assignment(
        session,
        admin_context,
        candidate_user_id=candidate.id,
        exam_version_id=uuid.UUID(ids["writing_exam_version_id"]),
    )
    attempt = CandidateRuntimeService.create_attempt(session, candidate_context, assignment.id)
    CandidateRuntimeService.start(session, candidate_context, attempt.id)
    item = attempt.runtime_plan["modules"][0]["sections"][0]["tasks"][0]["items"][0]
    binding_id = uuid.UUID(item["binding_id"])
    CandidateRuntimeService.save_response(
        session,
        candidate_context,
        attempt.id,
        binding_id,
        payload={"text": "Ich möchte einen neuen Termin vereinbaren."},
        expected_revision=0,
        client_operation_id=uuid.uuid4(),
    )
    CandidateRuntimeService.submit(session, candidate_context, attempt.id)
    # Historical UNSPECIFIED origin is retained. This proposal envelope models
    # existing stored review data; it is not a provider or execution claim.
    now = datetime.now(timezone.utc)
    assessment = SubjectiveAssessment(
        attempt_id=attempt.id,
        exam_version_id=attempt.exam_version_id,
        exam_version_item_binding_id=binding_id,
        question_bank_item_version_id=uuid.UUID(ids["writing_item_version_id"]),
        candidate_user_id=candidate.id,
        organisation_id=org,
        created_at=now,
    )
    session.add(assessment)
    session.flush()
    response = attempt.responses[0]
    revision = response.revisions[0]
    source = SubjectiveAssessmentVersion(
        subjective_assessment_id=assessment.id,
        version_number=1,
        assessment_origin="UNSPECIFIED",
        attempt_id=attempt.id,
        exam_version_id=attempt.exam_version_id,
        exam_version_item_binding_id=binding_id,
        question_bank_item_version_id=uuid.UUID(ids["writing_item_version_id"]),
        candidate_response_id=response.id,
        candidate_response_revision_id=revision.id,
        rubric_version_id=rubric_version.id,
        standard_pack_version_id=rubric_version.standard_pack_version_id,
        status=SubjectiveAssessmentStatus.AI_PROPOSED,
        response_checksum=revision.payload_checksum,
        rubric_checksum=rubric_version.checksum,
        checksum="1" * 64,
        created_at=now,
    )
    session.add(source)
    session.flush()
    source.proposed_score = Decimal("2")
    source.ai_proposal = {"legacy_test_proposal": True}
    for criterion in rubric_version.criteria:
        source.criterion_assessments.append(
            SubjectiveCriterionAssessment(
                criterion_code=criterion["code"],
                proposed_level="BASIC",
                proposed_points=Decimal("1"),
                confidence=SubjectiveConfidence.MEDIUM,
                evidence=[],
                rationale="Legacy fixture proposal.",
                uncertainties=[],
                disagreement={},
                status=SubjectiveCriterionStatus.PROPOSED,
            )
        )
    session.add(
        HumanReviewRequest(
            review_type="SUBJECTIVE_ASSESSMENT",
            entity_type="SUBJECTIVE_ASSESSMENT_VERSION",
            entity_id=source.id,
            status=HumanReviewStatus.PENDING,
            requested_by=reviewer.id,
            requested_at=datetime.now(timezone.utc),
            reviewed_checksum=source.checksum,
        )
    )
    session.commit()
    original_checksum = source.checksum
    original_proposal = copy.deepcopy(source.ai_proposal)
    approved = SubjectiveAssessmentService.approve(
        session,
        admin_context,
        source.id,
        corrections={
            criterion["code"]: {"approved_points": "2", "approved_level": "CLEAR"}
            for criterion in rubric_version.criteria
        },
    )
    session.commit()
    session.expire_all()
    assert approved.id != source.id and approved.supersedes_version_id == source.id
    assert approved.version_number == source.version_number + 1
    assert approved.status is SubjectiveAssessmentStatus.APPROVED
    assert approved.assessment_origin == source.assessment_origin == "UNSPECIFIED"
    assert approved.approved_score == Decimal("4")
    assert len(approved.criterion_assessments) == 2
    assert all(row.approved_points == Decimal("2") for row in approved.criterion_assessments)
    assert source.status is SubjectiveAssessmentStatus.SUPERSEDED
    assert source.checksum == original_checksum and source.ai_proposal == original_proposal
    assert all(
        row.proposed_points == Decimal("1") and row.approved_points is None
        for row in source.criterion_assessments
    )
    assert session.get(SubjectiveAssessmentVersion, approved.id).ai_run_id is None
    with pytest.raises(ValueError, match="immutable"):
        source.ai_proposal = {"rewritten": True}
        session.commit()
    session.rollback()
    with pytest.raises(ValueError, match="immutable"):
        approved.criterion_assessments[0].approved_points = Decimal("99")
        session.commit()
    session.rollback()
