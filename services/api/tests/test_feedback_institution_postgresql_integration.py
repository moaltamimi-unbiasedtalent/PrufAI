"""Guarded real PostgreSQL feedback intake from private Writing review and worker events."""

from __future__ import annotations

import json
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.evaluation import service as evaluation
from app.evaluation.disposable import assert_disposable_database
from app.evaluation.schemas import DatasetVersionInput, RunRequest
from app.main import app
from app.models import (
    AuditEvent,
    MembershipRole,
    OrganisationMembership,
    Role,
    RubricVersion,
    SubjectiveAssessmentVersion,
)
from app.models.feedback import FeedbackOperation, GovernedFeedback
from app.scripts.seed_ai_writing_demo import AI_WRITING_CANDIDATE_EMAIL
from app.scripts.seed_demo import PLATFORM_ADMIN_EMAIL
from tests.test_evaluation_foundation_postgresql_integration import dataset_body
from tests.test_expert_references_postgresql_integration import expert as expert
from tests.test_writing_execution_postgresql_integration import (
    WritingExecutionProof,
    _invalid,
    _provider_factory,
    writing_fixture,
)

REQUIRED = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRED,
    reason="Institution feedback acceptance requires guarded Alembic PostgreSQL",
)


@pytest.fixture
def feedback_writing(expert, monkeypatch):
    url = expert.engine.url.render_as_string(hide_password=False)
    name = expert.engine.url.database
    assert_disposable_database(url, name)
    for key in ("DATABASE_URL", "POSTGRES_INTEGRATION_URL"):
        monkeypatch.setenv(key, url)
    monkeypatch.setenv("PRUFAI_DISPOSABLE_DATABASE", name)
    monkeypatch.setenv("WRITING_TEST_ADAPTER_ENABLED", "1")
    generator = writing_fixture.__wrapped__()
    try:
        journey = next(generator)
        settings = app.dependency_overrides[get_settings]()
        settings.writing_test_adapter_enabled = True
        yield WritingExecutionProof(
            **{
                **journey.__dict__,
                "email": AI_WRITING_CANDIDATE_EMAIL,
                "candidate_id": uuid.UUID(journey.identifiers["ai_writing_candidate_user_id"]),
                "exam_version_id": journey.identifiers["ai_writing_exam_version_id"],
                "item_version_id": journey.identifiers["ai_writing_item_version_id"],
            },
            factory=sessionmaker(bind=journey.session.get_bind(), expire_on_commit=False),
            settings=settings,
            database_url=url,
        )
    finally:
        generator.close()


def _payload(source, category):
    return {
        "operation_id": str(uuid.uuid4()),
        "source": source,
        "category": category,
        "severity": "MEDIUM",
    }


def _assert_redaction(p, item, private, candidate_id):
    with p.factory() as session:
        row = session.get(GovernedFeedback, uuid.UUID(item["id"]))
        assert row is not None
        operation = session.scalar(
            select(FeedbackOperation).where(FeedbackOperation.entity_id == row.id)
        )
        assert operation is not None
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "feedback.received",
                AuditEvent.entity_id == str(row.id),
            )
        )
        assert audit is not None and audit.reason is None
        assert audit.request_id == str(operation.id)
        assert audit.organisation_id == row.organisation_id
        assert audit.new_state_hash == operation.checksum
        assert audit.metadata_json == {"status": "RECEIVED", "production_activation": False}
        serialized = json.dumps([row.source, operation.state, audit.metadata_json, item])
        assert private not in serialized
        assert str(candidate_id) not in serialized
        for key in (
            "candidate_text",
            "audio_sha256",
            "storage_key",
            "validated_proposal",
            "rationale",
        ):
            assert key not in serialized


def test_authenticated_writing_reviewer_flags_exact_version_and_preserves_scope(feedback_writing):
    p = feedback_writing
    private = "Original synthetic candidate Writing evidence for feedback " + uuid.uuid4().hex
    state = p.submit(candidate_text=private)
    queued = p.job(p.queue(state))
    p.execute(p.claim(), provider_factory=_provider_factory())
    with p.factory() as session:
        version = session.get(SubjectiveAssessmentVersion, queued.assessment_version_id)
        assert version is not None and version.status.value == "AI_PROPOSED"
        version_id, version_checksum = str(version.id), version.checksum
    source = {
        "kind": "REVIEWER_FLAG",
        "artifact_type": "SUBJECTIVE_ASSESSMENT_VERSION",
        "artifact": {"id": version_id, "checksum": version_checksum},
    }
    body = _payload(source, "RUBRIC_ALIGNMENT")
    path = p.org + "/feedback"
    reviewer = p.login("reviewer@demo.prufai.local")
    item = p.request("POST", path, reviewer, expected=201, json=body)
    assert item["status"] == "RECEIVED" and item["source_kind"] == "REVIEWER_FLAG"
    assert item["source_id"] == version_id and item["source_checksum"] == version_checksum
    assert item["privacy_class"] == "INSTITUTION_RESTRICTED" and item["requires_reference"] is True
    assert item["evidence_class"] == "TECHNICAL_TEST" and item["execution_class"] == "TEST_ONLY"
    assert p.request("POST", path, reviewer, expected=201, json=body) == item
    p.request("POST", path, reviewer, expected=409, json={**body, "severity": "HIGH"})
    p.request("POST", path, reviewer, expected=422, json={**body, "notes": private})
    p.request(
        "POST",
        path,
        reviewer,
        expected=409,
        json=_payload(
            {**source, "artifact": {"id": version_id, "checksum": "0" * 64}}, "RUBRIC_ALIGNMENT"
        ),
    )
    foreign_scope = str(p.foreign_org_id)
    foreign = p.login(p.foreign_email, organisation_id=foreign_scope)
    foreign_path = f"/api/v1/organisations/{foreign_scope}/feedback"
    for other in (body, _payload(source, "RUBRIC_ALIGNMENT")):
        p.request("POST", foreign_path, foreign, expected=403, json=other)
    query = "?source_id=" + version_id
    assert p.request("GET", path + query, reviewer)["items"] == [item]
    assert p.request("GET", foreign_path + query, foreign)["items"] == []
    p.request("GET", path + query, state["candidate"], expected=403)
    p.request(
        "POST",
        path + f"/{item['id']}/decision",
        state["candidate"],
        expected=403,
        json={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": item["checksum"],
            "action": "TRIAGE",
            "reason_code": "SOURCE_REVIEWED",
        },
    )
    platform = p.login(PLATFORM_ADMIN_EMAIL, tenant=False)
    public = p.request("GET", "/api/v1/platform/control-center/feedback/items", platform)
    assert item["id"] not in {entry["id"] for entry in public["items"]}
    _assert_redaction(p, item, private, p.candidate_id)


def test_operational_feedback_requires_exact_terminal_worker_failure(feedback_writing):
    p = feedback_writing
    private = "Original synthetic Writing evidence from a failed worker " + uuid.uuid4().hex
    queued = p.job(p.queue(p.submit(candidate_text=private)))
    p.execute(
        p.claim(),
        provider_factory=_provider_factory(
            mutation=lambda output: _invalid(output, "missing-rationale"),
        ),
    )
    job = p.job(queued.id)
    assert job.status == "FAILED" and job.failure_category == "INVALID_PROVIDER_OUTPUT"
    p.assert_no_handoff(job)
    with p.factory() as session:
        events = list(
            session.scalars(select(AuditEvent).where(AuditEvent.entity_id == str(job.id)))
        )
        failure = next(event for event in events if event.event_type == "writing_execution.failed")
        other = next(event for event in events if event.event_type != "writing_execution.failed")
        failure_id, other_id = str(failure.id), str(other.id)
    path = p.org + "/feedback"
    reviewer = p.login("reviewer@demo.prufai.local")
    for event_id in (other_id, str(uuid.uuid4())):
        p.request(
            "POST",
            path,
            reviewer,
            expected=409,
            json=_payload(
                {"kind": "OPERATIONAL_FAILURE", "event_id": event_id},
                "RUNTIME_RELIABILITY",
            ),
        )
    body = _payload({"kind": "OPERATIONAL_FAILURE", "event_id": failure_id}, "RUNTIME_RELIABILITY")
    item = p.request("POST", path, reviewer, expected=201, json=body)
    assert item["source_id"] == failure_id and item["source_kind"] == "OPERATIONAL_FAILURE"
    assert item["skill"] == "WRITING" and item["execution_class"] == "TEST_ONLY"
    assert item["evidence_class"] == "TECHNICAL_TEST" and item["requires_reference"] is False
    with p.factory() as session:
        stored = session.get(GovernedFeedback, uuid.UUID(item["id"]))
        assert stored is not None
        assert stored.source["job_id"] == str(job.id)
        assert stored.source["failure_category"] == job.failure_category
        assert stored.source["attempt_count"] == job.attempt_count
    _assert_redaction(p, item, private, p.candidate_id)


def test_worker_receipt_candidate_cannot_intake_read_or_decide_own_evaluation_feedback(
    feedback_writing, expert
):
    p = feedback_writing
    state = p.submit()
    queued = p.job(p.queue(state))
    p.execute(p.claim(), provider_factory=_provider_factory())
    job = p.job(queued.id)
    assert job.status == "COMPLETED"
    with p.factory() as session:
        reviewer_role = session.scalar(select(Role).where(Role.key == "INSTITUTION_ADMIN"))
        assert reviewer_role is not None
        evaluator = OrganisationMembership(
            organisation_id=job.organisation_id, user_id=expert.actors["creator"]
        )
        session.add(evaluator)
        session.flush()
        candidate = session.scalar(
            select(OrganisationMembership).where(
                OrganisationMembership.organisation_id == job.organisation_id,
                OrganisationMembership.user_id == job.candidate_user_id,
            )
        )
        assert candidate is not None
        for membership in (candidate, evaluator):
            session.add(MembershipRole(membership_id=membership.id, role_id=reviewer_role.id))
        session.flush()
        rubric = session.get(RubricVersion, job.rubric_version_id)
        assert rubric is not None
        revision = {"response_revision_id": str(job.candidate_response_revision_id)}
        case = {
            "case_id": "original-synthetic-writing-provenance-difference",
            "kind": "WRITING",
            "split": "VALIDATION",
            "input": {
                "language": "de",
                "modality": "TEXT",
                "text": state["text"],
                "evidence_identity": {**revision, "response_checksum": job.response_checksum},
            },
            # Both identity subsets point to the actual worker receipt. The
            # measured provenance check rejects their deliberate disagreement.
            "reference": {
                "label_kind": "SYNTHETIC_EXPECTATION",
                "evidence_identity": revision,
                "criteria": [
                    {"code": c["code"], "max_points": str(c["max_points"])} for c in rubric.criteria
                ],
            },
            "execution_job_id": str(job.id),
        }
        dataset = dataset_body(cases=[case])
        dataset.update(skill="WRITING", organisation_id=str(job.organisation_id))
        creator = expert.context(session, "creator")
        approver = expert.context(session, "approver")
        version = evaluation.create_dataset_version(
            session, creator, DatasetVersionInput.model_validate(dataset)
        )
        for action, actor in (("submit", creator), ("approve", approver), ("publish", creator)):
            evaluation.transition_dataset(
                session, actor, version.id, action, version.checksum, "synthetic:independent-review"
            )
        run = evaluation.execute_run(
            session,
            creator,
            RunRequest.model_validate(
                {
                    "operation_id": str(uuid.uuid4()),
                    "dataset_version_id": str(version.id),
                    "dataset_checksum": version.checksum,
                    "configuration": {
                        **{
                            key: job.source_identity[key]
                            for key in (
                                "standard_pack_version_id",
                                "standard_pack_checksum",
                                "rubric_version_id",
                                "rubric_checksum",
                                "question_bank_item_version_id",
                                "item_checksum",
                            )
                        },
                        **{
                            key: job.execution_snapshot[key]
                            for key in (
                                "model_profile_id",
                                "model_version",
                                "prompt_version_id",
                                "agent_version_id",
                                "workflow_version_id",
                                "profile_version",
                            )
                        },
                        "dataset_split": "VALIDATION",
                        "execution_adapter": "PINNED_EXECUTION_REPLAY_V1",
                        "evaluation_method": "DETERMINISTIC_REFERENCE_COMPARISON",
                        "evaluation_method_version": "1",
                    },
                }
            ),
        )
        (result,) = evaluation.verify_run(session, run)
        assert run.hard_gate == result.result["checks"]["immutable_provenance"] == "FAIL"
        assert result.execution_reference["execution_job_id"] == str(job.id)
        source = {
            "kind": "EVALUATION_FAILURE",
            "run": {"id": str(run.id), "checksum": run.checksum},
            "result": {"id": str(result.id), "checksum": result.checksum},
            "failed_check": "immutable_provenance",
        }
        session.commit()
    path = p.org + "/feedback"
    candidate_reviewer = p.login(p.email)
    body = _payload(source, "EVIDENCE_PROVENANCE")
    denied = p.request("POST", path, candidate_reviewer, expected=403, json=body)
    assert denied["detail"] == "FEEDBACK_EVALUATION_CANDIDATE_DENIED"
    reviewer = p.login("reviewer@demo.prufai.local")
    item = p.request("POST", path, reviewer, expected=201, json=body)
    assert item["source_id"] == source["result"]["id"]
    assert item["failed_check"] == "immutable_provenance"
    assert {pin["type"] for pin in item["artifact_pins"]} >= {"PROMPT_VERSION", "WORKFLOW_VERSION"}
    for method, url, kwargs in (
        ("GET", path + "?source_id=" + item["source_id"], {}),
        (
            "POST",
            path + f"/{item['id']}/decision",
            {
                "json": {
                    "operation_id": str(uuid.uuid4()),
                    "expected_checksum": item["checksum"],
                    "action": "TRIAGE",
                    "reason_code": "SOURCE_REVIEWED",
                }
            },
        ),
    ):
        denied = p.request(method, url, candidate_reviewer, expected=403, **kwargs)
        assert denied["detail"] == "FEEDBACK_EVALUATION_CANDIDATE_DENIED"
    _assert_redaction(p, item, state["text"], p.candidate_id)
