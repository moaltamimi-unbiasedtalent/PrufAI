"""Required P1B acceptance with real auth, Alembic and a private MinIO bucket."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.evaluation.disposable import assert_disposable_database
from app.main import app
from app.models import (
    AuditEvent,
    CandidateExamAttempt,
    CandidateMediaAsset,
    CandidateResponseRevision,
)
from app.models.enums import CandidateAttemptStatus
from app.models.privacy import PrivacyCase, PrivacyPreview
from app.privacy import service
from app.privacy.schemas import Category
from app.scripts.seed_ai_writing_demo import AI_WRITING_CANDIDATE_EMAIL
from app.scripts.seed_demo import PASSWORD, PLATFORM_ADMIN_EMAIL
from tests.alembic_helpers import repository_alembic_head
from tests.integration_support.privacy_cases import privacy_actors
from tests.test_integrated_writing_postgresql_integration import _human_journey, _release, _score
from tests.test_recorded_audio_postgresql_minio_integration import audio as audio
from tests.test_transcription_execution_postgresql_minio_integration import ExecutionProof
from tests.test_writing_execution_postgresql_integration import WritingExecutionProof
from tests.test_writing_foundation_postgresql_integration import writing as writing

pytestmark = pytest.mark.skipif(
    os.getenv("PRUFAI_REQUIRE_PRIVACY_INTEGRATION") != "1",
    reason="The dedicated privacy gate requires a fresh disposable database and private MinIO",
)


@pytest.fixture(autouse=True)
def guard():
    url = os.environ["POSTGRES_INTEGRATION_URL"]
    name = assert_disposable_database(url)
    assert name.startswith("prufai_eval2_privacy_")
    assert os.environ["DATABASE_URL"] == url
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as conn:
        assert conn.execute(
            "SELECT current_database(), version_num FROM alembic_version"
        ).fetchone() == (
            name,
            repository_alembic_head(),
        )


def _case(fixture, subject, org, *, categories=None):
    settings = app.dependency_overrides[get_settings]()
    settings.privacy_test_discovery_enabled = True
    settings.database_url = os.environ["DATABASE_URL"]
    reviewer_email = f"privacy-{uuid4()}@example.test"
    grant = privacy_actors(
        fixture.session,
        settings,
        organisation_id=org,
        subject_id=subject,
        reviewer_email=reviewer_email,
        password=PASSWORD,
    )
    admin = fixture.login("admin@demo.prufai.local")
    reviewer = fixture.login(reviewer_email)
    path = f"/api/v1/privacy/organisations/{org}/cases"
    body = {
        "operation_id": str(uuid4()),
        "subject_id": str(subject),
        "purpose": "ASSESSMENT_DELIVERY",
        "request_type": "ERASURE_REQUEST",
        "request_receipt": str(uuid4()),
        "categories": categories or list(Category),
    }

    def call(method, suffix, headers=reviewer, *, status=200, payload=None):
        response = fixture.client.request(method, path + suffix, headers=headers, json=payload)
        assert response.status_code == status, (response.status_code, response.text)
        assert response.headers["Cache-Control"] == "no-store"
        return response.json()

    created = call("POST", "", admin, payload=body)
    assert call("POST", "", admin, payload=body) == created
    selected = "/" + created["id"]
    verify = {
        "operation_id": str(uuid4()),
        "case_checksum": created["case_checksum"],
        "verification_receipt": str(uuid4()),
        "controller_context": "UNASSESSED",
    }
    blocked = {
        "operation_id": str(uuid4()),
        "case_checksum": created["case_checksum"],
        "scope_checksum": created["case_checksum"],
        "policy_pins": {},
    }
    assert (
        call("POST", selected + "/preview", status=409, payload=blocked)["detail"]
        == "IDENTITY_NOT_VERIFIED"
    )
    assert (
        call("POST", selected + "/verify", admin, status=403, payload=verify)["detail"]
        == "PRIVACY_ACCESS_DENIED"
    )
    verified = call("POST", selected + "/verify", payload=verify)
    assert verified["identity"] == "VERIFIED_TEST_ONLY"
    assert call("POST", selected + "/verify", payload=verify) == verified
    pins = {
        **blocked,
        "operation_id": str(uuid4()),
        "case_checksum": verified["case_checksum"],
        "scope_checksum": verified["scope_checksum"],
    }
    detail = call("GET", selected)
    pins["policy_pins"] = {
        category: draft["checksum"] for category, draft in detail["policies"].items()
    }
    return call, selected, pins, reviewer, admin, body, grant


def test_real_audio_transcription_and_governed_preview(audio, monkeypatch):
    org = UUID(audio.ids["organisation_id"])
    audio.settings.speech_transcription_test_adapter_enabled = True
    monkeypatch.setenv("SPEECH_TRANSCRIPTION_TEST_ADAPTER_ENABLED", "1")
    proof = ExecutionProof(**audio.__dict__, database_url=os.environ["DATABASE_URL"])
    state = proof.submitted()
    asset = proof.asset(state["asset"])
    source_key, source_sha = asset.storage_key, asset.sha256
    subject = asset.candidate_user_id
    proof.profile()
    job = proof.job(proof.request(state))
    proof.execute(proof.claim())
    assert proof.job({"id": str(job.id)}).status == "READY"
    call, selected, pins, reviewer, admin, body, grant = _case(audio, subject, org)
    preview_path = selected + "/preview"

    assert call("GET", "")["items"][0]["id"] == selected[1:]
    call("GET", selected, audio.login(audio.users[1].email), status=403)
    call("GET", selected, audio.login(PLATFORM_ADMIN_EMAIL), status=403)
    call("POST", "", admin, status=403, payload={**body, "subject_id": str(audio.users[1].id)})
    call("GET", "/" + str(uuid4()), status=403)
    foreign = audio.login(audio.users[2].email, org=str(audio.foreign_org))
    call("POST", preview_path, foreign, status=403, payload=pins)
    call("POST", preview_path, status=409, payload={**pins, "case_checksum": "0" * 64})

    policy = {
        "operation_id": str(uuid4()),
        "case_checksum": pins["case_checksum"],
        "category": "ORIGINAL_AUDIO",
        "jurisdiction": "DE",
        "controller_context": "UNASSESSED",
        "start_event": "UNASSESSED",
        "hold_applicability": "UNASSESSED",
        "backup_treatment": "UNASSESSED",
        "processor_treatment": "UNASSESSED",
    }
    approval = call(
        "POST", selected + "/policy-drafts", status=422, payload={**policy, "status": "APPROVED"}
    )
    assert approval == {"detail": "INVALID_PRIVACY_REQUEST"}
    draft = call("POST", selected + "/policy-drafts", payload=policy)
    assert draft["status"] == "DRAFT"
    assert call("POST", preview_path, status=409, payload=pins)["detail"] == "STALE_POLICY"
    pins["policy_pins"] = {"ORIGINAL_AUDIO": draft["checksum"]}
    now = datetime.now(UTC)
    hold = {
        "operation_id": str(uuid4()),
        "case_checksum": pins["case_checksum"],
        "status": "REPORTED_HOLD",
        "basis": "LEGAL_CLAIM",
        "basis_reference": str(uuid4()),
        "decision_receipt": str(uuid4()),
        "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "review_due_at": (now + timedelta(hours=1)).isoformat(),
    }
    call("POST", selected + "/hold-reviews", admin, status=403, payload=hold)
    pins["hold_checksum"] = call("POST", selected + "/hold-reviews", payload=hold)["checksum"]
    call("POST", preview_path, status=409, payload={**pins, "hold_checksum": None})

    tables = (CandidateExamAttempt, CandidateMediaAsset, CandidateResponseRevision)
    before = [audio.session.scalar(select(func.count()).select_from(table)) for table in tables]
    original_head = audio.storage.client.head_object(Bucket=audio.storage.bucket, Key=source_key)
    calls = []
    from app.knowledge.storage import S3CompatibleObjectStorage

    original_probe = S3CompatibleObjectStorage.probe_metadata

    def probe(storage, key, size):
        calls.append(key)
        return original_probe(storage, key, size)

    def no_bytes(*args, **kwargs):
        pytest.fail("privacy preview attempted to download or write an object")

    with monkeypatch.context() as patch:
        patch.setattr(S3CompatibleObjectStorage, "probe_metadata", probe)
        patch.setattr(S3CompatibleObjectStorage, "get", no_bytes)
        patch.setattr(S3CompatibleObjectStorage, "get_bounded", no_bytes)
        patch.setattr(S3CompatibleObjectStorage, "put", no_bytes)
        result = call("POST", preview_path, payload=pins)
        assert call("POST", preview_path, payload=pins) == result
    assert calls == [source_key, source_key]
    assert result["eligibility"] == "NOT_DETERMINED" and not result["physical_erasure_authorized"]
    assert result["hold"] == "REPORTED_HOLD"
    categories = {row["category"]: row for row in result["categories"]}
    assert categories["ORIGINAL_AUDIO"]["object_classes"] == {
        "ORIGINAL_CANDIDATE_AUDIO": {"OBJECT_KNOWN": 1}
    }
    assert categories["TRANSCRIPTIONS"]["tables"]["speech_transcription_versions"] == 1
    assert (
        categories["EVALUATION"]["count"] is None
        and categories["EVALUATION"]["classification"] == "UNSUPPORTED"
    )
    assert categories["OBJECT_BACKUPS"]["coverage"] == "NOT_VERIFIED"
    assert categories["ORIGINAL_AUDIO"]["retention_policy"] == "UNCONFIGURED"
    for value in (source_key, source_sha, "original-technical-tone.wav", audio.users[0].email):
        assert value not in str(result)
    audio.session.expire_all()
    assert [
        audio.session.scalar(select(func.count()).select_from(table)) for table in tables
    ] == before
    head = audio.storage.client.head_object(Bucket=audio.storage.bucket, Key=source_key)
    assert (head["ETag"], head["ContentLength"]) == (
        original_head["ETag"],
        original_head["ContentLength"],
    )
    receipt = audio.session.get(PrivacyPreview, UUID(result["id"]))
    assert receipt is not None and receipt.checksum == result["checksum"]
    with pytest.raises(DBAPIError, match="PRIVACY_EVIDENCE_IMMUTABLE"):
        audio.session.execute(
            text("UPDATE privacy_previews SET checksum = 'changed' WHERE id = :id"),
            {"id": receipt.id},
        )
    audio.session.rollback()
    assert (
        audio.session.scalar(
            select(func.count())
            .select_from(PrivacyPreview)
            .where(PrivacyPreview.case_id == UUID(selected[1:]))
        )
        == 1
    )
    events = audio.session.scalars(
        select(AuditEvent).where(AuditEvent.event_type.like("privacy.%"))
    ).all()
    assert {event.event_type for event in events} >= {
        "privacy.case_created",
        "privacy.identity_verified_test_only",
        "privacy.scope_verified",
        "privacy.legal_review_required",
        "privacy.discovery_requested",
        "privacy.preview_recorded",
        "privacy.policy_draft_created",
        "privacy.hold_reviewed",
        "privacy.access_denied",
    }
    assert all(not event.reason and not event.metadata_json for event in events)

    audio.start()
    assert call("POST", preview_path, payload=pins, status=409)["detail"] == "STALE_PREVIEW"
    attempt = audio.session.get(CandidateExamAttempt, UUID(state["id"]))
    assert attempt is not None
    conflicting = CandidateExamAttempt(
        assignment_id=attempt.assignment_id,
        candidate_user_id=audio.users[1].id,
        organisation_id=org,
        exam_version_id=attempt.exam_version_id,
        attempt_number=3,
        status=CandidateAttemptStatus.CANCELLED,
        delivery_manifest_checksum=attempt.delivery_manifest_checksum,
        runtime_plan_checksum=attempt.runtime_plan_checksum,
        runtime_plan={},
        created_at=datetime.now(UTC),
    )
    audio.session.add(conflicting)
    audio.session.commit()
    changed = call("POST", preview_path, payload={**pins, "operation_id": str(uuid4())})
    assert {"ATTEMPT_OPEN", "OWNERSHIP_OR_LINEAGE_CONFLICT"} <= set(changed["dependencies"])
    attempts = next(row for row in changed["categories"] if row["category"] == "ATTEMPTS")
    assert attempts["count"] == 2 and attempts["complete"] is False
    assert str(conflicting.id) not in str(changed)

    class AfterExpiry(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(hours=3)

    with monkeypatch.context() as patch:
        patch.setattr(service, "datetime", AfterExpiry)
        call("GET", selected, status=403)
    grant.revoked_at = datetime.now(UTC)
    audio.session.commit()
    call("POST", preview_path, reviewer, status=403, payload=pins)
    assert audio.session.get(PrivacyCase, UUID(selected[1:])) is not None


def test_writing_human_release_and_ai_provenance(writing):
    org = UUID(writing.identifiers["organisation_id"])
    state = _release(writing, _score(writing, _human_journey(writing)))
    call, selected, pins, *_ = _case(writing, writing.candidate_id, org)
    result = call("POST", selected + "/preview", payload=pins)
    categories = {row["category"]: row for row in result["categories"]}
    assert categories["WRITING_REVISIONS"]["tables"]["candidate_response_revisions"] >= 1
    assert categories["HUMAN_REVIEW"]["tables"]["subjective_assessment_versions"] >= 1
    assert categories["RELEASE_STATEMENTS"]["tables"]["statements_of_results"] >= 1
    assert categories["SCORING_RESULTS"]["tables"]["result_versions"] >= 1
    assert state["revision"].payload.get("text") not in str(result)

    settings = app.dependency_overrides[get_settings]()
    settings.writing_test_adapter_enabled = True
    proof = WritingExecutionProof(
        **{
            **writing.__dict__,
            "email": AI_WRITING_CANDIDATE_EMAIL,
            "candidate_id": UUID(writing.identifiers["ai_writing_candidate_user_id"]),
            "exam_version_id": writing.identifiers["ai_writing_exam_version_id"],
            "item_version_id": writing.identifiers["ai_writing_item_version_id"],
        },
        factory=sessionmaker(bind=writing.session.get_bind(), expire_on_commit=False),
        settings=settings,
        database_url=os.environ["DATABASE_URL"],
    )
    submitted = proof.submit()
    proof.queue(submitted)
    call, selected, pins, *_ = _case(writing, proof.candidate_id, org)
    result = call("POST", selected + "/preview", payload=pins)
    categories = {row["category"]: row for row in result["categories"]}
    assert categories["AI_EXECUTION"]["tables"]["writing_execution_jobs"] >= 1
    assert categories["AI_EXECUTION"]["tables"]["ai_runs"] >= 1
    assert "ASSESSMENT_EXECUTION_PENDING" in result["dependencies"]
    assert submitted["text"] not in str(result)
