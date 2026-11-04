"""Independent result-demo prerequisites preserve existing immutable scenarios."""

import uuid

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.models import (
    CandidateExamAssignment,
    CandidateMediaAsset,
    ExamVersion,
    RubricVersion,
    SubjectiveAssessment,
    User,
)
from app.models.enums import RubricVersionStatus
from app.scripts.seed_demo import reconcile_demo
from app.scripts.seed_speaking_results_demo import (
    SPEAKING_RESULTS_CANDIDATE_EMAIL,
    SPEAKING_RESULTS_EXAM_NAME,
)


def test_speaking_results_fixture_is_distinct_draft_and_has_no_assessed_outcome(session):
    settings = Settings(_env_file=None, environment="development")
    ids = reconcile_demo(session, settings, enabled=True)
    assert ids["speaking_results_rubric_version_id"] != ids["speaking_human_rubric_version_id"]
    assert ids["speaking_results_item_version_id"] != ids["speaking_human_item_version_id"]
    rubric = session.get(RubricVersion, uuid.UUID(ids["speaking_results_rubric_version_id"]))
    assert rubric.status is RubricVersionStatus.DRAFT
    assert rubric.ai_assessment_policy == {"enabled": False, "mode": "HUMAN_ONLY"}
    assert rubric.evidence_policy["mode"] == "AUDIO"
    assert rubric.approved_at is rubric.published_at is None
    exam = session.get(ExamVersion, uuid.UUID(ids["speaking_results_exam_version_id"]))
    assert exam.exam.name == SPEAKING_RESULTS_EXAM_NAME
    for key in ("speaking_results_candidate_user_id", "speaking_results_pending_candidate_user_id"):
        candidate = session.get(User, uuid.UUID(ids[key]))
        for model in (CandidateExamAssignment, CandidateMediaAsset, SubjectiveAssessment):
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.candidate_user_id == candidate.id)
                )
                == 0
            )
    counts = {
        model: session.scalar(select(func.count()).select_from(model))
        for model in (User, RubricVersion, CandidateExamAssignment)
    }
    assert reconcile_demo(session, settings, enabled=True) == ids
    assert {
        model: session.scalar(select(func.count()).select_from(model)) for model in counts
    } == counts


def test_speaking_results_seed_does_not_claim_an_unrelated_identity(session):
    user = User(
        email=SPEAKING_RESULTS_CANDIDATE_EMAIL, normalized_email=SPEAKING_RESULTS_CANDIDATE_EMAIL
    )
    session.add(user)
    session.commit()
    with pytest.raises(RuntimeError, match="identity collision"):
        reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    session.rollback()
    assert session.get(User, user.id).password_credential is None
