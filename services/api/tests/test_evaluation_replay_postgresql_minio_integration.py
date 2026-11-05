"""Replay actual separate-worker receipts; all inference remains TEST ONLY."""

from __future__ import annotations

import copy
import os
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterator

import pytest
from sqlalchemy import func, select

from app.auth.service import AuthenticationService
from app.authorization import AuthorizationError, build_tenant_context
from app.evaluation.expert_evidence import validate_reference_source
from app.evaluation.expert_schemas import SourceInput
from app.models import (
    AIRun,
    CandidateExamAttempt,
    ExamVersion,
    HumanReviewRequest,
    MembershipRole,
    OrganisationMembership,
    PlatformRoleAssignment,
    QuestionBankItemVersion,
    Role,
    RubricVersion,
    SpeechTranscriptionVersion,
    StandardPackVersion,
    User,
)
from app.models.enums import RoleScope, UserStatus
from app.scripts.seed_demo import PASSWORD as SEED_PASSWORD
from app.scripts.seed_demo import PLATFORM_ADMIN_EMAIL
from tests.test_evaluation_foundation_postgresql_integration import (
    PASSWORD,
    PREFIX,
    EvaluationScenario,
    dataset_body,
)
from tests.test_speaking_execution_postgresql_minio_integration import speaking_execution
from tests.test_transcription_execution_postgresql_minio_integration import execution
from tests.test_writing_execution_postgresql_integration import writing_execution

REQUIRED = (
    os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
    or os.getenv("PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION") == "1"
)
pytestmark = pytest.mark.skipif(
    not (os.getenv("POSTGRES_INTEGRATION_URL") and os.getenv("OBJECT_STORAGE_ENDPOINT"))
    and not REQUIRED,
    reason="Existing Speaking/audio receipt replay requires real PostgreSQL and private MinIO",
)


@dataclass
class PinnedScenario:
    kind: str
    evaluator: EvaluationScenario = field(repr=False)
    case: dict[str, Any] = field(repr=False)
    configuration: dict[str, Any] = field(repr=False)
    organisation_id: uuid.UUID
    job_id: uuid.UUID


@pytest.fixture(params=["WRITING", "SPEAKING", "TRANSCRIPTION"])
def pinned(request, monkeypatch) -> Iterator[PinnedScenario]:
    kind = request.param
    provider_fixture = {
        "WRITING": writing_execution,
        "SPEAKING": speaking_execution,
        "TRANSCRIPTION": execution,
    }[kind]
    generator = provider_fixture.__wrapped__(monkeypatch)
    proof = next(generator)
    try:
        state = proof.submit() if kind == "WRITING" else proof.submitted()
        queued = proof.request(state) if kind == "TRANSCRIPTION" else proof.queue(state)
        proof.worker_once()
        job = proof.job(queued)
        assert job.status == ("READY" if kind == "TRANSCRIPTION" else "COMPLETED")
        factory = proof.factory
        with factory() as session:
            creator = session.scalar(select(User).where(User.email == PLATFORM_ADMIN_EMAIL))
            assert creator is not None
            super_role = session.scalar(select(Role).where(Role.key == "PRUFAI_SUPER_ADMIN"))
            review_role = session.scalar(
                select(Role).where(Role.key == "INSTITUTION_ADMIN", Role.scope == RoleScope.TENANT)
            )
            assert super_role is not None and review_role is not None
            reviewer_email = "evaluation-review-" + uuid.uuid4().hex + "@fixture.invalid"
            reviewer = User(
                email=reviewer_email, normalized_email=reviewer_email, status=UserStatus.ACTIVE
            )
            session.add(reviewer)
            session.flush()
            AuthenticationService(proof.settings).set_password(session, reviewer, PASSWORD)
            session.add(PlatformRoleAssignment(user_id=reviewer.id, role_id=super_role.id))
            membership = OrganisationMembership(
                organisation_id=job.organisation_id, user_id=creator.id
            )
            session.add(membership)
            session.flush()
            session.add(MembershipRole(membership_id=membership.id, role_id=review_role.id))
            session.commit()
            actors = {"creator": creator.id, "reviewer": reviewer.id}
            identity = {
                "response_revision_id": str(job.candidate_response_revision_id),
                "response_checksum": state["revision"].payload_checksum,
            }
            reference: dict[str, Any] = {
                "label_kind": "SYNTHETIC_EXPECTATION",
                "evidence_identity": identity,
            }
            input_body: dict[str, Any] = {"language": "de", "evidence_identity": identity}
            if kind == "WRITING":
                input_body.update(modality="TEXT", text=state["text"])
            else:
                input_body.update(
                    modality="AUDIO",
                    audio_kind="TECHNICAL_TONE",
                    audio_sha256=job.audio_sha256,
                    duration_ms=job.verified_duration_ms,
                )
                identity["audio_sha256"] = job.audio_sha256
                identity["candidate_media_asset_id"] = str(job.candidate_media_asset_id)
            if kind == "TRANSCRIPTION":
                version = session.scalar(
                    select(SpeechTranscriptionVersion).where(
                        SpeechTranscriptionVersion.execution_job_id == job.id
                    )
                )
                assert version is not None
                reference.update(
                    text="Original synthetic reference transcript",
                    language="de",
                    segments_supported=bool(version.segments),
                )
                attempt = session.get(CandidateExamAttempt, job.attempt_id)
                exam = session.get(ExamVersion, attempt.exam_version_id)
                pack = session.get(StandardPackVersion, exam.standard_pack_version_id)
                configuration = {
                    "standard_pack_version_id": str(pack.id),
                    "standard_pack_checksum": pack.configuration_checksum,
                    "rubric_version_id": None,
                    "rubric_checksum": None,
                    "question_bank_item_version_id": job.source_identity["item_version_id"],
                    "item_checksum": job.source_identity["item_version_checksum"],
                    "model_profile_id": str(job.profile_id),
                    "model_version": job.profile_version,
                    "prompt_version_id": None,
                    "agent_version_id": None,
                    "workflow_version_id": None,
                    "profile_version": job.profile_version,
                }
            else:
                rubric = session.get(RubricVersion, job.rubric_version_id)
                reference["criteria"] = [
                    {"code": row["code"], "points": "0", "max_points": str(row["max_points"])}
                    for row in rubric.criteria
                ]
                configuration = {
                    key: job.source_identity[key]
                    for key in (
                        "standard_pack_version_id",
                        "standard_pack_checksum",
                        "rubric_version_id",
                        "rubric_checksum",
                        "question_bank_item_version_id",
                        "item_checksum",
                    )
                }
                configuration.update(
                    {
                        key: job.execution_snapshot[key]
                        for key in (
                            "model_profile_id",
                            "model_version",
                            "prompt_version_id",
                            "agent_version_id",
                            "workflow_version_id",
                            "profile_version",
                        )
                    }
                )
            configuration.update(
                execution_adapter="PINNED_EXECUTION_REPLAY_V1",
                evaluation_method="DETERMINISTIC_REFERENCE_COMPARISON",
                evaluation_method_version="1",
                metric_configuration={"retrieval_k": 5},
            )
            case = {
                "case_id": "existing-worker-" + kind.lower(),
                "kind": kind,
                "split": "TEST",
                "input": input_body,
                "reference": reference,
                "synthetic_observation": None,
                "execution_job_id": str(job.id),
            }
        evaluator = EvaluationScenario(
            proof.session.get_bind(),
            factory,
            proof.client,
            proof.settings,
            actors,
            [job.organisation_id],
            passwords={"creator": SEED_PASSWORD},
        )
        yield PinnedScenario(kind, evaluator, case, configuration, job.organisation_id, job.id)
    finally:
        generator.close()


def test_actual_existing_worker_receipt_is_replayed_with_private_authority(
    pinned: PinnedScenario,
) -> None:
    scenario = pinned.evaluator
    body = dataset_body(cases=[pinned.case])
    body["skill"] = pinned.kind
    body["organisation_id"] = str(pinned.organisation_id)
    version = scenario.published(body)
    run_request = {
        "operation_id": str(uuid.uuid4()),
        "dataset_version_id": version["id"],
        "dataset_checksum": version["checksum"],
        "configuration": pinned.configuration,
    }
    with scenario.factory() as session:
        counts = {
            model.__name__: session.scalar(select(func.count()).select_from(model))
            for model in (AIRun, HumanReviewRequest)
        }
        if pinned.kind == "WRITING":
            _assert_submitted_writing_reference(pinned, session)
    # A platform reviewer has no institution membership: existing private worker
    # evidence cannot be read merely by holding global evaluation authority.
    scenario.request("POST", PREFIX + "/runs", actor="reviewer", json=run_request, expected=403)
    detail = scenario.execute(run_request)
    assert detail["run"]["hard_gate"] == "PASS"
    assert detail["run"]["execution_class"] == "TEST_ONLY"
    assert detail["run"]["evidence_class"] == "TECHNICAL_TEST"
    assert detail["pins"]["profile_version"] == pinned.configuration["profile_version"]
    scenario.request("GET", PREFIX + "/dataset-versions/" + version["id"] + "/cases", expected=403)
    _assert_source_drift_rejected(pinned, detail)
    mismatched = copy.deepcopy(run_request)
    mismatched["operation_id"] = str(uuid.uuid4())
    mismatched["configuration"]["profile_version"] = "f" * 64
    scenario.request("POST", PREFIX + "/runs", json=mismatched, expected=422)
    with scenario.factory() as session:
        assert counts == {
            model.__name__: session.scalar(select(func.count()).select_from(model))
            for model in (AIRun, HumanReviewRequest)
        }


def _assert_submitted_writing_reference(pinned: PinnedScenario, session: Any) -> None:
    """Read the existing submitted fixture through the new human-reference resolver."""
    scenario = pinned.evaluator
    payload = SourceInput(
        operation_id=uuid.uuid4(),
        code="synthetic-submitted-writing-reference",
        organisation_id=pinned.organisation_id,
        kind="WRITING",
        split="VALIDATION",
        origin="ORIGINAL_SYNTHETIC",
        language="de",
        assessment_family="LANGUAGE",
        cefr_level="B1",
        retention_classification="SYNTHETIC_TEST",
        source_reference="synthetic:existing-writing-receipt",
        rights_approval_reference="synthetic:owned-original",
        processing_approval_reference="synthetic:submitted-fixture-processing",
        known_lineage_reference="synthetic:writing:" + str(pinned.job_id),
        input=pinned.case["input"],
        **{
            key: pinned.configuration[key]
            for key in (
                "standard_pack_version_id",
                "standard_pack_checksum",
                "rubric_version_id",
                "rubric_checksum",
                "question_bank_item_version_id",
                "item_checksum",
            )
        },
    )
    creator = build_tenant_context(session, scenario.actors["creator"])
    resolved = validate_reference_source(session, creator, payload)
    assert resolved["evidence_class"] == "SYNTHETIC_TEST"
    assert resolved["body"]["input"]["text"] == pinned.case["input"]["text"]
    assert (
        resolved["body"]["input"]["evidence_identity"] == pinned.case["input"]["evidence_identity"]
    )
    for changed, reason in (
        ({"text": payload.input.text + " tampered"}, "REFERENCE_WRITING_TEXT_MISMATCH"),
        (
            {
                "evidence_identity": {
                    **payload.input.evidence_identity,
                    "response_checksum": "0" * 64,
                }
            },
            "REFERENCE_SUBMITTED_RESPONSE_PIN_MISMATCH",
        ),
        (
            {
                "evidence_identity": {
                    **payload.input.evidence_identity,
                    "response_revision_id": str(uuid.uuid4()),
                }
            },
            "REFERENCE_RESPONSE_UNAVAILABLE",
        ),
    ):
        tampered = payload.model_copy(update={"input": payload.input.model_copy(update=changed)})
        with pytest.raises(ValueError, match=reason):
            validate_reference_source(session, creator, tampered)
    with pytest.raises(AuthorizationError):
        validate_reference_source(
            session, build_tenant_context(session, scenario.actors["reviewer"]), payload
        )
    assert not session.new and not session.dirty


def _assert_source_drift_rejected(pinned: PinnedScenario, detail: dict[str, Any]) -> None:
    # Corrupt only the fixture's synthetic source, then restore it. A harmless
    # Decimal representation round trip must differ from real content/point drift.
    scenario = pinned.evaluator
    item_id = uuid.UUID(pinned.configuration["question_bank_item_version_id"])
    with scenario.factory() as session:
        item = session.get(QuestionBankItemVersion, item_id)
        original_content = copy.deepcopy(item.content)
        original_points = item.max_points
    corrupt_content = {**original_content, "prompt": "Corrupted original synthetic prompt"}
    changes = [({"content": corrupt_content}, {"content": original_content})]
    if original_points is not None:
        changes.append(
            ({"max_points": original_points + Decimal(1)}, {"max_points": original_points})
        )
    table = QuestionBankItemVersion.__table__
    path = PREFIX + "/runs/" + detail["run"]["id"]
    for changed, restored in changes:
        with scenario.engine.begin() as connection:
            connection.execute(table.update().where(table.c.id == item_id).values(**changed))
        try:
            scenario.request("GET", path, expected=409)
        finally:
            with scenario.engine.begin() as connection:
                connection.execute(table.update().where(table.c.id == item_id).values(**restored))
    assert scenario.request("GET", path)["run"]["checksum"] == detail["run"]["checksum"]
