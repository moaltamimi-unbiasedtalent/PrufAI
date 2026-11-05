"""Real migration-backed reference governance; all examiners/labels are SYNTHETIC_TEST.

These tests do not establish examiner qualification, speech quality or CEFR validity.
The optional audio proof uses actual private MinIO and generated technical tones.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.auth.service import AuthenticationService
from app.authorization import AuthorizationError, build_tenant_context
from app.authorization.seed import seed_defaults
from app.config import Settings
from app.evaluation import expert_comparison, expert_evidence, expert_service
from app.evaluation import service as foundation
from app.evaluation.disposable import assert_disposable_database
from app.evaluation.expert_requests import (
    AdjudicationInput,
    CalibrationInput,
    JudgmentInput,
    QualificationInput,
    ReferenceDatasetInput,
)
from app.evaluation.expert_schemas import SourceInput
from app.evaluation.schemas import RunConfiguration, RunRequest
from app.evaluation.service import EvaluationConflict
from app.models import (
    AIRun,
    AuditEvent,
    CandidateResponseRevision,
    EvaluationDatasetVersion,
    HumanReviewRequest,
    KnowledgeAuthority,
    KnowledgeDocumentVersion,
    KnowledgeLegalMetadata,
    KnowledgeSource,
    MembershipRole,
    OrganisationMembership,
    Permission,
    PlatformRoleAssignment,
    Role,
    RolePermission,
    RubricVersion,
    StandardPack,
    StandardPackVersion,
    User,
)
from app.models.enums import (
    AssessmentFamily,
    KnowledgeAuthorityType,
    KnowledgeDocumentType,
    KnowledgeScope,
    KnowledgeVersionStatus,
    LegalReviewStatus,
    PlatformAssignmentStatus,
    QuestionBankSkill,
    RoleScope,
    RubricVersionStatus,
    StandardPackStatus,
    UserStatus,
)
from app.models.expert_evaluation import (
    EvaluationExaminerQualification,
    EvaluationExpertJudgment,
    EvaluationReference,
)
from app.standard_packs.service import StandardPackService
from app.writing_assessment.service import RubricService
from tests.alembic_helpers import repository_alembic_head
from tests.test_recorded_audio_postgresql_minio_integration import audio as recorded_audio_fixture
from tests.test_speaking_execution_postgresql_minio_integration import SpeakingProof

REQUIRED = (
    os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
    or os.getenv("PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION") == "1"
)
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRED,
    reason="Expert-reference acceptance requires real Alembic PostgreSQL",
)
API_ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "Synthetic-Expert-References-Only-2026!"


@dataclass
class ExpertScenario:
    factory: Any
    engine: Any
    database_url: str
    actors: dict[str, uuid.UUID]
    pack_id: uuid.UUID
    pack_checksum: str
    rubric_id: uuid.UUID
    rubric_checksum: str

    def context(self, session, actor="creator"):
        return build_tenant_context(session, self.actors[actor])

    def source_payload(self, **overrides):
        marker = uuid.uuid4().hex
        value = {
            "operation_id": str(uuid.uuid4()),
            "code": "synthetic-expert-" + marker,
            "kind": "WRITING",
            "split": "CALIBRATION",
            "origin": "ORIGINAL_SYNTHETIC",
            "language": "de",
            "assessment_family": "LANGUAGE",
            "cefr_level": "B1",
            "purpose": "EVALUATION_ONLY",
            "retention_classification": "SYNTHETIC_TEST",
            "source_reference": "synthetic:original:" + marker,
            "rights_approval_reference": "synthetic:owned-original",
            "known_lineage_reference": "synthetic:lineage:" + marker,
            "standard_pack_version_id": str(self.pack_id),
            "standard_pack_checksum": self.pack_checksum,
            "rubric_version_id": str(self.rubric_id),
            "rubric_checksum": self.rubric_checksum,
            "input": {"text": "Guten Tag " + marker, "language": "de", "modality": "TEXT"},
        }
        return SourceInput.model_validate({**value, **overrides})

    def source(self, *, approved=True, **overrides):
        with self.factory() as session:
            row = expert_service.create_reference(
                session, self.context(session), self.source_payload(**overrides)
            )
            session.commit()
            if approved:
                row = expert_service.approve_reference(
                    session,
                    self.context(session, "approver"),
                    row.id,
                    row.checksum,
                    "synthetic:independent-source-review",
                )
                session.commit()
            return row

    def qualification(self, source, actor):
        value = {
            key: source.body[key]
            for key in (
                "organisation_id",
                "kind",
                "language",
                "rubric_version_id",
                "rubric_checksum",
                "standard_pack_version_id",
                "standard_pack_checksum",
            )
        }
        payload = QualificationInput(
            **value,
            operation_id=uuid.uuid4(),
            examiner_id=self.actors[actor],
            origin="SYNTHETIC_TEST",
            credential_reference="synthetic:fixture-credential:" + actor,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        with self.factory() as session:
            row = expert_service.create_qualification(session, self.context(session), payload)
            session.commit()
            row = expert_service.approve_qualification(
                session,
                self.context(session, "approver"),
                row.id,
                row.checksum,
                "synthetic:independent-credential-review",
            )
            session.commit()
            return row

    def judgment_payload(self, source, qualification, points="2", **overrides):
        body = {
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": source.checksum,
            "qualification_id": str(qualification.id),
            "qualification_checksum": qualification.checksum,
            "criteria": [
                {
                    "criterion_code": "TASK",
                    "points": points,
                    "level": "LEVEL_1",
                    "rationale": "SYNTHETIC TEST examiner rationale",
                    "evidence": [{"quote": "Guten", "start": 0, "end": 5}],
                }
            ],
            "review_reference": "synthetic:private-examiner-decision",
        }
        return JudgmentInput.model_validate({**body, **overrides})

    def judgment(self, source, actor, points="2"):
        qualification = self.qualification(source, actor)
        payload = self.judgment_payload(source, qualification, points)
        with self.factory() as session:
            row = expert_service.submit_judgment(
                session, self.context(session, actor), source.id, payload
            )
            session.commit()
            return row

    def adjudication_payload(self, source, judgments, qualification, **overrides):
        body = self.judgment_payload(source, qualification).model_dump(mode="json")
        return AdjudicationInput.model_validate(
            {
                **body,
                "judgment_pins": [
                    {"id": str(row.id), "checksum": row.checksum} for row in judgments
                ],
                "rationale": "SYNTHETIC TEST independent adjudication, no expert quality claim",
                **overrides,
            }
        )

    def adjudicated(self):
        source = self.source()
        judgments = [
            self.judgment(source, "examiner-a", "1"),
            self.judgment(source, "examiner-b", "3"),
        ]
        qualification = self.qualification(source, "adjudicator")
        with self.factory() as session:
            adjudication = expert_service.adjudicate(
                session,
                self.context(session, "adjudicator"),
                source.id,
                self.adjudication_payload(source, judgments, qualification),
            )
            session.commit()
            return source, judgments, adjudication


@pytest.fixture(scope="module")
def expert() -> Iterator[ExpertScenario]:
    configured = os.environ.get("POSTGRES_INTEGRATION_URL", "")
    assert_disposable_database(configured, os.environ.get("PRUFAI_DISPOSABLE_DATABASE"))
    source = make_url(configured)
    name = "prufai_eval2_expert_" + uuid.uuid4().hex
    fresh = source.set(database=name).render_as_string(hide_password=False)
    assert_disposable_database(fresh, name)
    control = source.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(control, autocommit=True) as connection:
        assert connection.execute("SELECT current_database()").fetchone()[0] == source.database
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    engine = None
    try:
        with psycopg.connect(
            source.set(drivername="postgresql", database=name).render_as_string(hide_password=False)
        ) as connection:
            assert connection.execute("SELECT current_database()").fetchone()[0] == name
        environment = {
            **os.environ,
            "DATABASE_URL": fresh,
            "POSTGRES_INTEGRATION_URL": fresh,
            "PRUFAI_DISPOSABLE_DATABASE": name,
        }
        assert_disposable_database(environment["DATABASE_URL"], name)
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=API_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        engine = create_engine(fresh)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        actors = {}
        with factory() as session:
            assert session.scalar(text("SELECT current_database()")) == name
            assert (
                session.scalar(text("SELECT version_num FROM alembic_version"))
                == repository_alembic_head()
            )
            seed_defaults(session)
            for actor in (
                "creator",
                "approver",
                "examiner-a",
                "examiner-b",
                "adjudicator",
                "revoked",
                "candidate",
                "observer",
            ):
                email = actor + "-" + uuid.uuid4().hex + "@synthetic-expert.fixture.invalid"
                user = User(email=email, normalized_email=email, status=UserStatus.ACTIVE)
                session.add(user)
                session.flush()
                AuthenticationService(Settings(_env_file=None, environment="test")).set_password(
                    session, user, PASSWORD
                )
                actors[actor] = user.id
                if actor == "candidate":
                    continue
                role = Role(key="EXPERT_TEST_" + uuid.uuid4().hex, scope=RoleScope.PLATFORM)
                session.add(role)
                session.flush()
                keys = (
                    {"standards.evaluation.read", "platform.control_center.read"}
                    if actor == "observer"
                    else {
                        "standards.evaluation.read",
                        "standards.evaluation.run",
                        "ai.review",
                        "ai.manage",
                    }
                )
                for key in keys:
                    permission = session.scalar(select(Permission).where(Permission.key == key))
                    assert permission is not None
                    session.add(RolePermission(role_id=role.id, permission_id=permission.id))
                session.add(PlatformRoleAssignment(user_id=user.id, role_id=role.id))
            pack = StandardPack(
                code="EXPERT_TEST_" + uuid.uuid4().hex,
                name="SYNTHETIC TEST ONLY",
                assessment_family=AssessmentFamily.LANGUAGE,
                language="de",
            )
            session.add(pack)
            session.flush()
            version = StandardPackVersion(
                standard_pack_id=pack.id,
                version_label="synthetic-1",
                status=StandardPackStatus.DRAFT,
                created_at=datetime.now(timezone.utc),
                supported_levels=["B1"],
                allowed_classifications=[],
                configuration_schema_version="1",
            )
            session.add(version)
            session.flush()
            version.configuration_checksum = StandardPackService.checksum(version)
            version.status = StandardPackStatus.PUBLISHED
            rubric = RubricService.create_rubric(
                session,
                code="SYNTHETIC_EXPERT_" + uuid.uuid4().hex,
                name="Synthetic expert arithmetic",
                organisation_id=None,
            )
            rubric_version = RubricService.create_version(
                session,
                rubric=rubric,
                standard_pack=version,
                version_label="synthetic-1",
                criteria=[
                    {
                        "code": "TASK",
                        "max_points": "4",
                        "weight": "1",
                        "descriptors": {"LEVEL_0": "No evidence", "LEVEL_1": "Basic evidence"},
                    }
                ],
                performance_levels=[{"code": "LEVEL_0"}, {"code": "LEVEL_1"}],
                aggregation_policy={"method": "SUM"},
                evidence_policy={"required": False},
                ai_assessment_policy={"mode": "HUMAN_ONLY", "enabled": False},
                human_review_policy={"required": True, "separation_of_duties": True},
                skill=QuestionBankSkill.WRITING,
            )
            rubric_version.status = RubricVersionStatus.PUBLISHED
            session.commit()
            scenario = ExpertScenario(
                factory,
                engine,
                fresh,
                actors,
                version.id,
                version.configuration_checksum,
                rubric_version.id,
                rubric_version.checksum,
            )
        yield scenario
    finally:
        if engine is not None:
            engine.dispose()
        assert_disposable_database(fresh, name)
        with psycopg.connect(control, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


def test_fresh_alembic_reference_tables_and_single_dynamic_head(expert):
    with expert.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == repository_alembic_head()
        )
        tables = set(
            connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            ).scalars()
        )
        assert {
            "evaluation_references",
            "evaluation_examiner_qualifications",
            "evaluation_expert_judgments",
            "evaluation_adjudications",
        } <= tables


def test_source_independent_rights_approval_and_exact_idempotency(expert):
    payload = expert.source_payload()
    with expert.factory() as session:
        source = expert_service.create_reference(session, expert.context(session), payload)
        session.commit()
        assert source.evidence_class == "SYNTHETIC_TEST"
        assert (
            expert_service.create_reference(session, expert.context(session), payload).id
            == source.id
        )
        with pytest.raises(AuthorizationError, match="INDEPENDENT"):
            expert_service.approve_reference(
                session, expert.context(session), source.id, source.checksum, "synthetic:self"
            )
        session.rollback()
        with pytest.raises(EvaluationConflict):
            expert_service.approve_reference(
                session,
                expert.context(session, "approver"),
                source.id,
                "0" * 64,
                "synthetic:review",
            )
        session.rollback()


def test_two_blinded_judgments_require_independent_adjudication(expert):
    source = expert.source()
    first = expert.judgment(source, "examiner-a", "1")
    expert.qualification(source, "examiner-b")
    with expert.factory() as session:
        view = expert_service.workbench(session, expert.context(session, "examiner-b"), source.id)
        assert view["judgments"] == []
        assert view["reference"]["adjudication_status"] == "PENDING"
    second = expert.judgment(source, "examiner-b", "3")
    with expert.factory() as session:
        assert (
            expert_service.reference_summary(session, session.get(EvaluationReference, source.id))[
                "adjudication_status"
            ]
            == "PENDING"
        )
    qualification = expert.qualification(source, "examiner-a")
    with expert.factory() as session:
        with pytest.raises(AuthorizationError, match="INDEPENDENT_ADJUDICATION"):
            expert_service.adjudicate(
                session,
                expert.context(session, "examiner-a"),
                source.id,
                expert.adjudication_payload(source, [first, second], qualification),
            )
        session.rollback()
    adjudicator = expert.qualification(source, "adjudicator")
    with expert.factory() as session:
        body = expert.adjudication_payload(source, [first, second], adjudicator)
        row = expert_service.adjudicate(
            session, expert.context(session, "adjudicator"), source.id, body
        )
        session.commit()
        assert row.version_number == 1
        assert (
            expert_service.adjudicate(
                session, expert.context(session, "adjudicator"), source.id, body
            ).id
            == row.id
        )


@pytest.mark.parametrize(
    "change",
    [
        {"criteria": []},
        {
            "criteria": [
                {
                    "criterion_code": "TASK",
                    "points": "5",
                    "level": "LEVEL_1",
                    "rationale": "synthetic",
                    "evidence": [],
                }
            ]
        },
        {
            "criteria": [
                {
                    "criterion_code": "TASK",
                    "points": "2",
                    "level": "LEVEL_1",
                    "rationale": "synthetic",
                    "evidence": [{"quote": "forged", "start": 0, "end": 5}],
                }
            ]
        },
    ],
)
def test_missing_out_of_bounds_and_forged_quote_judgments_fail(expert, change):
    source = expert.source()
    qualification = expert.qualification(source, "examiner-a")
    with expert.factory() as session:
        with pytest.raises(ValueError):
            expert_service.submit_judgment(
                session,
                expert.context(session, "examiner-a"),
                source.id,
                expert.judgment_payload(source, qualification, **change),
            )
        session.rollback()
        assert not list(
            session.scalars(
                select(EvaluationExpertJudgment).where(
                    EvaluationExpertJudgment.reference_id == source.id
                )
            )
        )


def test_examiner_cannot_use_another_identity_or_stale_qualification(expert):
    source = expert.source()
    qualification = expert.qualification(source, "examiner-a")
    with expert.factory() as session:
        for actor, body in (
            ("examiner-b", expert.judgment_payload(source, qualification)),
            (
                "examiner-a",
                expert.judgment_payload(source, qualification, qualification_checksum="0" * 64),
            ),
        ):
            with pytest.raises((AuthorizationError, EvaluationConflict)):
                expert_service.submit_judgment(
                    session, expert.context(session, actor), source.id, body
                )
            session.rollback()


def test_candidate_and_metadata_observer_have_no_private_workbench(expert):
    source = expert.source()
    with expert.factory() as session:
        for actor in ("candidate", "observer"):
            with pytest.raises(AuthorizationError):
                expert_service.workbench(session, expert.context(session, actor), source.id)
            session.rollback()
        summary = expert_service.reference_summary(
            session, session.get(EvaluationReference, source.id)
        )
        serialized = json.dumps(summary)
        assert source.body["input"]["text"] not in serialized
        assert source.body["rights_approval_reference"] not in serialized


@pytest.mark.parametrize("duplicate", ["content", "lineage"])
def test_exact_or_known_lineage_overlap_cannot_cross_split(expert, duplicate):
    source = expert.source(split="HELD_OUT")
    changes = {"split": "TRAIN"}
    if duplicate == "content":
        changes["input"] = source.body["input"]
    else:
        changes["known_lineage_reference"] = source.body["known_lineage_reference"]
    with expert.factory() as session:
        with pytest.raises(EvaluationConflict, match="SPLIT_LEAKAGE"):
            expert_service.create_reference(
                session, expert.context(session), expert.source_payload(**changes)
            )
        session.rollback()


def test_same_text_with_renamed_code_and_whitespace_cannot_bypass_holdout(expert):
    source = expert.source(split="HELD_OUT")
    altered = {
        **source.body["input"],
        "text": "  " + source.body["input"]["text"].replace(" ", "\n") + "  ",
    }
    with expert.factory() as session:
        with pytest.raises(EvaluationConflict, match="SPLIT_LEAKAGE"):
            expert_service.create_reference(
                session,
                expert.context(session),
                expert.source_payload(split="CALIBRATION", input=altered),
            )
        session.rollback()


def test_source_and_submitted_history_resist_orm_nested_and_sql_rewrite(expert):
    source, judgments, adjudication = expert.adjudicated()
    with expert.factory() as session:
        row = session.get(EvaluationReference, source.id)
        row.body["input"]["text"] = "silently changed"
        with pytest.raises(ValueError, match="IMMUTABLE"):
            session.commit()
        session.rollback()
    for table, row in (
        ("evaluation_references", source),
        ("evaluation_expert_judgments", judgments[0]),
        ("evaluation_adjudications", adjudication),
    ):
        with expert.engine.begin() as connection:
            with pytest.raises(DBAPIError):
                connection.execute(
                    text(f"UPDATE {table} SET body='{{}}'::json WHERE id=:id"), {"id": row.id}
                )


def test_exact_adjudication_successor_preserves_original_and_derives_existing_registry(expert):
    source, judgments, first = expert.adjudicated()
    qualification = expert.qualification(source, "adjudicator")
    with expert.factory() as session:
        body = expert.adjudication_payload(
            source,
            judgments,
            qualification,
            supersedes_id=str(first.id),
            rationale="SYNTHETIC TEST documented successor",
        )
        successor = expert_service.adjudicate(
            session, expert.context(session, "adjudicator"), source.id, body
        )
        session.commit()
        assert successor.version_number == 2 and successor.supersedes_id == first.id
        with pytest.raises(EvaluationConflict, match="SUCCESSOR"):
            expert_service.adjudicate(
                session,
                expert.context(session, "adjudicator"),
                source.id,
                expert.adjudication_payload(source, judgments, qualification),
            )
        session.rollback()
        dataset = expert_service.derive_dataset(
            session,
            expert.context(session),
            source.id,
            ReferenceDatasetInput(
                operation_id=uuid.uuid4(),
                expected_checksum=source.checksum,
                adjudication_id=successor.id,
                adjudication_checksum=successor.checksum,
            ),
        )
        session.commit()
        assert isinstance(dataset, EvaluationDatasetVersion)
        assert dataset.dataset_id == source.dataset_id
        assert dataset.evidence_class in {"SYNTHETIC_TEST", "TECHNICAL_TEST"}
        assert dataset.provenance["reference_id"] == str(source.id)
        assert dataset.cases[0]["reference"]["label_kind"] == "SYNTHETIC_EXPECTATION"


def test_current_role_revocation_blocks_held_jwt_context(expert):
    source = expert.source()
    qualification = expert.qualification(source, "revoked")
    with expert.factory() as session:
        context = expert.context(session, "revoked")
        grant = session.scalar(
            select(PlatformRoleAssignment).where(
                PlatformRoleAssignment.user_id == expert.actors["revoked"]
            )
        )
        grant.status = PlatformAssignmentStatus.SUSPENDED
        session.commit()
        with pytest.raises(AuthorizationError):
            expert_service.submit_judgment(
                session, context, source.id, expert.judgment_payload(source, qualification)
            )
        session.rollback()


def test_audit_records_safe_opaque_receipts_without_source_or_rationale(expert):
    source, judgments, adjudication = expert.adjudicated()
    with expert.factory() as session:
        rows = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_id.in_(
                        [str(source.id), str(judgments[0].id), str(adjudication.id)]
                    )
                )
            )
        )
        assert {
            "evaluation.reference_created",
            "evaluation.reference_rights_approved",
            "evaluation.expert_judgment_submitted",
            "evaluation.expert_adjudicated",
        } <= {row.event_type for row in rows}
        payload = json.dumps([row.metadata_json for row in rows])
        for private in (
            source.body["input"]["text"],
            source.body["rights_approval_reference"],
            judgments[0].body["criteria"][0]["rationale"],
            adjudication.body["rationale"],
        ):
            assert private not in payload


def test_genuine_candidate_requires_processing_and_language_speech_not_tone(expert):
    value = expert.source_payload().model_dump(mode="json")
    value.update(
        origin="APPROVED_CANDIDATE",
        retention_classification="APPROVED_RESTRICTED",
        source_reference="source:approved-schema-negative",
        rights_approval_reference="rights:approved-schema-negative",
        known_lineage_reference="lineage:approved-schema-negative",
    )
    with pytest.raises(ValueError, match="processing approval"):
        SourceInput.model_validate(value)
    value.update(
        kind="TRANSCRIPTION",
        organisation_id=str(uuid.uuid4()),
        processing_approval_reference="processing:approved",
        input={
            "language": "de",
            "modality": "AUDIO",
            "audio_sha256": "a" * 64,
            "duration_ms": 1000,
            "audio_kind": "TECHNICAL_TONE",
        },
    )
    with pytest.raises(ValueError, match="language speech"):
        SourceInput.model_validate(value)


@pytest.fixture
def original_audio(expert, monkeypatch):
    """Actual recorder/finalizer/submission on the already guarded child database."""
    if not os.getenv("OBJECT_STORAGE_ENDPOINT"):
        if os.getenv("PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION") == "1":
            pytest.fail("Required expert-audio gate needs actual private MinIO configuration")
        pytest.skip("Expert audio requires actual private MinIO")
    name = make_url(expert.database_url).database
    assert_disposable_database(expert.database_url, name)
    monkeypatch.setenv("POSTGRES_INTEGRATION_URL", expert.database_url)
    monkeypatch.setenv("DATABASE_URL", expert.database_url)
    monkeypatch.setenv("PRUFAI_DISPOSABLE_DATABASE", name)
    # The positive receipt test executes the actual separate TEST_ONLY worker.
    # Configure its explicit opt-in before RecordedAudio constructs Settings;
    # the subprocess inherits this fixture-scoped environment.
    monkeypatch.setenv("SPEAKING_TEST_ADAPTER_ENABLED", "1")
    generator = recorded_audio_fixture.__wrapped__()
    audio = next(generator)
    try:
        with audio.factory() as session:
            candidate = session.get(User, uuid.UUID(audio.ids["ai_speaking_candidate_user_id"]))
            assert candidate is not None
            session.expunge(candidate)
            audio.users[0] = candidate
            role = session.scalar(
                select(Role).where(Role.key == "INSTITUTION_ADMIN", Role.scope == RoleScope.TENANT)
            )
            assert role is not None
            for actor in ("creator", "approver"):
                existing = session.scalar(
                    select(OrganisationMembership).where(
                        OrganisationMembership.organisation_id
                        == uuid.UUID(audio.ids["organisation_id"]),
                        OrganisationMembership.user_id == expert.actors[actor],
                    )
                )
                if existing is None:
                    membership = OrganisationMembership(
                        organisation_id=uuid.UUID(audio.ids["organisation_id"]),
                        user_id=expert.actors[actor],
                    )
                    session.add(membership)
                    session.flush()
                    session.add(MembershipRole(membership_id=membership.id, role_id=role.id))
            session.commit()
        proof = SpeakingProof(**audio.__dict__, database_url=expert.database_url)
        state = proof.submitted()
        monkeypatch.setattr(expert_evidence, "get_settings", lambda: proof.settings)
        with proof.factory() as session:
            rubric = session.get(
                RubricVersion, uuid.UUID(proof.ids["ai_speaking_rubric_version_id"])
            )
            revision = session.get(CandidateResponseRevision, state["revision"].id)
            assert rubric is not None and revision is not None
            pack = session.get(StandardPackVersion, rubric.standard_pack_version_id)
            assert pack is not None
            asset = proof.asset(state["asset"])
            payload = expert.source_payload(
                kind="SPEAKING",
                organisation_id=proof.ids["organisation_id"],
                standard_pack_version_id=str(pack.id),
                standard_pack_checksum=pack.configuration_checksum,
                rubric_version_id=str(rubric.id),
                rubric_checksum=rubric.checksum,
                question_bank_item_version_id=proof.ids["ai_speaking_item_version_id"],
                item_checksum=state["items"][0]["item_version_checksum"],
                input={
                    "language": "de",
                    "modality": "AUDIO",
                    "audio_kind": "TECHNICAL_TONE",
                    "audio_sha256": asset.sha256,
                    "duration_ms": asset.duration_ms,
                    "evidence_identity": {
                        "candidate_media_asset_id": str(asset.id),
                        "candidate_response_revision_id": str(revision.id),
                        "response_checksum": revision.payload_checksum,
                    },
                },
                known_lineage_reference="synthetic:original-private-audio:" + str(asset.id),
                processing_approval_reference="synthetic:fixture-original-audio-processing",
            )
        yield proof, state, payload
    finally:
        generator.close()


def test_real_minio_original_audio_source_exact_binding_and_checksum(expert, original_audio):
    proof, state, payload = original_audio
    with expert.factory() as session:
        source = expert_service.create_reference(session, expert.context(session), payload)
        session.commit()
        assert source.evidence_class == "SYNTHETIC_TEST"
        assert source.body["input"]["audio_kind"] == "TECHNICAL_TONE"
        assert source.body["input"]["audio_sha256"] == state["asset"]["sha256"]
        asset = proof.asset(state["asset"])
        assert proof.storage.get(asset.storage_key) == state["original"]
        approved = expert_service.approve_reference(
            session,
            expert.context(session, "approver"),
            source.id,
            source.checksum,
            "synthetic:original-audio-review",
        )
        session.commit()
        assert approved.status == "APPROVED"


def test_real_minio_replaced_original_bytes_are_rejected(expert, original_audio):
    proof, state, payload = original_audio
    asset = proof.asset(state["asset"])
    original = proof.storage.get(asset.storage_key)
    try:
        proof.storage.put(
            asset.storage_key, bytes(value ^ 1 for value in original), asset.content_type
        )
        with expert.factory() as session:
            with pytest.raises(ValueError, match="CHECKSUM"):
                expert_evidence.validate_reference_source(session, expert.context(session), payload)
    finally:
        proof.storage.put(asset.storage_key, original, asset.content_type)


def test_audio_reference_rejects_stale_revision_duration_cross_tenant_and_transcript_only(
    expert, original_audio
):
    _, _, payload = original_audio
    for change in (
        {"duration_ms": payload.input.duration_ms + 1},
        {
            "evidence_identity": {
                **payload.input.evidence_identity,
                "candidate_response_revision_id": str(uuid.uuid4()),
            }
        },
    ):
        bad = payload.model_copy(update={"input": payload.input.model_copy(update=change)})
        with expert.factory() as session:
            with pytest.raises(ValueError):
                expert_evidence.validate_reference_source(session, expert.context(session), bad)
    with expert.factory() as session:
        with pytest.raises(AuthorizationError):
            expert_evidence.validate_reference_source(
                session, expert.context(session, "examiner-b"), payload
            )
    body = payload.model_dump(mode="json")
    body["input"]["modality"] = "TRANSCRIPT"
    with pytest.raises(ValueError, match="original audio"):
        SourceInput.model_validate(body)


def test_audio_judgment_requires_exact_original_asset_and_valid_spans(expert, original_audio):
    proof, _, payload = original_audio
    with proof.factory() as session:
        rubric = session.get(RubricVersion, payload.rubric_version_id)
        assert rubric is not None
        identity = payload.input.evidence_identity
        evidence = {
            "asset_id": identity["candidate_media_asset_id"],
            "audio_sha256": payload.input.audio_sha256,
            "candidate_response_revision_id": identity["candidate_response_revision_id"],
            "start_ms": 0,
            "end_ms": payload.input.duration_ms,
            "time_unit": "milliseconds",
        }
        criteria = [
            {
                "criterion_code": row["code"],
                "points": "0",
                "level": rubric.performance_levels[0]["code"],
                "rationale": "SYNTHETIC TEST audio evidence only",
                "evidence": [evidence],
            }
            for row in rubric.criteria
        ]
        body = payload.model_dump(mode="json", exclude={"operation_id", "code"})
        assert expert_evidence.validate_judgment_evidence(body, rubric, criteria, None, [])
        for change in (
            {"end_ms": payload.input.duration_ms + 1},
            {"audio_sha256": "0" * 64},
            {"asset_id": str(uuid.uuid4())},
            {"candidate_response_revision_id": str(uuid.uuid4())},
            {"start_ms": True},
        ):
            invalid = copy.deepcopy(criteria)
            invalid[0]["evidence"][0].update(change)
            with pytest.raises(ValueError):
                expert_evidence.validate_judgment_evidence(body, rubric, invalid, None, [])


def _published_reference(expert):
    source, judgments, adjudication = expert.adjudicated()
    with expert.factory() as session:
        dataset = expert_service.derive_dataset(
            session,
            expert.context(session),
            source.id,
            ReferenceDatasetInput(
                operation_id=uuid.uuid4(),
                expected_checksum=source.checksum,
                adjudication_id=adjudication.id,
                adjudication_checksum=adjudication.checksum,
            ),
        )
        session.commit()
        for action, actor in (
            ("submit", "creator"),
            ("approve", "approver"),
            ("publish", "creator"),
        ):
            foundation.transition_dataset(
                session,
                expert.context(session, actor),
                dataset.id,
                action,
                dataset.checksum,
                "synthetic:independent-dataset-review",
            )
            session.commit()
        return source, judgments, adjudication, dataset


def test_persisted_human_comparison_exact_ids_arithmetic_privacy_and_idempotency(expert):
    source, judgments, _, dataset = _published_reference(expert)
    payload = CalibrationInput(
        operation_id=uuid.uuid4(),
        dataset_version_id=dataset.id,
        dataset_checksum=dataset.checksum,
        comparison_kind="HUMAN_A_VS_HUMAN_B",
        left_id=judgments[0].id,
        left_checksum=judgments[0].checksum,
        right_id=judgments[1].id,
        right_checksum=judgments[1].checksum,
    )
    with expert.factory() as session:
        run = expert_comparison.calibrate(session, expert.context(session), payload)
        session.commit()
        assert run.status == "COMPLETED" and run.evidence_class == "SYNTHETIC_TEST"
        assert run.metrics["TASK.mean_absolute_error"]["value"] == 2
        assert run.metrics["TASK.agreement_rate"]["value"] == 0
        assert expert_comparison.calibrate(session, expert.context(session), payload).id == run.id
        detail = foundation.run_detail(session, expert.context(session), run.id)
        safe = json.dumps(detail)
        assert source.body["input"]["text"] not in safe
        assert judgments[0].body["criteria"][0]["rationale"] not in safe
        assert "UNCONFIGURED" in safe
        changed = payload.model_copy(
            update={
                "left_id": judgments[1].id,
                "left_checksum": judgments[1].checksum,
                "right_id": judgments[0].id,
                "right_checksum": judgments[0].checksum,
            }
        )
        with pytest.raises(EvaluationConflict, match="OPERATION_CONFLICT"):
            expert_comparison.calibrate(session, expert.context(session), changed)
        session.rollback()


def test_persisted_adjudication_comparison_and_wrong_reference_or_side_rejected(expert):
    _, judgments, adjudication, dataset = _published_reference(expert)
    payload = CalibrationInput(
        operation_id=uuid.uuid4(),
        dataset_version_id=dataset.id,
        dataset_checksum=dataset.checksum,
        comparison_kind="ADJUDICATED_VS_HUMAN",
        left_id=adjudication.id,
        left_checksum=adjudication.checksum,
        right_id=judgments[0].id,
        right_checksum=judgments[0].checksum,
    )
    with expert.factory() as session:
        run = expert_comparison.calibrate(session, expert.context(session), payload)
        session.commit()
        assert run.metrics["TASK.mean_absolute_error"]["value"] == 1
        for change in (
            {"right_id": uuid.uuid4()},
            {"right_checksum": "0" * 64},
            {"dataset_checksum": "0" * 64},
        ):
            with pytest.raises(EvaluationConflict):
                expert_comparison.calibrate(
                    session,
                    expert.context(session),
                    payload.model_copy(update={"operation_id": uuid.uuid4(), **change}),
                )
            session.rollback()


def test_arbitrary_proposal_or_human_judgment_cannot_impersonate_completed_ai_run(expert):
    _, judgments, _, dataset = _published_reference(expert)
    payload = CalibrationInput(
        operation_id=uuid.uuid4(),
        dataset_version_id=dataset.id,
        dataset_checksum=dataset.checksum,
        comparison_kind="AI_PROPOSAL_VS_REFERENCE",
        left_id=judgments[0].id,
        left_checksum=judgments[0].checksum,
        right_id=judgments[1].id,
        right_checksum=judgments[1].checksum,
    )
    with expert.factory() as session:
        with pytest.raises(EvaluationConflict, match="COMPLETED_PINNED_EXECUTION_REQUIRED"):
            expert_comparison.calibrate(session, expert.context(session), payload)
        session.rollback()


def test_expired_qualification_blocks_new_judgment_and_persisted_comparison(expert, monkeypatch):
    source, judgments, _, dataset = _published_reference(expert)
    later = datetime.now(timezone.utc) + timedelta(days=2)
    monkeypatch.setattr(foundation, "utcnow", lambda: later)
    payload = CalibrationInput(
        operation_id=uuid.uuid4(),
        dataset_version_id=dataset.id,
        dataset_checksum=dataset.checksum,
        comparison_kind="HUMAN_A_VS_HUMAN_B",
        left_id=judgments[0].id,
        left_checksum=judgments[0].checksum,
        right_id=judgments[1].id,
        right_checksum=judgments[1].checksum,
    )
    with expert.factory() as session:
        with pytest.raises((EvaluationConflict, AuthorizationError)):
            expert_comparison.calibrate(session, expert.context(session), payload)
        session.rollback()
        qualification = session.get(EvaluationExaminerQualification, judgments[0].qualification_id)
        with pytest.raises(AuthorizationError, match="QUALIFIED_EXAMINER"):
            expert_service.submit_judgment(
                session,
                expert.context(session, "examiner-a"),
                source.id,
                expert.judgment_payload(source, qualification),
            )
        session.rollback()


def test_persisted_transcript_comparison_uses_declared_normalization_without_speech_quality_claim(
    expert,
):
    source = expert.source(
        kind="TRANSCRIPTION",
        rubric_version_id=None,
        rubric_checksum=None,
        normalization="NFC_WHITESPACE_CASEFOLD",
        input={
            "language": "de",
            "modality": "AUDIO",
            "audio_kind": "TECHNICAL_TONE",
            "audio_sha256": uuid.uuid4().hex * 2,
            "duration_ms": 1000,
        },
    )
    judgments = []
    for actor, transcript in (("examiner-a", "GUTEN TAG"), ("examiner-b", "Guten Tag")):
        qualification = expert.qualification(source, actor)
        with expert.factory() as session:
            row = expert_service.submit_judgment(
                session,
                expert.context(session, actor),
                source.id,
                expert.judgment_payload(source, qualification, criteria=[], transcript=transcript),
            )
            session.commit()
            judgments.append(row)
    qualification = expert.qualification(source, "adjudicator")
    payload = expert.adjudication_payload(
        source, judgments, qualification, criteria=[], transcript="Guten Tag"
    )
    with expert.factory() as session:
        adjudication = expert_service.adjudicate(
            session, expert.context(session, "adjudicator"), source.id, payload
        )
        session.commit()
        dataset = expert_service.derive_dataset(
            session,
            expert.context(session),
            source.id,
            ReferenceDatasetInput(
                operation_id=uuid.uuid4(),
                expected_checksum=source.checksum,
                adjudication_id=adjudication.id,
                adjudication_checksum=adjudication.checksum,
            ),
        )
        session.commit()
        for action, actor in (
            ("submit", "creator"),
            ("approve", "approver"),
            ("publish", "creator"),
        ):
            foundation.transition_dataset(
                session,
                expert.context(session, actor),
                dataset.id,
                action,
                dataset.checksum,
                "synthetic:transcript-normalization-review",
            )
            session.commit()
        run = expert_comparison.calibrate(
            session,
            expert.context(session),
            CalibrationInput(
                operation_id=uuid.uuid4(),
                dataset_version_id=dataset.id,
                dataset_checksum=dataset.checksum,
                comparison_kind="HUMAN_A_VS_HUMAN_B",
                left_id=judgments[0].id,
                left_checksum=judgments[0].checksum,
                right_id=judgments[1].id,
                right_checksum=judgments[1].checksum,
            ),
        )
        session.commit()
        assert run.evidence_class == "SYNTHETIC_TEST"
        assert run.metrics["word_error_rate"]["value"] == 0
        assert run.metrics["character_error_rate"]["value"] == 0
        detail = foundation.run_detail(session, expert.context(session), run.id)
        serialized = json.dumps(detail)
        assert "GUTEN TAG" not in serialized and "Guten Tag" not in serialized
        assert "UNCONFIGURED" in serialized
        assert "No genuine spoken-audio recognition quality" in serialized


def _synthetic_document(
    expert,
    *,
    derivative=True,
    legal_status=LegalReviewStatus.APPROVED,
    scope=KnowledgeScope.PLATFORM_SHARED,
    document_status=KnowledgeVersionStatus.APPROVED,
):
    marker = uuid.uuid4().hex
    with expert.factory() as session:
        authority = KnowledgeAuthority(
            code="SYNTHETIC_" + marker,
            name="Synthetic test document owner",
            authority_type=KnowledgeAuthorityType.INTERNAL,
        )
        session.add(authority)
        session.flush()
        source = KnowledgeSource(
            authority_id=authority.id,
            source_code="synthetic-document-" + marker,
            title="ORIGINAL SYNTHETIC TEST MATERIAL",
            document_type=KnowledgeDocumentType.POLICY,
            scope=scope,
            language="de",
            assessment_family=AssessmentFamily.LANGUAGE,
        )
        session.add(source)
        session.flush()
        document = KnowledgeDocumentVersion(
            source_id=source.id,
            version_label="synthetic-1",
            status=document_status,
            sha256=marker * 2,
            notes="Original synthetic relevance fixture",
            created_at=datetime.now(timezone.utc),
            created_by_user_id=expert.actors["creator"],
            reviewed_at=datetime.now(timezone.utc),
            reviewed_by_user_id=expert.actors["approver"],
            approved_at=datetime.now(timezone.utc),
            approved_by_user_id=expert.actors["approver"],
        )
        session.add(document)
        session.flush()
        session.add(
            KnowledgeLegalMetadata(
                document_version_id=document.id,
                copyright_status="PRUFAI_ORIGINAL",
                licence_type="ORIGINAL_SYNTHETIC_TEST",
                rights_holder="Synthetic test fixture owner",
                usage_rights="SYNTHETIC_TEST evaluation-only relevance labeling",
                derivative_use_allowed=derivative,
                legal_review_status=legal_status,
                legal_reviewed_by_user_id=expert.actors["approver"],
                legal_reviewed_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
        return document


def test_real_registry_approved_synthetic_document_and_exact_relevance_labels(expert):
    document = _synthetic_document(expert)
    payload = expert.source_payload(
        kind="RETRIEVAL",
        rubric_version_id=None,
        rubric_checksum=None,
        knowledge_document_version_id=str(document.id),
        knowledge_document_checksum=document.sha256,
        input={
            "language": "de",
            "modality": "TEXT",
            "text": "Original synthetic relevance query " + uuid.uuid4().hex,
        },
    )
    with expert.factory() as session:
        row = expert_service.create_reference(session, expert.context(session), payload)
        session.commit()
        assert row.evidence_class == "SYNTHETIC_TEST"
        relevance = [
            {"document_version_id": str(document.id), "checksum": document.sha256, "relevant": True}
        ]
        expert_evidence.validate_judgment_evidence(row.body, None, [], None, relevance)
        for change in (
            {"checksum": "0" * 64},
            {"document_version_id": str(uuid.uuid4())},
            {"relevant": "true"},
        ):
            with pytest.raises(ValueError, match="IDENTITY"):
                expert_evidence.validate_judgment_evidence(
                    row.body, None, [], None, [{**relevance[0], **change}]
                )


@pytest.mark.parametrize(
    "change",
    [
        {"derivative": None},
        {"legal_status": LegalReviewStatus.PENDING},
        {"scope": KnowledgeScope.TENANT_PRIVATE},
        {"document_status": KnowledgeVersionStatus.DRAFT},
    ],
)
def test_document_reference_rejects_unknown_rights_unapproved_or_unowned_private_registry(
    expert, change
):
    document = _synthetic_document(expert, **change)
    payload = expert.source_payload(
        kind="RETRIEVAL",
        rubric_version_id=None,
        rubric_checksum=None,
        knowledge_document_version_id=str(document.id),
        knowledge_document_checksum=document.sha256,
        input={"language": "de", "modality": "TEXT", "text": "Original synthetic query"},
    )
    with expert.factory() as session:
        with pytest.raises(ValueError, match="APPROVED_DOCUMENT_RIGHTS_REQUIRED"):
            expert_service.create_reference(session, expert.context(session), payload)
        session.rollback()


def test_known_synthetic_examiner_cannot_acquire_verified_credential_claim(expert):
    source = expert.source()
    scope = {
        key: source.body[key]
        for key in (
            "organisation_id",
            "kind",
            "language",
            "rubric_version_id",
            "rubric_checksum",
            "standard_pack_version_id",
            "standard_pack_checksum",
        )
    }
    payload = QualificationInput(
        **scope,
        operation_id=uuid.uuid4(),
        examiner_id=expert.actors["examiner-a"],
        origin="VERIFIED_CREDENTIAL",
        credential_reference="credential:external-review",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    with expert.factory() as session:
        with pytest.raises((ValueError, AuthorizationError)):
            expert_service.create_qualification(session, expert.context(session), payload)
        session.rollback()


def test_reference_publication_operation_is_globally_bound_to_one_reference(expert):
    _, _, _, dataset = _published_reference(expert)
    other, _, adjudication = expert.adjudicated()
    with expert.factory() as session:
        with pytest.raises(EvaluationConflict, match="OPERATION_CONFLICT"):
            expert_service.derive_dataset(
                session,
                expert.context(session),
                other.id,
                ReferenceDatasetInput(
                    operation_id=uuid.UUID(dataset.provenance["operation_id"]),
                    expected_checksum=other.checksum,
                    adjudication_id=adjudication.id,
                    adjudication_checksum=adjudication.checksum,
                ),
            )
        session.rollback()


def test_final_reference_dataset_approval_excludes_original_examiners_and_adjudicator(expert):
    source, _, adjudication = expert.adjudicated()
    with expert.factory() as session:
        dataset = expert_service.derive_dataset(
            session,
            expert.context(session),
            source.id,
            ReferenceDatasetInput(
                operation_id=uuid.uuid4(),
                expected_checksum=source.checksum,
                adjudication_id=adjudication.id,
                adjudication_checksum=adjudication.checksum,
            ),
        )
        session.commit()
        foundation.transition_dataset(
            session,
            expert.context(session),
            dataset.id,
            "submit",
            dataset.checksum,
            "synthetic:dataset-independent-final-review",
        )
        session.commit()
        for actor in ("examiner-a", "examiner-b", "adjudicator"):
            with pytest.raises(AuthorizationError):
                foundation.transition_dataset(
                    session,
                    expert.context(session, actor),
                    dataset.id,
                    "approve",
                    dataset.checksum,
                    "synthetic:forbidden-own-evidence-approval",
                )
            session.rollback()
            assert session.get(EvaluationDatasetVersion, dataset.id).status == "REVIEW_REQUIRED"
        foundation.transition_dataset(
            session,
            expert.context(session, "approver"),
            dataset.id,
            "approve",
            dataset.checksum,
            "synthetic:independent-final-reference-approval",
        )
        session.commit()
        assert dataset.status == "APPROVED"


def test_receipt_backed_ai_reference_and_fixed_policy_ab_fail_closed(expert, original_audio):
    """Actual S3 audio -> separate TEST_ONLY worker -> exact governed comparison.

    The published rubric fixes one execution profile. Replaying that legitimate
    receipt twice must not be marketed as a different-model/prompt experiment.
    """
    proof, state, source_payload = original_audio
    queued = proof.queue(state)
    worker = proof.worker_once()
    job = proof.job(queued)
    assert "COMPLETED" in worker.stdout and job.status == "COMPLETED"
    assert job.provider_request_id.startswith("test-only-")
    with expert.factory() as session:
        role = session.scalar(
            select(Role).where(Role.key == "INSTITUTION_ADMIN", Role.scope == RoleScope.TENANT)
        )
        assert role is not None
        for actor in ("examiner-a", "examiner-b", "adjudicator"):
            membership = OrganisationMembership(
                organisation_id=job.organisation_id, user_id=expert.actors[actor]
            )
            session.add(membership)
            session.flush()
            session.add(MembershipRole(membership_id=membership.id, role_id=role.id))
        source = expert_service.create_reference(session, expert.context(session), source_payload)
        session.commit()
        source = expert_service.approve_reference(
            session,
            expert.context(session, "approver"),
            source.id,
            source.checksum,
            "synthetic:receipt-bound-original-audio-rights",
        )
        session.commit()
        rubric = session.get(RubricVersion, job.rubric_version_id)
        assert rubric is not None
        criteria = [
            {
                "criterion_code": row["code"],
                "points": "0",
                "level": rubric.performance_levels[0]["code"],
                "rationale": "SYNTHETIC TEST original-audio reference arithmetic only",
                "evidence": [
                    {
                        "asset_id": str(job.candidate_media_asset_id),
                        "audio_sha256": job.audio_sha256,
                        "candidate_response_revision_id": str(job.candidate_response_revision_id),
                        "start_ms": 0,
                        "end_ms": job.verified_duration_ms,
                        "time_unit": "milliseconds",
                    }
                ],
            }
            for row in rubric.criteria
        ]
    judgments = []
    for actor in ("examiner-a", "examiner-b"):
        qualification = expert.qualification(source, actor)
        with expert.factory() as session:
            judgment = expert_service.submit_judgment(
                session,
                expert.context(session, actor),
                source.id,
                expert.judgment_payload(source, qualification, criteria=criteria),
            )
            session.commit()
            judgments.append(judgment)
    qualification = expert.qualification(source, "adjudicator")
    with expert.factory() as session:
        adjudication = expert_service.adjudicate(
            session,
            expert.context(session, "adjudicator"),
            source.id,
            expert.adjudication_payload(source, judgments, qualification, criteria=criteria),
        )
        session.commit()
        publication = ReferenceDatasetInput(
            operation_id=uuid.uuid4(),
            expected_checksum=source.checksum,
            adjudication_id=adjudication.id,
            adjudication_checksum=adjudication.checksum,
            execution_job_id=job.id,
        )
        dataset = expert_service.derive_dataset(
            session, expert.context(session), source.id, publication
        )
        session.commit()
        assert (
            expert_service.derive_dataset(
                session, expert.context(session), source.id, publication
            ).id
            == dataset.id
        )
        session.rollback()
        with pytest.raises(EvaluationConflict, match="OPERATION_CONFLICT"):
            expert_service.derive_dataset(
                session,
                expert.context(session),
                source.id,
                publication.model_copy(update={"execution_job_id": None}),
            )
        session.rollback()
        for action, actor in (
            ("submit", "creator"),
            ("approve", "approver"),
            ("publish", "creator"),
        ):
            foundation.transition_dataset(
                session,
                expert.context(session, actor),
                dataset.id,
                action,
                dataset.checksum,
                "synthetic:independent-receipt-bound-reference-publication",
            )
            session.commit()
        counts = {
            model.__name__: session.scalar(select(func.count()).select_from(model))
            for model in (AIRun, HumanReviewRequest)
        }
        assert session.get(AIRun, job.ai_run_id) is not None
        configuration = {
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
            "execution_adapter": "PINNED_EXECUTION_REPLAY_V1",
            "evaluation_method": "DETERMINISTIC_REFERENCE_COMPARISON",
            "evaluation_method_version": "1",
            "dataset_split": source.split,
        }
        replay_request = RunRequest(
            operation_id=uuid.uuid4(),
            dataset_version_id=dataset.id,
            dataset_checksum=dataset.checksum,
            configuration=RunConfiguration(**configuration),
        )
        replay = foundation.execute_run(session, expert.context(session), replay_request)
        session.commit()
        assert replay.status == "COMPLETED" and replay.hard_gate == "PASS"
        assert replay.execution_class == "TEST_ONLY" and replay.evidence_class == "SYNTHETIC_TEST"
        replay_results = foundation.verify_run(session, replay)
        assert len(replay_results) == 1
        assert replay_results[0].execution_reference["execution_job_id"] == str(job.id)
        assert (
            replay_results[0].execution_reference["provider_request_id"] == job.provider_request_id
        )
        comparison_payload = CalibrationInput(
            operation_id=uuid.uuid4(),
            dataset_version_id=dataset.id,
            dataset_checksum=dataset.checksum,
            comparison_kind="AI_PROPOSAL_VS_REFERENCE",
            left_id=replay.id,
            left_checksum=replay.checksum,
            right_id=adjudication.id,
            right_checksum=adjudication.checksum,
        )
        comparison = expert_comparison.calibrate(
            session, expert.context(session), comparison_payload
        )
        session.commit()
        assert comparison.status == "COMPLETED" and comparison.hard_gate == "PASS"
        assert comparison.execution_class == "TEST_ONLY"
        assert comparison.evidence_class == "SYNTHETIC_TEST"
        for row in job.validated_proposal["criteria"]:
            measured = comparison.metrics[row["criterion_code"] + ".mean_absolute_error"]
            assert measured["sample_size"] == 1 and measured["missing_count"] == 0
            assert measured["value"] == float(row["proposed_points"])
        detail = foundation.run_detail(session, expert.context(session), comparison.id)
        serialized = json.dumps(detail)
        assert "UNCONFIGURED" in serialized
        assert source.body["rights_approval_reference"] not in serialized
        assert adjudication.body["rationale"] not in serialized
        assert str(job.candidate_response_revision_id) not in serialized
        assert source.body["input"]["audio_sha256"] not in serialized
        assert all(metric["evidence_class"] == "SYNTHETIC_TEST" for metric in detail["metrics"])
        # A result ID is eligible only with its exact stored checksum/receipt.
        by_result = expert_comparison.calibrate(
            session,
            expert.context(session),
            comparison_payload.model_copy(
                update={
                    "operation_id": uuid.uuid4(),
                    "left_id": replay_results[0].id,
                    "left_checksum": replay_results[0].checksum,
                }
            ),
        )
        session.commit()
        assert by_result.hard_gate == "PASS" and by_result.evidence_class == "SYNTHETIC_TEST"
        replay_b = foundation.execute_run(
            session,
            expert.context(session),
            replay_request.model_copy(update={"operation_id": uuid.uuid4()}),
        )
        session.commit()
        assert replay_b.id != replay.id
        ab = comparison_payload.model_copy(
            update={
                "operation_id": uuid.uuid4(),
                "comparison_kind": "VERSION_A_VS_B",
                "right_id": replay_b.id,
                "right_checksum": replay_b.checksum,
            }
        )
        with pytest.raises(EvaluationConflict, match="DISTINCT_COMPATIBLE_VERSIONS"):
            expert_comparison.calibrate(session, expert.context(session), ab)
        session.rollback()
        for change in ({"left_checksum": "0" * 64}, {"left_id": job.ai_run_id}):
            with pytest.raises(EvaluationConflict, match="COMPLETED_PINNED_EXECUTION_REQUIRED"):
                expert_comparison.calibrate(
                    session,
                    expert.context(session),
                    comparison_payload.model_copy(
                        update={
                            "operation_id": uuid.uuid4(),
                            **change,
                        }
                    ),
                )
            session.rollback()
        assert counts == {
            model.__name__: session.scalar(select(func.count()).select_from(model))
            for model in (AIRun, HumanReviewRequest)
        }
    for absent in ("candidate_media_asset_id", "candidate_response_revision_id"):
        missing = source_payload.model_dump(mode="json")
        missing["operation_id"], missing["code"] = str(uuid.uuid4()), "missing-" + uuid.uuid4().hex
        missing["input"]["evidence_identity"].pop(absent)
        with expert.factory() as session:
            with pytest.raises(
                ValueError,
                match="REFERENCE_(EXACT_EVIDENCE_IDENTITY_MISMATCH|"
                "ORIGINAL_AUDIO_REQUIRED|EXACT_RESPONSE_REQUIRED)",
            ):
                expert_service.create_reference(
                    session, expert.context(session), SourceInput.model_validate(missing)
                )
            session.rollback()
    with expert.factory() as session:
        invalid = replay_request.model_copy(
            update={
                "operation_id": uuid.uuid4(),
                "configuration": replay_request.configuration.model_copy(
                    update={"prompt_version_id": uuid.uuid4()}
                ),
            }
        )
        with pytest.raises(ValueError):
            foundation.execute_run(session, expert.context(session), invalid)
        session.rollback()


def test_manager_derives_from_exact_metadata_without_other_examiner_rationale(expert):
    source, judgments, adjudication = expert.adjudicated()
    with expert.factory() as session:
        workbench = expert_service.workbench(session, expert.context(session), source.id)
        assert "DERIVE_DATASET" in workbench["allowed_actions"]
        assert workbench["qualifications"] == []
        assert workbench["judgments"] == []
        metadata = workbench["adjudications"][-1]
        assert metadata == {
            "id": str(adjudication.id),
            "checksum": adjudication.checksum,
            "version_number": adjudication.version_number,
            "body": {},
        }
        rubric = session.get(RubricVersion, expert.rubric_id)
        assert workbench["rubric"]["aggregation_policy"] == rubric.aggregation_policy
        assert workbench["rubric"]["evidence_policy"] == rubric.evidence_policy
        dataset = expert_service.derive_dataset(
            session,
            expert.context(session),
            source.id,
            ReferenceDatasetInput(
                operation_id=uuid.uuid4(),
                expected_checksum=source.checksum,
                adjudication_id=metadata["id"],
                adjudication_checksum=metadata["checksum"],
            ),
        )
        session.commit()
        assert dataset.status == "DRAFT"
        assert dataset.provenance["adjudication_checksum"] == adjudication.checksum
        role_ids = list(
            session.scalars(
                select(PlatformRoleAssignment.role_id).where(
                    PlatformRoleAssignment.user_id == expert.actors["creator"]
                )
            )
        )
        grants = list(
            session.scalars(
                select(RolePermission)
                .join(Permission)
                .where(RolePermission.role_id.in_(role_ids), Permission.key == foundation.RUN)
            )
        )
        assert grants
        for grant in grants:
            session.delete(grant)
        session.flush()
        limited = expert_service.workbench(session, expert.context(session), source.id)
        assert "DERIVE_DATASET" not in limited["allowed_actions"]
        assert limited["adjudications"][-1]["body"] == {}
        session.rollback()
        # The general run projection must preserve the actual array contract.
        source, judgments, adjudication, published = _published_reference(expert)
        run = expert_comparison.calibrate(
            session,
            expert.context(session),
            CalibrationInput(
                operation_id=uuid.uuid4(),
                dataset_version_id=published.id,
                dataset_checksum=published.checksum,
                comparison_kind="HUMAN_A_VS_HUMAN_B",
                left_id=judgments[0].id,
                left_checksum=judgments[0].checksum,
                right_id=judgments[1].id,
                right_checksum=judgments[1].checksum,
            ),
        )
        session.commit()
        comparison = foundation.run_detail(session, expert.context(session), run.id)["comparison"]
        distribution = comparison["criteria"][0]["disagreement_distribution"]
        assert isinstance(distribution, list) and distribution
        assert set(distribution[0]) == {"difference", "absolute_difference", "count", "rate"}
