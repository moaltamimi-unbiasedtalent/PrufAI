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
