import uuid
from types import SimpleNamespace

import pytest

from app.authorization import TenantContext
from app.models import (
    AssessmentResult,
    Base,
    CandidateExamAttempt,
    ExamVersion,
    ResultVersion,
    ScoringRun,
)
from app.models.enums import (
    AssessmentClassification,
    CandidateAttemptStatus,
    ResultVersionStatus,
    ScoringRunStatus,
)
from app.result_release.service import ResultReleaseError, ResultReleaseService


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.added = []

    def get(self, model, _identity):
        return self.rows.get(model)

    def scalar(self, _query):
        entity = _query.column_descriptions[0].get("entity")
        if entity in self.rows:
            return self.rows[entity]
        return None

    def add(self, value):
        value.id = value.id or uuid.uuid4()
        self.added.append(value)

    def flush(self):
        return None


def _context(org_id, user_id):
    return TenantContext(
        user_id=user_id,
        organisation_id=org_id,
        membership_id=uuid.uuid4(),
        permissions=frozenset({"result.release", "audit.read"}),
    )


def test_release_tables_are_registered():
    assert {
        "result_releases",
        "statements_of_results",
        "certification_authorizations",
        "certificate_records",
    } <= set(Base.metadata.tables)


def _release_rows(classification=AssessmentClassification.PRACTICE):
    org_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    result_id = uuid.uuid4()
    run_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    exam_id = uuid.uuid4()
    version = SimpleNamespace(
        id=uuid.uuid4(),
        result_id=result_id,
        scoring_run_id=run_id,
        status=ResultVersionStatus.RELEASED,
        checksum="r1",
    )
    result = SimpleNamespace(
        id=result_id,
        organisation_id=org_id,
        candidate_user_id=candidate_id,
        attempt_id=attempt_id,
        exam_version_id=exam_id,
    )
    run = SimpleNamespace(id=run_id, status=ScoringRunStatus.COMPLETED, checksum="s1")
    attempt = SimpleNamespace(id=attempt_id, status=CandidateAttemptStatus.SUBMITTED)
    exam = SimpleNamespace(
        id=exam_id,
        classification=classification,
        standard_pack_version_id=uuid.uuid4(),
    )
    return (
        org_id,
        candidate_id,
        version,
        FakeSession(
            {
                ResultVersion: version,
                AssessmentResult: result,
                ScoringRun: run,
                CandidateExamAttempt: attempt,
                ExamVersion: exam,
            }
        ),
    )


def test_release_pins_exact_result_and_scoring_provenance(monkeypatch):
    org_id, _candidate_id, version, session = _release_rows()
    context = _context(org_id, uuid.uuid4())
    monkeypatch.setattr(
        "app.result_release.service.AuditService.record", lambda *_args, **_kwargs: None
    )

    release = ResultReleaseService.release(session, context, version.id, operation_id="op-1")

    assert release.result_version_id == version.id
    assert release.scoring_run_id == session.rows[ScoringRun].id
    assert release.standard_pack_version_id == session.rows[ExamVersion].standard_pack_version_id
    assert release.checksum


def test_official_certification_is_denied_without_explicit_authorization():
    org_id, _candidate_id, version, session = _release_rows(
        AssessmentClassification.OFFICIAL_CERTIFICATION
    )
    with pytest.raises(ResultReleaseError, match="CERTIFICATION.NOT_AUTHORIZED"):
        ResultReleaseService.release(
            session, _context(org_id, uuid.uuid4()), version.id, operation_id="op-1"
        )
