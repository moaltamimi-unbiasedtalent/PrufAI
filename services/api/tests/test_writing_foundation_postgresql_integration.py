"""Required real-JWT HUMAN Writing acceptance on an Alembic-created PostgreSQL DB.

There is deliberately no AI model, AI run, synthetic inference or worker here.
The seed's deterministic walkthrough candidate remains untouched by these tests.
"""

from __future__ import annotations

import copy
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier, Lock
from typing import Any, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.auth.api import auth_settings_dependency
from app.auth.service import AuthenticationService
from app.authorization import build_tenant_context
from app.config import Settings, get_settings
from app.db import get_db_session
from app.exams.service import ExamStudioService
from app.main import app
from app.models import (
    AIRun,
    AuditEvent,
    CandidateResponseRevision,
    HumanReviewRequest,
    MembershipRole,
    Organisation,
    OrganisationMembership,
    Permission,
    PlatformRoleAssignment,
    QuestionBankItemVersion,
    Role,
    RolePermission,
    RubricVersion,
    SubjectiveAssessmentVersion,
    SubjectiveCriterionAssessment,
    User,
    WritingGovernanceOperation,
)
from app.models.enums import (
    AssessmentClassification,
    ExamVersionStatus,
    OrganisationType,
    QuestionBankLifecycleStatus,
    RoleScope,
)
from app.question_bank.service import QuestionBankService
from app.scripts.seed_demo import PASSWORD, PLATFORM_ADMIN_EMAIL, reconcile_demo
from app.scripts.seed_writing_demo import WRITING_CRITERIA, WRITING_LEVELS, writing_id
from app.services.identity import IdentityService
from app.services.organisations import OrganisationService
from app.writing_assessment.governance import RubricGovernanceService
from app.writing_assessment.service import RubricError
from tests.alembic_helpers import repository_alembic_head
from tests.test_writing_demo_seed import assert_writing_seed_preconditions

REQUIRED = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRED,
    reason="POSTGRES_INTEGRATION_URL is required for HUMAN Writing foundation acceptance",
)

WRITING_RESPONSE = (
    "Liebe Frau Sommer, am Dienstag kann ich wegen eines Termins nicht kommen. "
    "Können Sie mir bitte die Hausaufgaben schicken? Am Donnerstag bin ich wieder "
    "im Kurs. Vielen Dank für Ihre Hilfe. Viele Grüße, Demo"
)


def _url() -> str:
    url = os.getenv("POSTGRES_INTEGRATION_URL")
    if not url:
        pytest.fail("Required HUMAN Writing foundation gate needs POSTGRES_INTEGRATION_URL")
    return url


@dataclass
class WritingJourney:
    session: Session
    client: TestClient
    identifiers: dict[str, str]
    email: str
    candidate_id: uuid.UUID
    other_email: str
    foreign_email: str
    foreign_org_id: uuid.UUID
    exam_version_id: str | None = None
    item_version_id: str | None = None

    @property
    def org(self) -> str:
        return f"/api/v1/organisations/{self.identifiers['organisation_id']}"

    def login(
        self, email: str, *, organisation_id: str | None = None, tenant: bool = True
    ) -> dict[str, str]:
        response = self.client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        if tenant:
            headers["X-PrufAI-Organisation-ID"] = (
                organisation_id or self.identifiers["organisation_id"]
            )
        return headers

    def request(self, method: str, path: str, headers: dict[str, str], *, expected=200, **kwargs):
        response = self.client.request(method, path, headers=headers, **kwargs)
        assert response.status_code == expected, (method, path, response.status_code, response.text)
        return response.json() if response.content else None

    def assert_generic_ai_cannot_decide(self, review_id: uuid.UUID) -> None:
        """Real JWT authority contains ai.review, without institution permissions."""
        reviewer = IdentityService.create_user(
            self.session, email=f"ai-review-only-{uuid.uuid4()}@example.test"
        )
        AuthenticationService(Settings(_env_file=None, environment="test")).set_password(
            self.session, reviewer, PASSWORD
        )
        role = Role(key="AI_REVIEW_ONLY_" + uuid.uuid4().hex, scope=RoleScope.PLATFORM)
        self.session.add(role)
        self.session.flush()
        permission = self.session.scalar(
            select(Permission).where(
                Permission.key == "ai.review",
                Permission.scope == RoleScope.PLATFORM,
            )
        )
        assert permission is not None
        self.session.add_all(
            [
                RolePermission(role_id=role.id, permission_id=permission.id),
                PlatformRoleAssignment(user_id=reviewer.id, role_id=role.id),
            ]
        )
        self.session.commit()
        context = build_tenant_context(self.session, reviewer.id)
        assert context.platform_permissions == frozenset({"ai.review"})
        assert not context.permissions and context.organisation_id is None
        headers = self.login(reviewer.email, tenant=False)
        for action in ("approve", "reject", "request-changes", "decision"):
            kwargs = {"json": {"decision": "APPROVED"}} if action == "decision" else {}
            response = self.request(
                "POST",
                f"/api/v1/platform/ai/reviews/{review_id}/{action}",
                headers,
                expected=409,
                **kwargs,
            )
            assert response["detail"] == "review requires a domain governed decision"
            self.session.expire_all()
            pending = self.session.get(HumanReviewRequest, review_id)
            assert pending.status.value == "PENDING"
            assert pending.decided_by is pending.decided_at is pending.decision is None

    def malicious_newer_ai_review(
        self, domain_review_id: uuid.UUID, requested_by_email: str
    ) -> uuid.UUID:
        """Adversarial review-envelope injection, never an AI execution fixture."""
        domain = self.session.get(HumanReviewRequest, domain_review_id)
        other_actor = self.session.scalar(select(User).where(User.email == requested_by_email))
        injected = HumanReviewRequest(
            review_type="AI_PROPOSAL",
            entity_type=domain.entity_type,
            entity_id=domain.entity_id,
            status=domain.status,
            requested_by=other_actor.id,
            requested_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            reviewed_checksum=domain.reviewed_checksum,
        )
        self.session.add(injected)
        self.session.commit()
        return injected.id

    def rubric(self) -> dict[str, Any]:
        admin = self.login("admin@demo.prufai.local")
        rubric = self.request(
            "POST",
            self.org + "/rubrics",
            admin,
            json={
                "code": "HUMAN_PG_" + uuid.uuid4().hex,
                "name": "Original HUMAN Writing PG acceptance — SYNTHETIC TEST",
            },
        )
        version = self.request(
            "POST",
            self.org + f"/rubrics/{rubric['id']}/versions",
            admin,
            json={
                "standard_pack_version_id": self.identifiers["writing_pack_version_id"],
                "task_spec_id": self.identifiers["writing_task_spec_id"],
                "version_label": "pg-human-v1",
                "criteria": WRITING_CRITERIA,
                "performance_levels": WRITING_LEVELS,
                "aggregation_policy": {"method": "SUM"},
                "evidence_policy": {"required": True, "minimum_references": 1},
                "evidence_bindings": [
                    {
                        "knowledge_document_version_id": str(writing_id("policy-document")),
                        "purpose": "ORIGINAL_PRACTICE_POLICY",
                    }
                ],
                "ai_assessment_policy": {"enabled": False, "mode": "HUMAN_ONLY"},
                "human_review_policy": {"required": True, "separation_of_duties": True},
            },
        )
        version["rubric_id"] = rubric["id"]
        self.deliverable(version)
        return version

    def deliverable(self, rubric: dict[str, Any]) -> None:
        """Create genuine immutable item/exam versions for this exact rubric.

        The seeded item remains pinned to its original DRAFT rubric for the
        browser. A per-case fixture never rewrites that published item linkage.
        """
        self.session.expire_all()
        admin = self.session.scalar(select(User).where(User.email == "admin@demo.prufai.local"))
        operator = self.session.scalar(select(User).where(User.email == PLATFORM_ADMIN_EMAIL))
        org_id = uuid.UUID(self.identifiers["organisation_id"])
        tenant = build_tenant_context(self.session, admin.id, org_id)
        publisher = build_tenant_context(self.session, operator.id, org_id)
        template = self.session.get(
            QuestionBankItemVersion, uuid.UUID(self.identifiers["writing_item_version_id"])
        )
        item = QuestionBankService.create_item(
            self.session,
            tenant,
            code="HUMAN_PG_" + uuid.uuid4().hex,
            scope_type="ORGANISATION_PRIVATE",
            organisation_id=org_id,
        )
        version = QuestionBankService.create_version(
            self.session,
            tenant,
            item,
            version_label="pg-human-v1",
            assessment_family=template.assessment_family,
            task_type=template.task_type,
            response_mode=template.response_mode,
            content=copy.deepcopy(template.content),
            language=template.language,
            cefr_levels=["B1"],
            skill=template.skill,
            scoring_policy=template.scoring_policy,
            max_points=template.max_points,
            scoring_metadata=copy.deepcopy(template.scoring_metadata),
            rights_status=template.rights_status,
            rights_holder="PrüfAI original synthetic test",
            rubric_reference=rubric["id"],
        )
        pack_id = uuid.UUID(self.identifiers["writing_pack_version_id"])
        task_id = uuid.UUID(self.identifiers["writing_task_spec_id"])
        QuestionBankService.bind_pack(self.session, tenant, version, pack_id, task_id)
        QuestionBankService.request_review(self.session, publisher, version)
        validation = QuestionBankService.validate_pack_compatibility(
            self.session, version, pack_id, task_id
        )
        for target in (
            QuestionBankLifecycleStatus.APPROVED,
            QuestionBankLifecycleStatus.PUBLISHED,
            QuestionBankLifecycleStatus.ACTIVE,
        ):
            QuestionBankService.transition(
                self.session,
                publisher,
                version,
                target,
                validation=validation,
                checksum=version.checksum,
            )
        exam = ExamStudioService.create_exam(
            self.session,
            tenant,
            code="HUMAN_PG_" + uuid.uuid4().hex,
            name="Original HUMAN PG Writing — SYNTHETIC TEST",
            scope_type="ORGANISATION_PRIVATE",
            organisation_id=org_id,
        )
        exam_version = ExamStudioService.create_version(
            self.session,
            tenant,
            exam,
            version_label="pg-human-v1",
            standard_pack_version_id=pack_id,
            classification=AssessmentClassification.PRACTICE,
        )
        ExamStudioService.materialize_blueprint(self.session, tenant, exam_version)
        self.session.expire(exam_version, ["modules"])
        concrete_task = exam_version.modules[0].sections[0].tasks[0]
        ExamStudioService.assign_item(
            self.session,
            tenant,
            exam_version,
            task_id=concrete_task.id,
            item_version_id=version.id,
            slot_index=0,
        )
        self.session.expire_all()
        ExamStudioService.request_review(self.session, tenant, exam_version)
        for target in (
            ExamVersionStatus.APPROVED,
            ExamVersionStatus.PUBLISHED,
            ExamVersionStatus.ACTIVE,
        ):
            ExamStudioService.transition(self.session, tenant, exam_version, target)
        self.session.commit()
        self.exam_version_id = str(exam_version.id)
        self.item_version_id = str(version.id)

    def rubric_path(self, version: dict[str, Any]) -> str:
        return self.org + f"/rubrics/{version['rubric_id']}/versions/{version['id']}"

    def publish(self, version: dict[str, Any]) -> dict[str, Any]:
        path = self.rubric_path(version)
        admin = self.login("admin@demo.prufai.local")
        reviewer = self.login("reviewer@demo.prufai.local")
        for action, actor in (
            ("request-review", admin),
            ("approve", reviewer),
            ("publish", reviewer),
        ):
            version = self.request(
                "POST",
                path + "/" + action,
                actor,
                json={
                    "operation_id": str(uuid.uuid4()),
                    "expected_checksum": version["checksum"],
                },
            )
        return version

    def submit(self) -> dict[str, Any]:
        admin = self.login("admin@demo.prufai.local")
        assignment = self.request(
            "POST",
            self.org + "/exam-assignments",
            admin,
            expected=201,
            json={
                "candidate_user_id": str(self.candidate_id),
                "exam_version_id": self.exam_version_id,
            },
        )
        candidate = self.login(self.email)
        attempt = self.request(
            "POST",
            f"/api/v1/candidate/exam-assignments/{assignment['id']}/attempts",
            candidate,
            expected=201,
        )
        path = f"/api/v1/candidate/exam-attempts/{attempt['id']}"
        runtime = self.request("POST", path + "/start", candidate)
        item = runtime["runtime_plan"]["modules"][0]["sections"][0]["tasks"][0]["items"][0]
        assert item["response_mode"] == "LONG_TEXT"
        assert item["item_version_id"] == self.item_version_id
        operation = str(uuid.uuid4())
        first_body = {
            "client_operation_id": operation,
            "expected_revision": 0,
            "payload": {"text": "ORIGINAL SYNTHETIC DRAFT: Hallo Frau Sommer."},
        }
        response_path = path + f"/responses/{item['binding_id']}"
        first = self.request("PUT", response_path, candidate, json=first_body)
        assert self.request("PUT", response_path, candidate, json=first_body) == first
        text = WRITING_RESPONSE
        final = self.request(
            "PUT",
            response_path,
            candidate,
            json={
                "client_operation_id": str(uuid.uuid4()),
                "expected_revision": 1,
                "payload": {"text": text},
            },
        )
        assert final["revision"] == 2
        reloaded = self.request("GET", path + "/runtime", candidate)
        assert reloaded["responses"][0]["payload"] == {"text": text}
        self.request("POST", path + "/submit", candidate)
        self.request(
            "PUT",
            response_path,
            candidate,
            expected=409,
            json={
                "client_operation_id": str(uuid.uuid4()),
                "expected_revision": 2,
                "payload": {"text": "Late edit must be denied"},
            },
        )
        self.session.expire_all()
        revision = self.session.scalar(
            select(CandidateResponseRevision).where(
                CandidateResponseRevision.attempt_id == uuid.UUID(attempt["id"]),
                CandidateResponseRevision.revision_number == 2,
            )
        )
        assert revision is not None and revision.payload["text"] == text
        return {
            "attempt": attempt,
            "item": item,
            "revision": revision,
            "text": text,
            "candidate": candidate,
        }

    def initialize(self, state: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "POST",
            self.org + "/subjective-assessments/human",
            self.login("reviewer@demo.prufai.local"),
            json={
                "operation_id": str(uuid.uuid4()),
                "attempt_id": state["attempt"]["id"],
                "exam_version_item_binding_id": state["item"]["binding_id"],
                "rubric_version_id": rubric["id"],
                "rubric_checksum": rubric["checksum"],
                "response_checksum": state["revision"].payload_checksum,
            },
        )


@pytest.fixture
def writing() -> Iterator[WritingJourney]:
    engine = create_engine(_url())
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_rate_limit_backend="memory",
        auth_rate_limit_login_attempts=200,
        auth_jwt_secret="writing-foundation-real-jwt-" + "x" * 40,
    )
    previous = dict(app.dependency_overrides)

    def database():
        with factory() as session:
            yield session

    with factory() as session:
        identifiers = reconcile_demo(
            session, Settings(_env_file=None, environment="development"), enabled=True
        )
        admin = session.scalar(select(User).where(User.email == "admin@demo.prufai.local"))
        assert admin is not None
        context = build_tenant_context(session, admin.id, uuid.UUID(identifiers["organisation_id"]))
        users = []
        for label in ("writer", "other", "foreign"):
            user = IdentityService.create_user(
                session, email=f"human-{label}-{uuid.uuid4()}@example.test"
            )
            AuthenticationService(settings).set_password(session, user, PASSWORD)
            users.append(user)
        for user in users[:2]:
            membership = OrganisationService.create_membership(
                session, context, organisation_id=context.organisation_id, user_id=user.id
            )
            OrganisationService.assign_membership_role(
                session, context, membership_id=membership.id, role_key="EXAMINER"
            )
        foreign = Organisation(
            slug="human-foreign-" + uuid.uuid4().hex,
            name="Other HUMAN PG tenant",
            organisation_type=OrganisationType.LANGUAGE_SCHOOL,
        )
        session.add(foreign)
        session.flush()
        membership = OrganisationMembership(organisation_id=foreign.id, user_id=users[2].id)
        session.add(membership)
        session.flush()
        role = session.scalar(
            select(Role).where(Role.key == "INSTITUTION_ADMIN", Role.scope == RoleScope.TENANT)
        )
        assert role is not None
        session.add(MembershipRole(membership_id=membership.id, role_id=role.id))
        session.commit()
        app.dependency_overrides[get_db_session] = database
        app.dependency_overrides[auth_settings_dependency] = lambda: settings
        app.dependency_overrides[get_settings] = lambda: settings
        try:
            with TestClient(app) as client:
                yield WritingJourney(
                    session,
                    client,
                    identifiers,
                    users[0].email,
                    users[0].id,
                    users[1].email,
                    users[2].email,
                    foreign.id,
                )
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
    engine.dispose()


def criteria() -> list[dict[str, Any]]:
    return [
        {
            "criterion_code": code,
            "level": "CLEAR",
            "points": "2",
            "rationale": "Authorized HUMAN review of the original synthetic response.",
            "evidence": [{"quote": WRITING_RESPONSE[:17], "start": 0, "end": 17}],
        }
        for code in ("TASK", "LANGUAGE")
    ]


def test_writing_foundation_uses_current_alembic_schema() -> None:
    with psycopg.connect(_url().replace("postgresql+psycopg://", "postgresql://")) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [
            (repository_alembic_head(),)
        ]
        assert connection.execute("SELECT to_regclass('writing_governance_operations')").fetchone()[
            0
        ]


def test_writing_seed_is_unassigned_draft_and_idempotent_on_real_postgresql(writing):
    identifiers = assert_writing_seed_preconditions(writing.session)
    assert identifiers == writing.identifiers
    assert assert_writing_seed_preconditions(writing.session) == identifiers


def test_rubric_human_lifecycle_is_exact_idempotent_and_separates_duties(writing):
    draft = writing.rubric()
    path = writing.rubric_path(draft)
    admin = writing.login("admin@demo.prufai.local")
    reviewer = writing.login("reviewer@demo.prufai.local")
    wrong = {"operation_id": str(uuid.uuid4()), "expected_checksum": "0" * 64}
    writing.request("POST", path + "/request-review", admin, expected=409, json=wrong)
    body = {"operation_id": str(uuid.uuid4()), "expected_checksum": draft["checksum"]}
    review = writing.request("POST", path + "/request-review", admin, json=body)
    assert review["status"] == "HUMAN_REVIEW"
    assert writing.request("POST", path + "/request-review", admin, json=body) == review
    writing.assert_generic_ai_cannot_decide(uuid.UUID(review["review"]["id"]))
    injected_id = writing.malicious_newer_ai_review(
        uuid.UUID(review["review"]["id"]), "reviewer@demo.prufai.local"
    )
    assert writing.request("GET", path, reviewer)["review"]["id"] == review["review"]["id"]
    writing.request(
        "POST",
        path + "/approve",
        admin,
        expected=403,
        json={**body, "operation_id": str(uuid.uuid4())},
    )
    approved = writing.request(
        "POST", path + "/approve", reviewer, json={**body, "operation_id": str(uuid.uuid4())}
    )
    assert approved["status"] == "APPROVED"
    writing.session.expire_all()
    assert writing.session.get(HumanReviewRequest, injected_id).status.value == "PENDING"
    published = writing.request(
        "POST", path + "/publish", reviewer, json={**body, "operation_id": str(uuid.uuid4())}
    )
    assert published["status"] == "PUBLISHED" and published["checksum"] == draft["checksum"]
    assert published["review"]["requested_by"] != published["review"]["decided_by"]
    writing.session.expire_all()
    persisted = writing.session.get(RubricVersion, uuid.UUID(draft["id"]))
    assert persisted.approved_at is not None and persisted.published_at is not None
    expected_metadata = {
        "language": persisted.language,
        "assessment_family": persisted.assessment_family.value,
        "cefr_levels": persisted.cefr_levels,
    }
    assert expected_metadata == {
        "language": "de",
        "assessment_family": "LANGUAGE",
        "cefr_levels": ["B1"],
    }
    detail = writing.request("GET", path, reviewer)
    listed = writing.request("GET", writing.org + "/rubrics", reviewer)
    listed_version = next(
        version
        for rubric in listed
        for version in rubric["versions"]
        if version["id"] == str(persisted.id)
    )
    for projection in (published, detail, listed_version):
        assert {key: projection[key] for key in expected_metadata} == expected_metadata
    with pytest.raises(ValueError, match="immutable"):
        persisted.criteria = [{**persisted.criteria[0], "max_points": "100"}]
        writing.session.flush()
    writing.session.rollback()


def test_submitted_writing_human_proposal_and_distinct_approval_have_no_ai_evidence(writing):
    before_ai = writing.session.scalar(select(func.count()).select_from(AIRun))
    rubric = writing.publish(writing.rubric())
    state = writing.submit()
    created = writing.initialize(state, rubric)
    assert created["status"] == "REVIEW_REQUIRED" and created["assessment_origin"] == "HUMAN"
    reviewer = writing.login("reviewer@demo.prufai.local")
    path = writing.org + f"/subjective-assessments/{created['version_id']}"
    proposal_body = {
        "operation_id": str(uuid.uuid4()),
        "expected_checksum": created["checksum"],
        "criteria": criteria(),
    }
    proposed = writing.request("POST", path + "/human-proposal", reviewer, json=proposal_body)
    assert proposed["status"] == "IN_REVIEW"
    legacy_body = {
        "attempt_id": state["attempt"]["id"],
        "exam_version_item_binding_id": state["item"]["binding_id"],
        "rubric_version_id": rubric["id"],
    }
    writing.request(
        "POST", writing.org + "/subjective-assessments", reviewer, expected=409, json=legacy_body
    )
    writing.session.expire_all()
    pending = writing.session.scalar(
        select(HumanReviewRequest).where(
            HumanReviewRequest.entity_id == uuid.UUID(created["version_id"]),
            HumanReviewRequest.entity_type == "SUBJECTIVE_ASSESSMENT_VERSION",
        )
    )
    assert pending is not None and pending.status.value == "PENDING"
    assert pending.reviewed_checksum == proposed["checksum"]
    writing.assert_generic_ai_cannot_decide(pending.id)
    injected_id = writing.malicious_newer_ai_review(pending.id, "admin@demo.prufai.local")
    workspace = writing.request(
        "GET", writing.org + f"/subjective-assessments/{created['id']}", reviewer
    )
    assert workspace["review"]["id"] == str(pending.id)
    assert (
        writing.request("POST", path + "/human-proposal", reviewer, json=proposal_body) == proposed
    )
    writing.request(
        "POST",
        path + "/human-approve",
        reviewer,
        expected=403,
        json={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": proposed["checksum"],
        },
    )
    approved = writing.request(
        "POST",
        path + "/human-approve",
        writing.login("admin@demo.prufai.local"),
        json={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": proposed["checksum"],
        },
    )
    assert approved["status"] == "APPROVED" and Decimal(approved["approved_score"]) == Decimal("4")
    writing.session.expire_all()
    assert writing.session.get(HumanReviewRequest, injected_id).status.value == "PENDING"
    writing.request(
        "POST", writing.org + "/subjective-assessments", reviewer, expected=409, json=legacy_body
    )
    writing.session.expire_all()
    version = writing.session.get(SubjectiveAssessmentVersion, uuid.UUID(created["version_id"]))
    assert version.candidate_response_revision_id == state["revision"].id
    assert version.response_checksum == state["revision"].payload_checksum
    assert version.rubric_version_id == uuid.UUID(rubric["id"])
    assert version.rubric_checksum == rubric["checksum"]
    assert all(
        getattr(version, key) is None
        for key in (
            "ai_run_id",
            "model_profile_id",
            "agent_version_id",
            "prompt_version_id",
            "workflow_version_id",
        )
    )
    assert version.ai_proposal == {} and version.proposed_score == Decimal("4")
    assert all(row.confidence is None for row in version.criterion_assessments)
    assert writing.session.scalar(select(func.count()).select_from(AIRun)) == before_ai
    assert (
        writing.session.scalar(
            select(func.count())
            .select_from(SubjectiveAssessmentVersion)
            .where(SubjectiveAssessmentVersion.attempt_id == uuid.UUID(state["attempt"]["id"]))
        )
        == 1
    )
    before_checksum = version.checksum
    for field, replacement in (("approved_score", Decimal("99")), ("assessment_origin", "AI")):
        with pytest.raises(ValueError, match="immutable"):
            setattr(version, field, replacement)
            writing.session.flush()
        writing.session.rollback()
    with pytest.raises(ValueError, match="immutable"):
        version.criterion_assessments[0].approved_points = Decimal("99")
        writing.session.flush()
    writing.session.rollback()
    assert version.checksum == before_checksum and version.approved_score == Decimal("4")


@pytest.mark.parametrize(
    "change",
    ["unknown", "duplicate", "missing", "negative", "too_high", "nan", "level", "evidence"],
)
def test_human_proposal_rejects_invalid_complete_criterion_contract(writing, change):
    rubric = writing.publish(writing.rubric())
    created = writing.initialize(writing.submit(), rubric)
    values = criteria()
    if change == "unknown":
        values[0]["criterion_code"] = "UNKNOWN"
    elif change == "duplicate":
        values.append(copy.deepcopy(values[0]))
    elif change == "missing":
        values.pop()
    elif change == "negative":
        values[0]["points"] = "-1"
    elif change == "too_high":
        values[0]["points"] = "4"
    elif change == "nan":
        values[0]["points"] = "NaN"
    elif change == "level":
        values[0]["level"] = "OFFICIAL_PASS"
    elif change == "evidence":
        values[0]["evidence"][0]["quote"] = "not in the pinned response"
    writing.request(
        "POST",
        writing.org + f"/subjective-assessments/{created['version_id']}/human-proposal",
        writing.login("reviewer@demo.prufai.local"),
        expected=409,
        json={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": created["checksum"],
            "criteria": values,
        },
    )
    writing.session.expire_all()
    version = writing.session.get(SubjectiveAssessmentVersion, uuid.UUID(created["version_id"]))
    assert version.status.value == "REVIEW_REQUIRED" and version.approved_score is None
    assert (
        writing.session.scalar(
            select(func.count())
            .select_from(SubjectiveCriterionAssessment)
            .where(SubjectiveCriterionAssessment.subjective_assessment_version_id == version.id)
        )
        == 0
    )


def test_human_initializer_rejects_stale_response_rubric_and_unpublished_version(writing):
    rubric = writing.rubric()
    state = writing.submit()
    reviewer = writing.login("reviewer@demo.prufai.local")
    body = {
        "operation_id": str(uuid.uuid4()),
        "attempt_id": state["attempt"]["id"],
        "exam_version_item_binding_id": state["item"]["binding_id"],
        "rubric_version_id": rubric["id"],
        "rubric_checksum": rubric["checksum"],
        "response_checksum": state["revision"].payload_checksum,
    }
    writing.request(
        "POST", writing.org + "/subjective-assessments/human", reviewer, expected=409, json=body
    )
    rubric = writing.publish(rubric)
    for field in ("response_checksum", "rubric_checksum"):
        writing.request(
            "POST",
            writing.org + "/subjective-assessments/human",
            reviewer,
            expected=409,
            json={**body, "operation_id": str(uuid.uuid4()), field: "0" * 64},
        )


def test_pending_criterion_mutation_cannot_receive_stale_human_approval(writing):
    rubric = writing.publish(writing.rubric())
    created = writing.initialize(writing.submit(), rubric)
    path = writing.org + f"/subjective-assessments/{created['version_id']}"
    proposed = writing.request(
        "POST",
        path + "/human-proposal",
        writing.login("reviewer@demo.prufai.local"),
        json={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": created["checksum"],
            "criteria": criteria(),
        },
    )
    writing.session.expire_all()
    version = writing.session.get(SubjectiveAssessmentVersion, uuid.UUID(created["version_id"]))
    version.criterion_assessments[0].proposed_points = Decimal("1")
    writing.session.commit()
    writing.request(
        "POST",
        path + "/human-approve",
        writing.login("admin@demo.prufai.local"),
        expected=409,
        json={"operation_id": str(uuid.uuid4()), "expected_checksum": proposed["checksum"]},
    )
    writing.session.expire_all()
    assert version.status.value == "IN_REVIEW" and version.approved_score is None


def test_changes_requested_history_is_frozen_and_only_human_path_creates_successor(writing):
    rubric = writing.publish(writing.rubric())
    state = writing.submit()
    created = writing.initialize(state, rubric)
    reviewer = writing.login("reviewer@demo.prufai.local")
    path = writing.org + f"/subjective-assessments/{created['version_id']}"
    proposed = writing.request(
        "POST",
        path + "/human-proposal",
        reviewer,
        json={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": created["checksum"],
            "criteria": criteria(),
        },
    )
    changed = writing.request(
        "POST",
        path + "/human-decision",
        writing.login("admin@demo.prufai.local"),
        json={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": proposed["checksum"],
            "decision": "CHANGES_REQUESTED",
            "reason": "Original synthetic human review needs revision.",
        },
    )
    assert changed["status"] == "CHANGES_REQUESTED"
    writing.session.expire_all()
    prior = writing.session.get(SubjectiveAssessmentVersion, uuid.UUID(created["version_id"]))
    prior_checksum = prior.checksum
    for mutate in (
        lambda: setattr(prior, "assessment_origin", "AI"),
        lambda: setattr(prior.criterion_assessments[0], "proposed_points", Decimal("99")),
    ):
        with pytest.raises(ValueError, match="immutable"):
            mutate()
            writing.session.flush()
        writing.session.rollback()
    writing.request(
        "POST",
        writing.org + "/subjective-assessments",
        reviewer,
        expected=409,
        json={
            "attempt_id": state["attempt"]["id"],
            "exam_version_item_binding_id": state["item"]["binding_id"],
            "rubric_version_id": rubric["id"],
        },
    )
    successor = writing.initialize(state, rubric)
    assert successor["id"] == created["id"]
    assert successor["version_id"] != created["version_id"]
    assert successor["assessment_origin"] == "HUMAN" and successor["status"] == "REVIEW_REQUIRED"
    writing.session.expire_all()
    latest = writing.session.get(SubjectiveAssessmentVersion, uuid.UUID(successor["version_id"]))
    assert latest.supersedes_version_id == prior.id
    assert latest.candidate_response_revision_id == prior.candidate_response_revision_id
    assert latest.rubric_version_id == prior.rubric_version_id
    assert prior.checksum == prior_checksum and prior.status.value == "CHANGES_REQUESTED"
    assert latest.approved_score is None and latest.ai_run_id is None


def test_human_writing_authority_rejects_candidate_foreign_tenant_and_forged_headers(writing):
    rubric = writing.publish(writing.rubric())
    state = writing.submit()
    created = writing.initialize(state, rubric)
    path = writing.org + f"/subjective-assessments/{created['version_id']}/human-proposal"
    body = {
        "operation_id": str(uuid.uuid4()),
        "expected_checksum": created["checksum"],
        "criteria": criteria(),
    }
    candidate = state["candidate"]
    for headers in (
        candidate,
        {**candidate, "X-PrufAI-Role": "INSTITUTION_ADMIN"},
        writing.login(writing.other_email),
        writing.login(PLATFORM_ADMIN_EMAIL, tenant=False),
        writing.login(writing.foreign_email, organisation_id=str(writing.foreign_org_id)),
    ):
        writing.request("POST", path, headers, expected=403, json=body)
        writing.request(
            "GET", writing.org + f"/subjective-assessments/{created['id']}", headers, expected=403
        )
    writing.request("POST", path, writing.login(writing.foreign_email), expected=401, json=body)


def test_human_operation_replay_cannot_change_payload_or_bypass_current_authority(writing):
    rubric = writing.publish(writing.rubric())
    created = writing.initialize(writing.submit(), rubric)
    path = writing.org + f"/subjective-assessments/{created['version_id']}/human-proposal"
    body = {
        "operation_id": str(uuid.uuid4()),
        "expected_checksum": created["checksum"],
        "criteria": criteria(),
    }
    reviewer = writing.login("reviewer@demo.prufai.local")
    first = writing.request("POST", path, reviewer, json=body)
    assert writing.request("POST", path, reviewer, json=body) == first
    changed = copy.deepcopy(body)
    changed["criteria"][0]["points"] = "1"
    writing.request("POST", path, reviewer, expected=409, json=changed)
    writing.request("POST", path, writing.login("admin@demo.prufai.local"), expected=409, json=body)
    writing.request("POST", path, writing.login(writing.email), expected=403, json=body)


def test_two_postgresql_connections_serialize_real_human_initialization(writing):
    rubric = writing.publish(writing.rubric())
    state = writing.submit()
    # Distinct real login sessions prevent the authentication session's own
    # last-used update from serializing the requests before the domain lock.
    headers = [writing.login("reviewer@demo.prufai.local") for _ in range(2)]
    operation_id = uuid.uuid4()
    body = {
        "operation_id": str(operation_id),
        "attempt_id": state["attempt"]["id"],
        "exam_version_item_binding_id": state["item"]["binding_id"],
        "rubric_version_id": rubric["id"],
        "rubric_checksum": rubric["checksum"],
        "response_checksum": state["revision"].payload_checksum,
    }
    barrier = Barrier(2, timeout=8)
    guard = Lock()
    backend_pids: set[int] = set()
    engine = writing.session.get_bind()

    def overlap(connection, _cursor, statement, _parameters, _context, _executemany):
        if "candidate_exam_attempts" not in statement or "FOR UPDATE" not in statement:
            return
        pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
        with guard:
            if pid in backend_pids:
                return
            backend_pids.add(pid)
        # Both real service requests have acquired independent DB connections
        # and reached the actual domain row lock before either may proceed.
        barrier.wait()

    def initialize(request_headers):
        return writing.client.post(
            writing.org + "/subjective-assessments/human", headers=request_headers, json=body
        )

    event.listen(engine, "before_cursor_execute", overlap)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(initialize, request_headers) for request_headers in headers]
            responses = [future.result(timeout=20) for future in futures]
    finally:
        event.remove(engine, "before_cursor_execute", overlap)
    assert len(backend_pids) == 2
    assert [response.status_code for response in responses] == [200, 200], [
        response.text for response in responses
    ]
    assert responses[0].json() == responses[1].json()
    writing.session.expire_all()
    assert (
        writing.session.scalar(
            select(func.count())
            .select_from(SubjectiveAssessmentVersion)
            .where(SubjectiveAssessmentVersion.attempt_id == uuid.UUID(state["attempt"]["id"]))
        )
        == 1
    )
    assert (
        writing.session.scalar(
            select(func.count())
            .select_from(WritingGovernanceOperation)
            .where(WritingGovernanceOperation.operation_id == operation_id)
        )
        == 1
    )


def test_preloaded_draft_rubric_is_reloaded_under_contended_postgresql_lock(writing):
    draft = writing.rubric()
    engine = writing.session.get_bind()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    sessions = [factory(), factory()]
    contexts = []
    for session in sessions:
        preload = session.get(RubricVersion, uuid.UUID(draft["id"]))
        assert preload is not None and preload.status.value == "DRAFT"
        # Keep a strong reference: SQLAlchemy identity maps otherwise release
        # an unmodified object before the service's locking read.
        session.info["preloaded_rubric"] = preload
        administrator = session.scalar(select(User).where(User.email == "admin@demo.prufai.local"))
        contexts.append(
            build_tenant_context(
                session, administrator.id, uuid.UUID(writing.identifiers["organisation_id"])
            )
        )
    barrier = Barrier(2, timeout=8)
    guard = Lock()
    backend_pids: set[int] = set()

    def overlap(connection, _cursor, statement, _parameters, _context, _executemany):
        if "rubric_versions" not in statement or "FOR UPDATE" not in statement:
            return
        pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
        with guard:
            if pid in backend_pids:
                return
            backend_pids.add(pid)
        barrier.wait()

    def request_review(session, context):
        try:
            RubricGovernanceService.request_review(
                session,
                context,
                uuid.UUID(draft["id"]),
                operation_id=uuid.uuid4(),
                expected_checksum=draft["checksum"],
            )
            session.commit()
            return "REVIEW_CREATED"
        except RubricError as exc:
            session.rollback()
            assert "draft" in str(exc).lower()
            return "CONFLICT"
        finally:
            session.close()

    event.listen(engine, "before_cursor_execute", overlap)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(request_review, session, context)
                for session, context in zip(sessions, contexts)
            ]
            outcomes = [future.result(timeout=20) for future in futures]
    finally:
        event.remove(engine, "before_cursor_execute", overlap)
        for session in sessions:
            session.close()
    assert len(backend_pids) == 2
    assert sorted(outcomes) == ["CONFLICT", "REVIEW_CREATED"]
    writing.session.expire_all()
    reviews = writing.session.scalars(
        select(HumanReviewRequest).where(
            HumanReviewRequest.entity_id == uuid.UUID(draft["id"]),
            HumanReviewRequest.entity_type == "RUBRIC_VERSION",
        )
    ).all()
    assert len(reviews) == 1 and reviews[0].status.value == "PENDING"


def test_human_audit_excludes_candidate_text_and_model_or_private_payloads(writing):
    rubric = writing.publish(writing.rubric())
    state = writing.submit()
    created = writing.initialize(state, rubric)
    writing.request(
        "POST",
        writing.org + f"/subjective-assessments/{created['version_id']}/human-proposal",
        writing.login("reviewer@demo.prufai.local"),
        json={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": created["checksum"],
            "criteria": criteria(),
        },
    )
    writing.session.expire_all()
    events = writing.session.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_id.in_([created["id"], created["version_id"], rubric["id"]])
        )
    ).all()
    assert events
    for recorded_event in events:
        payload = repr(recorded_event.metadata_json).lower()
        assert state["text"].lower() not in payload
        assert not any(
            marker in payload
            for marker in ("password", "secret", "candidate_text", "raw_input", "private_url")
        )
