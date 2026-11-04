"""Governed synthetic evaluation on a fresh, Alembic-migrated PostgreSQL DB.

These tests use original synthetic expectations and replayed observations. They
prove metric arithmetic, authorization and immutable persistence, never genuine
provider inference, expert calibration or German assessment validity.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.api import auth_settings_dependency
from app.auth.service import AuthenticationService
from app.authorization.seed import seed_defaults
from app.config import Settings, get_settings
from app.db import get_db_session
from app.main import app
from app.models import (
    EvaluationDatasetVersion,
    EvaluationRun,
    MembershipRole,
    Organisation,
    OrganisationMembership,
    Permission,
    PlatformRoleAssignment,
    Role,
    RolePermission,
    StandardPack,
    StandardPackVersion,
    User,
)
from app.models.enums import (
    AssessmentFamily,
    OrganisationType,
    PlatformAssignmentStatus,
    RoleScope,
    StandardPackStatus,
    UserStatus,
)
from app.standard_packs import StandardPackService
from tests.alembic_helpers import repository_alembic_head

PREFIX = "/api/v1/platform/evaluation"
CONTROL = "/api/v1/platform/control-center/evaluation"
API_ROOT = Path(__file__).resolve().parents[1]
REQUIRED = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRED,
    reason="Evaluation foundation requires real Alembic PostgreSQL",
)
PASSWORD = "Synthetic-Evaluation-Only-2026!"
PRIVATE_TEXT = "PRIVATE SYNTHETIC TEST DATA — NOT OFFICIAL EXAM MATERIAL — Guten Tag"


def technical_case(*, case_id: str = "original-de-1") -> dict[str, Any]:
    identity = {"audio_sha256": "a" * 64}
    return {
        "case_id": case_id,
        "kind": "TRANSCRIPTION",
        "split": "TEST",
        "input": {
            "text": PRIVATE_TEXT,
            "language": "de",
            "modality": "AUDIO",
            "audio_sha256": "a" * 64,
            "duration_ms": 1000,
            "audio_kind": "TECHNICAL_TONE",
            "evidence_identity": identity,
        },
        "reference": {
            "label_kind": "SYNTHETIC_EXPECTATION",
            "text": "Guten Tag",
            "language": "de",
            "evidence_identity": identity,
        },
        "synthetic_observation": {
            "text": "Guten Tag",
            "language": "de",
            "audio_sha256": "a" * 64,
            "evidence_identity": identity,
            "modality": "AUDIO",
        },
    }


def dataset_body(*, cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "code": "synthetic-evaluation-" + uuid.uuid4().hex,
        "assessment_family": "LANGUAGE",
        "language": "de",
        "cefr_level": "B1",
        "exam_scope": "ORIGINAL_SYNTHETIC_TECHNICAL_ARITHMETIC",
        "skill": "TRANSCRIPTION",
        "purpose": "SYNTHETIC_REGRESSION",
        "origin": "ORIGINAL_SYNTHETIC",
        "source_legal_status": "OWNED_ORIGINAL",
        "source_reference": "original-synthetic:integration-fixture",
        "label_provenance": "SYNTHETIC_EXPECTATION",
        "expert_review_status": "NOT_EXPERT_REVIEWED",
        "retention_classification": "SYNTHETIC_TEST",
        "cases": cases if cases is not None else [technical_case()],
    }


def criterion_case(kind: str) -> dict[str, Any]:
    identity = {"response_checksum": "b" * 64}
    input_body: dict[str, Any] = {
        "text": "Guten Tag",
        "language": "de",
        "modality": "TEXT",
        "evidence_identity": identity,
    }
    evidence = [{"start": 0, "end": 5, "quote": "Guten"}]
    if kind == "SPEAKING":
        identity = {"candidate_media_asset_id": "synthetic-asset-1", "audio_sha256": "a" * 64}
        input_body = {
            "language": "de",
            "modality": "AUDIO",
            "audio_kind": "TECHNICAL_TONE",
            "audio_sha256": "a" * 64,
            "duration_ms": 1000,
            "evidence_identity": identity,
        }
        evidence = [
            {
                "type": "AUDIO",
                "candidate_media_asset_id": "synthetic-asset-1",
                "audio_sha256": "a" * 64,
                "start_ms": 0,
                "end_ms": 500,
            }
        ]
    observation: dict[str, Any] = {
        "modality": input_body["modality"],
        "evidence_identity": identity,
        "criteria": [{"criterion_code": "CONTENT", "proposed_points": "3", "evidence": evidence}],
    }
    if kind == "SPEAKING":
        observation["audio_sha256"] = "a" * 64
    return {
        "case_id": "synthetic-criterion-" + kind.lower(),
        "kind": kind,
        "split": "TEST",
        "input": input_body,
        "reference": {
            "label_kind": "SYNTHETIC_EXPECTATION",
            "criteria": [{"code": "CONTENT", "points": "2", "max_points": "4"}],
            "evidence_identity": identity,
        },
        "synthetic_observation": observation,
    }


@dataclass
class EvaluationScenario:
    engine: Engine
    factory: Any
    client: TestClient
    settings: Settings = field(repr=False)
    actors: dict[str, uuid.UUID]
    organisations: list[uuid.UUID]
    tokens: dict[str, str] = field(default_factory=dict, repr=False)
    passwords: dict[str, str] = field(default_factory=dict, repr=False)

    def headers(self, actor: str = "creator") -> dict[str, str]:
        if actor not in self.tokens:
            with self.factory() as session:
                user = session.get(User, self.actors[actor])
                email = user.email
            response = self.client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": self.passwords.get(actor, PASSWORD)},
            )
            assert response.status_code == 200, response.text
            self.tokens[actor] = response.json()["access_token"]
        return {"Authorization": "Bearer " + self.tokens[actor]}

    def request(
        self,
        method: str,
        path: str,
        *,
        actor: str = "creator",
        expected: int | tuple[int, ...] = 200,
        **kwargs: Any,
    ) -> Any:
        response = self.client.request(method, path, headers=self.headers(actor), **kwargs)
        expected_values = (expected,) if isinstance(expected, int) else expected
        assert response.status_code in expected_values, (method, path, response.text)
        return response.json()

    def create(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request(
            "POST", PREFIX + "/datasets", json=body or dataset_body(), expected=(200, 201)
        )

    def published(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        version = self.create(body)
        path = PREFIX + "/dataset-versions/" + version["id"]
        checksum = {"expected_checksum": version["checksum"]}
        self.request("POST", path + "/submit", json=checksum)
        self.request(
            "POST",
            path + "/approve",
            actor="reviewer",
            json={**checksum, "provenance_review_reference": "synthetic:independent-review"},
        )
        return self.request("POST", path + "/publish", json=checksum)

    def configuration(self) -> dict[str, Any]:
        with self.factory() as session:
            pack = StandardPack(
                code="SYNTHETIC_EVAL_" + uuid.uuid4().hex,
                name="SYNTHETIC TEST DATA — NOT OFFICIAL EXAM MATERIAL",
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
            session.commit()
            return {
                "standard_pack_version_id": str(version.id),
                "standard_pack_checksum": version.configuration_checksum,
                "rubric_version_id": None,
                "rubric_checksum": None,
                "question_bank_item_version_id": None,
                "item_checksum": None,
                "model_profile_id": None,
                "model_version": None,
                "prompt_version_id": None,
                "agent_version_id": None,
                "workflow_version_id": None,
                "profile_version": None,
                "execution_adapter": "SYNTHETIC_OBSERVATION_REPLAY_V1",
                "evaluation_method": "DETERMINISTIC_REFERENCE_COMPARISON",
                "evaluation_method_version": "1",
                "metric_configuration": {"retrieval_k": 5},
            }

    def run_body(self, version: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation_id": str(uuid.uuid4()),
            "dataset_version_id": version["id"],
            "dataset_checksum": version["checksum"],
            "configuration": self.configuration(),
        }

    def execute(self, body: dict[str, Any]) -> dict[str, Any]:
        value = self.request("POST", PREFIX + "/runs", json=body, expected=(200, 201))
        summary = value.get("run", value)
        return self.request("GET", PREFIX + "/runs/" + summary["id"])


@pytest.fixture(scope="module")
def evaluation() -> Iterator[EvaluationScenario]:
    configured = os.getenv("POSTGRES_INTEGRATION_URL")
    if not configured:
        pytest.fail("Required evaluation gate needs POSTGRES_INTEGRATION_URL")
    source = make_url(configured)
    database = "prufai_evaluation_" + uuid.uuid4().hex
    fresh = source.set(database=database).render_as_string(hide_password=False)
    control = source.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    with psycopg.connect(control, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    engine = None
    previous = dict(app.dependency_overrides)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=API_ROOT,
            env={**os.environ, "DATABASE_URL": fresh},
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stderr
        engine = create_engine(fresh)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        settings = Settings(
            _env_file=None,
            environment="test",
            auth_rate_limit_backend="memory",
            auth_rate_limit_login_attempts=100,
            auth_jwt_secret="synthetic-evaluation-integration-" + uuid.uuid4().hex,
        )
        actors: dict[str, uuid.UUID] = {}
        organisations: list[uuid.UUID] = []
        grants = {
            "creator": {
                "standards.evaluation.read",
                "standards.evaluation.run",
                "ai.manage",
                "ai.review",
            },
            "reviewer": {
                "standards.evaluation.read",
                "standards.evaluation.run",
                "ai.review",
                "ai.manage",
            },
            "observer": {"platform.control_center.read", "standards.evaluation.read"},
            "runner": {"standards.evaluation.read", "standards.evaluation.run"},
        }
        with factory() as session:
            seed_defaults(session)
            for name in (*grants, "tenant-a", "tenant-b"):
                email = name + "-" + uuid.uuid4().hex + "@evaluation.fixture.invalid"
                user = User(email=email, normalized_email=email, status=UserStatus.ACTIVE)
                session.add(user)
                session.flush()
                AuthenticationService(settings).set_password(session, user, PASSWORD)
                actors[name] = user.id
                if name in grants:
                    role = Role(key="EVAL_" + uuid.uuid4().hex, scope=RoleScope.PLATFORM)
                    session.add(role)
                    session.flush()
                    for key in grants[name]:
                        permission = session.scalar(select(Permission).where(Permission.key == key))
                        assert permission is not None
                        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
                    session.add(PlatformRoleAssignment(user_id=user.id, role_id=role.id))
                else:
                    organisation = Organisation(
                        name="Original synthetic evaluation tenant",
                        slug="evaluation-" + uuid.uuid4().hex,
                        organisation_type=OrganisationType.TRAINING_PROVIDER,
                    )
                    session.add(organisation)
                    session.flush()
                    organisations.append(organisation.id)
                    membership = OrganisationMembership(
                        organisation_id=organisation.id, user_id=user.id
                    )
                    session.add(membership)
                    session.flush()
                    role = session.scalar(select(Role).where(Role.key == "INSTITUTION_ADMIN"))
                    if role is not None:
                        session.add(MembershipRole(membership_id=membership.id, role_id=role.id))
            session.commit()

        def database_session() -> Iterator[Session]:
            with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = database_session
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[auth_settings_dependency] = lambda: settings
        with TestClient(app) as client:
            yield EvaluationScenario(engine, factory, client, settings, actors, organisations)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        if engine is not None:
            engine.dispose()
        with psycopg.connect(control, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )


def test_evaluation_database_is_migration_backed(evaluation: EvaluationScenario) -> None:
    with evaluation.engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalars().all() == [repository_alembic_head()]
        tables = set(
            connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            ).scalars()
        )
        assert {
            "evaluation_datasets",
            "evaluation_dataset_versions",
            "evaluation_runs",
            "evaluation_case_results",
            "evaluation_reviews",
        } <= tables


def test_exact_checksum_independent_approval_and_publication(
    evaluation: EvaluationScenario,
) -> None:
    version = evaluation.create()
    assert version["status"] == "DRAFT" and version["version_number"] == 1
    assert len(version["checksum"]) == 64
    path = PREFIX + "/dataset-versions/" + version["id"]
    checksum = {"expected_checksum": version["checksum"]}
    evaluation.request(
        "POST", path + "/submit", json={"expected_checksum": "f" * 64}, expected=(409, 422)
    )
    evaluation.request("POST", path + "/submit", json=checksum)
    approval = {**checksum, "provenance_review_reference": "synthetic:independent-review"}
    evaluation.request("POST", path + "/approve", json=approval, expected=403)
    approved = evaluation.request("POST", path + "/approve", actor="reviewer", json=approval)
    assert approved["status"] == "APPROVED"
    assert approved["checksum"] == version["checksum"]
    published = evaluation.request("POST", path + "/publish", json=checksum)
    assert published["status"] == "PUBLISHED"
    assert published["checksum"] == version["checksum"]


@pytest.mark.parametrize("actor", ["tenant-a", "tenant-b"])
def test_tenant_principals_and_forged_platform_headers_are_denied(
    evaluation: EvaluationScenario, actor: str
) -> None:
    version = evaluation.create()
    headers = {
        **evaluation.headers(actor),
        "X-PrufAI-Platform-Admin": "true",
        "X-PrufAI-Permissions": "standards.evaluation.read,ai.review,ai.manage",
    }
    for path in (
        CONTROL + "?kind=datasets",
        PREFIX + "/dataset-versions/" + version["id"] + "/cases",
    ):
        response = evaluation.client.get(path, headers=headers)
        assert response.status_code == 403
    response = evaluation.client.post(PREFIX + "/datasets", headers=headers, json=dataset_body())
    assert response.status_code == 403


def test_platform_tenant_context_is_rejected(evaluation: EvaluationScenario) -> None:
    response = evaluation.client.post(
        PREFIX + "/datasets",
        headers={
            **evaluation.headers(),
            "X-PrufAI-Organisation-ID": str(evaluation.organisations[0]),
        },
        json=dataset_body(),
    )
    assert response.status_code in (400, 403)


@pytest.mark.parametrize(
    "key,value",
    [
        ("origin", "REAL_CANDIDATE"),
        ("source_legal_status", "UNREVIEWED"),
        ("label_provenance", "ADJUDICATED_EXPERT"),
        ("expert_review_status", "EXPERT_CALIBRATED"),
        ("evidence_class", "PILOT_CALIBRATED"),
    ],
)
def test_original_synthetic_dataset_cannot_be_relabelled_as_validated(
    evaluation: EvaluationScenario, key: str, value: str
) -> None:
    body = dataset_body()
    body[key] = value
    evaluation.request("POST", PREFIX + "/datasets", json=body, expected=422)


def test_split_leakage_and_duplicate_case_ids_are_rejected(evaluation: EvaluationScenario) -> None:
    first = technical_case()
    second = copy.deepcopy(first)
    second["case_id"] = "duplicate-evidence-other-split"
    second["split"] = "TRAIN"
    evaluation.request(
        "POST", PREFIX + "/datasets", json=dataset_body(cases=[first, second]), expected=422
    )
    second["case_id"] = first["case_id"]
    second["split"] = "TEST"
    evaluation.request(
        "POST", PREFIX + "/datasets", json=dataset_body(cases=[first, second]), expected=422
    )


def test_lid_never_acquires_a_cefr_level(evaluation: EvaluationScenario) -> None:
    body = dataset_body()
    body["assessment_family"] = "INTEGRATION_KNOWLEDGE"
    evaluation.request("POST", PREFIX + "/datasets", json=body, expected=422)
    body["cefr_level"] = None
    assert evaluation.create(body)["id"]


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_published_dataset_version_resists_raw_sql_rewrite(
    evaluation: EvaluationScenario, operation: str
) -> None:
    version = evaluation.published()
    query = (
        "UPDATE evaluation_dataset_versions SET checksum=:checksum WHERE id=:id"
        if operation == "UPDATE"
        else "DELETE FROM evaluation_dataset_versions WHERE id=:id"
    )
    with pytest.raises(DBAPIError, match="(?i)immutable|append.only|history|lifecycle.conflict"):
        with evaluation.engine.begin() as connection:
            connection.execute(text(query), {"id": uuid.UUID(version["id"]), "checksum": "f" * 64})
    with evaluation.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT checksum FROM evaluation_dataset_versions WHERE id=:id"),
                {"id": uuid.UUID(version["id"])},
            ).scalar_one()
            == version["checksum"]
        )


def test_successor_version_keeps_prior_published_evidence_immutable(
    evaluation: EvaluationScenario,
) -> None:
    body = dataset_body()
    original = evaluation.published(body)
    successor_body = copy.deepcopy(body)
    successor_body["cases"][0]["synthetic_observation"]["text"] = "Guten guten Tag"
    successor = evaluation.request(
        "POST",
        PREFIX + "/datasets/" + original["dataset_id"] + "/versions",
        json=successor_body,
        expected=(200, 201),
    )
    assert successor["id"] != original["id"]
    assert successor["version_number"] == 2
    assert successor["checksum"] != original["checksum"]
    with evaluation.engine.connect() as connection:
        rows = connection.execute(
            text("SELECT status,checksum FROM evaluation_dataset_versions WHERE id=:id"),
            {"id": uuid.UUID(original["id"])},
        ).one()
        assert rows == ("PUBLISHED", original["checksum"])


def test_private_cases_require_review_permission_and_never_enter_control_summary(
    evaluation: EvaluationScenario,
) -> None:
    version = evaluation.published()
    path = PREFIX + "/dataset-versions/" + version["id"] + "/cases"
    evaluation.request("GET", path, actor="observer", expected=403)
    evidence = evaluation.request("GET", path, actor="reviewer")
    assert PRIVATE_TEXT in json.dumps(evidence, ensure_ascii=False)
    summary = evaluation.request("GET", CONTROL + "?kind=datasets", actor="observer")
    serialized = json.dumps(summary, ensure_ascii=False)
    assert PRIVATE_TEXT not in serialized
    assert "Guten Tag" not in serialized
    assert "synthetic_observation" not in serialized
    assert "cases" not in serialized


def test_live_platform_revocation_blocks_existing_jwt(evaluation: EvaluationScenario) -> None:
    headers = evaluation.headers("observer")
    assert evaluation.client.get(CONTROL + "?kind=datasets", headers=headers).status_code == 200
    with evaluation.factory() as session:
        assignment = session.scalar(
            select(PlatformRoleAssignment).where(
                PlatformRoleAssignment.user_id == evaluation.actors["observer"]
            )
        )
        assignment.status = PlatformAssignmentStatus.SUSPENDED
        session.commit()
    try:
        response = evaluation.client.get(CONTROL + "?kind=datasets", headers=headers)
        assert response.status_code == 403
    finally:
        with evaluation.factory() as session:
            assignment = session.scalar(
                select(PlatformRoleAssignment).where(
                    PlatformRoleAssignment.user_id == evaluation.actors["observer"]
                )
            )
            assignment.status = PlatformAssignmentStatus.ACTIVE
            session.commit()


def test_run_pins_exact_versions_and_measures_arithmetic_without_quality_claims(
    evaluation: EvaluationScenario,
) -> None:
    case = technical_case()
    case["synthetic_observation"]["text"] = "Guten guten Tag"
    version = evaluation.published(dataset_body(cases=[case]))
    body = evaluation.run_body(version)
    detail = evaluation.execute(body)
    run = detail["run"]
    assert run["status"] == "COMPLETED" and run["hard_gate"] == "PASS"
    assert run["evidence_class"] == "TECHNICAL_TEST"
    assert run["execution_class"] == "TEST_ONLY"
    metrics = {metric["name"]: metric for metric in detail["metrics"]}
    measured = metrics["word_error_rate"]
    assert measured["value"] == pytest.approx(0.5)
    assert float(measured["numerator"]) == 1
    assert float(measured["denominator"]) == 2
    assert measured["sample_size"] > 0 and measured["evidence_class"] == "TECHNICAL_TEST"
    assert measured["limitations"]
    assert (
        detail["pins"]["standard_pack_version_id"]
        == body["configuration"]["standard_pack_version_id"]
    )
    assert (
        detail["pins"]["standard_pack_checksum"] == body["configuration"]["standard_pack_checksum"]
    )
    assert detail["pins"]["execution_adapter"] == "SYNTHETIC_OBSERVATION_REPLAY_V1"
    assert "Guten" not in json.dumps(detail, ensure_ascii=False)
    assert "PILOT_CALIBRATED" not in {metric["evidence_class"] for metric in detail["metrics"]}
    with evaluation.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM evaluation_case_results WHERE run_id=:run"),
                {"run": uuid.UUID(run["id"])},
            ).scalar_one()
            == 1
        )


def test_same_pinned_run_is_idempotent_and_mismatched_replay_is_rejected(
    evaluation: EvaluationScenario,
) -> None:
    version = evaluation.published()
    body = evaluation.run_body(version)
    original = evaluation.execute(body)
    repeated = evaluation.execute(body)
    assert repeated["run"]["id"] == original["run"]["id"]
    assert repeated["run"]["checksum"] == original["run"]["checksum"]
    mismatched = copy.deepcopy(body)
    mismatched["configuration"]["metric_configuration"]["retrieval_k"] = 7
    evaluation.request("POST", PREFIX + "/runs", json=mismatched, expected=409)


@pytest.mark.parametrize("pin", ["dataset_checksum", "standard_pack_checksum"])
def test_stale_exact_pin_is_rejected_before_any_evaluation_run(
    evaluation: EvaluationScenario, pin: str
) -> None:
    body = evaluation.run_body(evaluation.published())
    target = body if pin == "dataset_checksum" else body["configuration"]
    target[pin] = "f" * 64
    evaluation.request("POST", PREFIX + "/runs", json=body, expected=(409, 422))
    with evaluation.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM evaluation_runs WHERE operation_id=:operation"),
                {"operation": uuid.UUID(body["operation_id"])},
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize(
    "field_name,value,category",
    [
        ("audio_sha256", "b" * 64, "AUDIO_IDENTITY_MISMATCH"),
        ("language", "en", "UNSUPPORTED_LANGUAGE"),
        ("actions", ["APPROVE_ASSESSMENT"], "PROMPT_INJECTION_POLICY_VIOLATION"),
    ],
)
def test_deliberate_observation_failures_are_measured_and_cannot_be_human_accepted(
    evaluation: EvaluationScenario, field_name: str, value: Any, category: str
) -> None:
    case = technical_case()
    case["synthetic_observation"][field_name] = value
    version = evaluation.published(dataset_body(cases=[case]))
    detail = evaluation.execute(evaluation.run_body(version))
    run = detail["run"]
    assert run["hard_gate"] == "FAIL" and run["failure_count"] == 1
    assert any(category in row["failure_categories"] for row in detail["failures"])
    review = {
        "expected_run_checksum": run["checksum"],
        "decision": "ACCEPTED",
        "review_reference": "synthetic:cannot-override-hard-fail",
    }
    evaluation.request(
        "POST",
        PREFIX + "/runs/" + run["id"] + "/reviews",
        actor="reviewer",
        json=review,
        expected=409,
    )
    review["decision"] = "CHANGES_REQUIRED"
    evaluation.request(
        "POST",
        PREFIX + "/runs/" + run["id"] + "/reviews",
        actor="reviewer",
        json=review,
        expected=(200, 201),
    )


def test_completed_run_results_and_review_are_append_only_in_postgresql(
    evaluation: EvaluationScenario,
) -> None:
    version = evaluation.published()
    detail = evaluation.execute(evaluation.run_body(version))
    run = detail["run"]
    review = {
        "expected_run_checksum": run["checksum"],
        "decision": "ACCEPTED",
        "review_reference": "synthetic:independent-run-review",
    }
    path = PREFIX + "/runs/" + run["id"] + "/reviews"
    evaluation.request("POST", path, json=review, expected=403)
    evaluation.request("POST", path, actor="reviewer", json=review, expected=(200, 201))
    for statement in (
        "UPDATE evaluation_runs SET checksum=:checksum WHERE id=:id",
        "DELETE FROM evaluation_runs WHERE id=:id",
        "UPDATE evaluation_case_results SET checksum=:checksum WHERE run_id=:id",
        "DELETE FROM evaluation_case_results WHERE run_id=:id",
        "UPDATE evaluation_reviews SET decision='REJECTED' WHERE run_id=:id",
        "DELETE FROM evaluation_reviews WHERE run_id=:id",
    ):
        with pytest.raises(DBAPIError, match="(?i)immutable|append.only|history"):
            with evaluation.engine.begin() as connection:
                connection.execute(
                    text(statement), {"id": uuid.UUID(run["id"]), "checksum": "f" * 64}
                )
    unchanged = evaluation.request("GET", PREFIX + "/runs/" + run["id"])
    assert unchanged["run"]["checksum"] == run["checksum"]
    assert len(unchanged["reviews"]) == 1
    assert unchanged["reviews"][0]["decision"] == "ACCEPTED"


def test_comparison_is_reproducible_and_does_not_activate_an_empirical_threshold(
    evaluation: EvaluationScenario,
) -> None:
    version = evaluation.published()
    body = evaluation.run_body(version)
    first = evaluation.execute(body)
    repeated = copy.deepcopy(body)
    repeated["operation_id"] = str(uuid.uuid4())
    second = evaluation.execute(repeated)
    assert first["metrics"] == second["metrics"]
    comparison = evaluation.request(
        "GET",
        PREFIX + "/runs/compare?left_id=" + first["run"]["id"] + "&right_id=" + second["run"]["id"],
        actor="observer",
    )
    assert PRIVATE_TEXT not in json.dumps(comparison, ensure_ascii=False)
    assert "Guten Tag" not in json.dumps(comparison, ensure_ascii=False)
    assert comparison
    assert comparison.get("empirical_thresholds", "NONE") == "NONE"


def test_unpublished_dataset_and_fabricated_execution_class_are_not_executable(
    evaluation: EvaluationScenario,
) -> None:
    body = evaluation.run_body(evaluation.create())
    evaluation.request("POST", PREFIX + "/runs", json=body, expected=(409, 422))
    body = evaluation.run_body(evaluation.published())
    body["configuration"]["execution_adapter"] = "REAL_PROVIDER"
    evaluation.request("POST", PREFIX + "/runs", json=body, expected=422)


@pytest.mark.parametrize("kind", ["WRITING", "SPEAKING"])
def test_criterion_comparison_is_actual_synthetic_arithmetic(
    evaluation: EvaluationScenario, kind: str
) -> None:
    body = dataset_body(cases=[criterion_case(kind)])
    body["skill"] = kind
    version = evaluation.published(body)
    detail = evaluation.execute(evaluation.run_body(version))
    metrics = {metric["name"]: metric for metric in detail["metrics"]}
    arithmetic = metrics["mean_absolute_criterion_error"]
    assert arithmetic["value"] == 1
    assert arithmetic["sample_size"] == 1
    assert arithmetic["evidence_class"] == "TECHNICAL_TEST"
    assert detail["run"]["hard_gate"] == "PASS"
    if kind == "SPEAKING":
        assert metrics["acoustic_assessment_validity"]["value"] is None
        assert metrics["acoustic_assessment_validity"]["unavailable_reason"]


def test_missing_reference_is_unavailable_rather_than_invented_quality(
    evaluation: EvaluationScenario,
) -> None:
    case = criterion_case("WRITING")
    case["reference"]["criteria"][0]["points"] = None
    body = dataset_body(cases=[case])
    body["skill"] = "WRITING"
    detail = evaluation.execute(evaluation.run_body(evaluation.published(body)))
    metric = next(
        row for row in detail["metrics"] if row["name"] == "mean_absolute_criterion_error"
    )
    assert metric["value"] is None
    assert metric["sample_size"] == 0
    assert metric["missing_count"] >= 1


def test_transcript_only_cannot_become_acoustic_speaking_evidence(
    evaluation: EvaluationScenario,
) -> None:
    case = criterion_case("SPEAKING")
    case["synthetic_observation"]["modality"] = "TRANSCRIPT"
    body = dataset_body(cases=[case])
    body["skill"] = "SPEAKING"
    detail = evaluation.execute(evaluation.run_body(evaluation.published(body)))
    assert detail["run"]["hard_gate"] == "FAIL"
    assert any(
        "UNSUPPORTED_MODEL_MODALITY" in row["failure_categories"] for row in detail["failures"]
    )


@pytest.mark.parametrize("kind", ["WRITING", "SPEAKING"])
def test_out_of_range_proposals_and_missing_criteria_fail_measured_gate(
    evaluation: EvaluationScenario, kind: str
) -> None:
    case = criterion_case(kind)
    case["synthetic_observation"]["criteria"][0]["proposed_points"] = "5"
    missing = copy.deepcopy(case)
    missing["case_id"] += "-missing"
    missing["synthetic_observation"]["criteria"] = []
    body = dataset_body(cases=[case, missing])
    body["skill"] = kind
    detail = evaluation.execute(evaluation.run_body(evaluation.published(body)))
    assert detail["run"]["hard_gate"] == "FAIL"
    assert detail["run"]["failure_count"] == 2


def test_missing_pinned_job_cannot_fabricate_a_provider_run(evaluation: EvaluationScenario) -> None:
    case = technical_case()
    case["synthetic_observation"] = None
    case["execution_job_id"] = str(uuid.uuid4())
    body = dataset_body(cases=[case])
    body["organisation_id"] = str(evaluation.organisations[0])
    version = evaluation.published(body)
    run_body = evaluation.run_body(version)
    run_body["configuration"]["execution_adapter"] = "PINNED_EXECUTION_REPLAY_V1"
    evaluation.request("POST", PREFIX + "/runs", json=run_body, expected=422)
    with evaluation.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM evaluation_runs WHERE operation_id=:operation"),
                {"operation": uuid.UUID(run_body["operation_id"])},
            ).scalar_one()
            == 0
        )


def test_case_language_must_match_declared_dataset_scope(evaluation: EvaluationScenario) -> None:
    body = dataset_body()
    body["cases"][0]["input"]["language"] = "en"
    evaluation.request("POST", PREFIX + "/datasets", json=body, expected=422)


def test_empty_selected_split_is_rejected_before_execution(evaluation: EvaluationScenario) -> None:
    body = evaluation.run_body(evaluation.published())
    body["configuration"]["dataset_split"] = "CALIBRATION"
    evaluation.request("POST", PREFIX + "/runs", json=body, expected=422)
    with evaluation.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM evaluation_runs WHERE operation_id=:operation"),
                {"operation": uuid.UUID(body["operation_id"])},
            ).scalar_one()
            == 0
        )


def test_registered_split_selects_cases_before_measuring(evaluation: EvaluationScenario) -> None:
    selected = technical_case()
    unselected = technical_case(case_id="unselected-training-negative")
    unselected["split"] = "TRAIN"
    unselected["input"]["text"] = "Different original synthetic training evidence"
    unselected["reference"]["text"] = "Andere synthetische Trainingsreferenz"
    unselected["synthetic_observation"]["text"] = "Andere synthetische Trainingsreferenz"
    for part in ("input", "reference", "synthetic_observation"):
        unselected[part]["evidence_identity"] = {"audio_sha256": "b" * 64}
        if "audio_sha256" in unselected[part]:
            unselected[part]["audio_sha256"] = "b" * 64
    unselected["synthetic_observation"]["actions"] = ["APPROVE_ASSESSMENT"]
    version = evaluation.published(dataset_body(cases=[selected, unselected]))
    detail = evaluation.execute(evaluation.run_body(version))
    assert detail["run"]["case_count"] == 1
    assert detail["run"]["hard_gate"] == "PASS"
    assert detail["failures"] == []


def test_platform_review_role_does_not_export_tenant_private_cases(
    evaluation: EvaluationScenario,
) -> None:
    body = dataset_body()
    body["organisation_id"] = str(evaluation.organisations[0])
    version = evaluation.published(body)
    evaluation.request(
        "GET",
        PREFIX + "/dataset-versions/" + version["id"] + "/cases",
        actor="reviewer",
        expected=403,
    )


@pytest.mark.parametrize("kind", ["DATASET", "RUN"])
def test_nested_json_of_published_or_terminal_history_is_immutable(
    evaluation: EvaluationScenario, kind: str
) -> None:
    version = evaluation.published()
    detail = evaluation.execute(evaluation.run_body(version))
    with evaluation.factory() as session:
        if kind == "DATASET":
            row = session.get(EvaluationDatasetVersion, uuid.UUID(version["id"]))
            row.cases[0]["input"]["text"] = "Attempted historical rewrite"
        else:
            row = session.get(EvaluationRun, uuid.UUID(detail["run"]["id"]))
            row.configuration["metric_configuration"]["retrieval_k"] = 9
        with pytest.raises(ValueError, match="(?i)immutable|history"):
            session.commit()
        session.rollback()


def test_completed_run_rejects_appended_case_results_in_postgresql(
    evaluation: EvaluationScenario,
) -> None:
    detail = evaluation.execute(evaluation.run_body(evaluation.published()))
    run_id = uuid.UUID(detail["run"]["id"])
    statement = text(
        "INSERT INTO evaluation_case_results "
        "(id,run_id,case_id,input_checksum,observation_checksum,"
        "execution_reference,result,checksum,created_at) "
        "SELECT :new_id,run_id,'late-unexecuted-case',input_checksum,observation_checksum,"
        "execution_reference,result,checksum,created_at FROM evaluation_case_results "
        "WHERE run_id=:run LIMIT 1"
    )
    with pytest.raises(DBAPIError, match="(?i)immutable|history"):
        with evaluation.engine.begin() as connection:
            connection.execute(statement, {"new_id": uuid.uuid4(), "run": run_id})
    with evaluation.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM evaluation_case_results WHERE run_id=:run"),
                {"run": run_id},
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize("scope", ["LANGUAGE", "CEFR"])
def test_wrong_pack_language_or_cefr_is_rejected_before_execution(
    evaluation: EvaluationScenario, scope: str
) -> None:
    body = dataset_body()
    if scope == "LANGUAGE":
        body["language"] = "en"
        body["cases"][0]["input"]["language"] = "en"
        body["cases"][0]["reference"]["language"] = "en"
        body["cases"][0]["synthetic_observation"]["language"] = "en"
    else:
        body["cefr_level"] = "B2"
    version = evaluation.published(body)
    evaluation.request("POST", PREFIX + "/runs", json=evaluation.run_body(version), expected=409)
