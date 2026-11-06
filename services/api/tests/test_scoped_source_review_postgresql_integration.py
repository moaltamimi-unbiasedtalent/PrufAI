"""Required Alembic/PostgreSQL proof for resource-scoped source review.

Every identity and document in this module is synthetic and confined to a fresh
disposable acceptance database.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Event

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.api import auth_settings_dependency
from app.auth.service import AuthenticationService
from app.authorization import AuthorizationError, TenantContext
from app.authorization.seed import seed_defaults
from app.config import Settings, get_settings
from app.db import get_db_session
from app.evaluation.disposable import assert_disposable_database
from app.main import app
from app.models import (
    AuditEvent,
    KnowledgeLegalMetadata,
    KnowledgeSource,
    Organisation,
    PlatformRoleAssignment,
    Role,
    User,
)
from app.models.enums import (
    LegalReviewStatus,
    OrganisationType,
    PlatformAssignmentStatus,
    RoleScope,
    UserStatus,
)
from app.scoped_source_review import service as scoped_service
from app.scoped_source_review.schemas import CreateGrant, grant_scope_checksum
from app.services.organisations import OrganisationService
from tests.alembic_helpers import repository_alembic_head

_REQUIRED = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not _REQUIRED,
    reason="Scoped source review requires real Alembic PostgreSQL",
)
API_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "/api/v1/platform/standards"
CONTROL = "/api/v1/platform/control-center"
SCOPED = "/api/v1/scoped-source-review"
PASSWORD = "Synthetic-scoped-source-review-test-only-2026!"
PRIVATE_REFERENCE = "synthetic:confidential-review-dossier-do-not-audit"
PRIVATE_SOURCE_NOTE = "synthetic:unprojected-source-notes"


@dataclass(repr=False)
class Scenario:
    factory: sessionmaker[Session]
    headers: dict[str, dict[str, str]]
    user_ids: dict[str, uuid.UUID]
    organisation_id: str
    other_organisation_id: str
    authority_id: str

    def request(self, method, path, *, actor="reviewer", status=200, payload=None, extra=None):
        with TestClient(app) as client:
            response = client.request(
                method,
                path,
                headers={**self.headers[actor], **(extra or {})},
                json=payload,
            )
        statuses = (status,) if isinstance(status, int) else status
        assert response.status_code in statuses, (method, path, response.status_code, response.text)
        return response.json()

    def new_organisation(self):
        with self.factory() as session:
            org = Organisation(
                name="Synthetic scoped source organisation",
                slug="synthetic-scoped-" + uuid.uuid4().hex,
                organisation_type=OrganisationType.LANGUAGE_SCHOOL,
            )
            session.add(org)
            session.commit()
            return str(org.id)

    def grant(self, versions, *, actor="issuer", org=None, status=(200, 201), **overrides):
        org = org or self.new_organisation()
        payload = _grant_payload(
            self.user_ids["reviewer"], versions, org, self.user_ids[actor], **overrides
        )
        response = self.request(
            "POST", _org_grants(org), actor=actor, status=status, payload=payload
        )
        return response, payload

    def version(self, *, actor="author", source_id=None, supersedes=None, **changes):
        suffix = uuid.uuid4().hex
        if source_id is None:
            source_id = self.request(
                "POST",
                REGISTRY + "/sources",
                actor=actor,
                status=201,
                payload={
                    "authority_id": self.authority_id,
                    "source_code": "SYNTHETIC_SCOPED_" + suffix,
                    "title": "Synthetic bibliographic source " + suffix,
                    "document_type": "STANDARD",
                    "assessment_family": "INTEGRATION_KNOWLEDGE",
                    "notes": PRIVATE_SOURCE_NOTE,
                },
            )["id"]
        version = self.request(
            "POST",
            REGISTRY + f"/sources/{source_id}/versions",
            actor=actor,
            status=201,
            payload={
                "version_label": "Synthetic edition " + suffix,
                "content_mode": "METADATA_ONLY",
                "supersedes_document_version_id": supersedes,
                "rights_holder": "Synthetic test authors",
                "licence_type": "SYNTHETIC TEST",
                "notes": PRIVATE_SOURCE_NOTE,
                **changes,
            },
        )
        return {**version, "source_id": source_id}


@pytest.fixture(scope="module")
def scenario():
    configured = os.environ.get("POSTGRES_INTEGRATION_URL", "")
    control_name = assert_disposable_database(
        configured, os.environ.get("PRUFAI_DISPOSABLE_DATABASE")
    )
    control_url = make_url(configured)
    child_name = "prufai_eval2_scopedreview_" + uuid.uuid4().hex
    database_url = control_url.set(database=child_name)
    connection_url = database_url.render_as_string(hide_password=False)
    assert_disposable_database(connection_url, child_name)
    control = control_url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(control, autocommit=True) as connection:
        assert connection.execute("SELECT current_database()").fetchone()[0] == control_name
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(child_name)))
    engine = None
    original_overrides = app.dependency_overrides.copy()
    try:
        engine = create_engine(database_url)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT current_database()")) == child_name
        migration = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=API_ROOT,
            env={
                **os.environ,
                "ENVIRONMENT": "test",
                "DATABASE_URL": connection_url,
                "POSTGRES_INTEGRATION_URL": connection_url,
                "PRUFAI_DISPOSABLE_DATABASE": child_name,
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert migration.returncode == 0, migration.stderr
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        settings = Settings(
            _env_file=None,
            environment="test",
            database_url=connection_url,
            auth_rate_limit_backend="memory",
            auth_rate_limit_login_attempts=50,
        )
        with factory() as session:
            assert session.scalars(text("SELECT version_num FROM alembic_version")).all() == [
                repository_alembic_head()
            ]
            seed_defaults(session)
            admin = session.scalar(
                select(Role).where(
                    Role.key == "PRUFAI_SUPER_ADMIN", Role.scope == RoleScope.PLATFORM
                )
            )
            standards = session.scalar(
                select(Role).where(
                    Role.key == "PRUFAI_STANDARDS_ADMIN", Role.scope == RoleScope.PLATFORM
                )
            )
            assert admin is not None and standards is not None
            roles = {
                "issuer": admin,
                "author": standards,
                "other_issuer": admin,
                "former_author": standards,
            }
            user_ids = {}
            emails = {}
            for actor in (
                "issuer",
                "author",
                "other_issuer",
                "former_author",
                "reviewer",
                "other_reviewer",
                "disabled_reviewer",
            ):
                email = f"{actor}-{uuid.uuid4().hex}@scoped-source-test.invalid"
                user = User(email=email, normalized_email=email, status=UserStatus.ACTIVE)
                session.add(user)
                session.flush()
                AuthenticationService(settings).set_password(session, user, PASSWORD)
                user_ids[actor] = user.id
                emails[actor] = email
                if actor in roles:
                    session.add(PlatformRoleAssignment(user_id=user.id, role_id=roles[actor].id))
            organisations = [
                Organisation(
                    name=f"Synthetic scoped source organisation {number}",
                    slug=f"synthetic-scoped-source-{number}",
                    organisation_type=OrganisationType.LANGUAGE_SCHOOL,
                )
                for number in range(2)
            ]
            session.add_all(organisations)
            session.commit()
            organisation_id, other_organisation_id = (str(org.id) for org in organisations)

        def database():
            with factory() as session:
                yield session

        app.dependency_overrides.update(
            {
                get_db_session: database,
                get_settings: lambda: settings,
                auth_settings_dependency: lambda: settings,
            }
        )
        headers = {}
        with TestClient(app) as client:
            emails.update(
                {"issuer_second": emails["issuer"], "reviewer_second": emails["reviewer"]}
            )
            for actor, email in emails.items():
                login = client.post(
                    "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
                )
                assert login.status_code == 200, login.text
                headers[actor] = {"Authorization": "Bearer " + login.json()["access_token"]}
        with TestClient(app) as client:
            authority = client.post(
                REGISTRY + "/authorities",
                headers=headers["author"],
                json={
                    "code": "SCOPED_SOURCE_SYNTHETIC",
                    "name": "Synthetic source publisher",
                    "authority_type": "INTERNAL",
                },
            )
            assert authority.status_code == 201, authority.text
        yield Scenario(
            factory,
            headers,
            user_ids,
            organisation_id,
            other_organisation_id,
            authority.json()["id"],
        )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)
        if engine is not None:
            engine.dispose()
        assert_disposable_database(connection_url, child_name)
        with psycopg.connect(control, autocommit=True) as connection:
            assert connection.execute("SELECT current_database()").fetchone()[0] == control_name
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(child_name))
            )


def test_legacy_source_and_platform_permissions_remain_unchanged(scenario):
    version = scenario.version()
    grant, _ = scenario.grant([version])
    scenario.request("GET", _sources(grant) + "/" + version["id"])
    version_path = REGISTRY + f"/versions/{version['id']}"
    source_path = REGISTRY + f"/sources/{version['source_id']}"
    for path in (
        REGISTRY + "/sources",
        source_path,
        source_path + "/versions",
        version_path,
        version_path + "/pages",
        version_path + "/integrity",
        version_path + "/provenance",
        CONTROL + "/overview",
        CONTROL + "/source-registry",
        CONTROL + "/audit",
        CONTROL + "/system",
        CONTROL + "/standards",
        "/api/v1/platform/question-bank/items",
        "/api/v1/platform/standard-packs",
    ):
        scenario.request("GET", path, status=403)
    for path, payload in (
        (
            REGISTRY + "/retrieval/search",
            {
                "query": "Synthetic source",
                "mode": "HYBRID",
                "scope": {"document_version_ids": [version["id"]]},
            },
        ),
        (REGISTRY + "/retrieval/index", {"document_version_id": version["id"]}),
        (
            version_path + "/rights-review",
            {
                "expected_metadata_checksum": version["metadata_checksum"],
                "decision": "METADATA_ONLY",
                "basis_reference": PRIVATE_REFERENCE,
                "rights_holder": "Synthetic",
                "licence_type": "Synthetic",
                "retention_policy": "Synthetic",
            },
        ),
        (
            version_path + "/file",
            {"filename": "synthetic.txt", "content_base64": "SGVsbG8="},
        ),
        (version_path + "/ingest", None),
        (version_path + "/approve", {"expected_sha256": "a" * 64}),
        (version_path + "/reject", {"reason": "Synthetic deny test"}),
    ):
        scenario.request("POST", path, status=403, payload=payload)
    identity = scenario.request("GET", "/api/v1/auth/me")
    assert identity["scoped_source_review"] is True
    assert identity["platform_permissions"] == [] and identity["memberships"] == []
    with scenario.factory() as session:
        # There is no public role-assignment route. Verify the actual service guard
        # with the permissions of the authenticated scoped-only identity.
        with pytest.raises(AuthorizationError, match="platform.role.assign"):
            OrganisationService.assign_platform_role(
                session,
                TenantContext(
                    user_id=uuid.UUID(identity["id"]),
                    organisation_id=None,
                    membership_id=None,
                    platform_permissions=frozenset(identity["platform_permissions"]),
                ),
                user_id=uuid.UUID(identity["id"]),
                role_key="PRUFAI_SUPER_ADMIN",
            )
    scenario.request("GET", REGISTRY + "/sources", actor="issuer")
    scenario.request("GET", source_path + "/versions", actor="issuer")
    scenario.request("GET", version_path, actor="issuer")
    scenario.request("GET", CONTROL + "/overview", actor="issuer")


def _org_grants(org):
    return f"{SCOPED}/orgs/{org}/grants"


def _grant_path(grant, org=None):
    return f"{_org_grants(org or grant['organisation_id'])}/{grant['grant_id']}"


def _sources(grant):
    return _grant_path(grant) + "/sources"


def _decision_path(grant, version):
    return f"{_sources(grant)}/{version['id']}/decision"


def _pin(version, **overrides):
    return {
        "version_id": version["id"],
        "metadata_checksum": version["metadata_checksum"],
        **overrides,
    }


def _grant_payload(reviewer, versions, organisation, issuer, **overrides):
    now = datetime.now(timezone.utc)
    manifest = {
        "operation_id": str(uuid.uuid4()),
        "reviewer_user_id": str(reviewer),
        "purpose": "Synthetic bibliographic acceptance",
        "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "resources": [_pin(version) for version in versions],
        **overrides,
    }
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    payload = {"business_approval_reference": "sha256:" + digest, **manifest}
    scope = grant_scope_checksum(
        uuid.UUID(organisation),
        issuer,
        CreateGrant.model_validate({**payload, "approved_scope_checksum": "0" * 64}),
    )
    return {**payload, "approved_scope_checksum": scope}


def _decision(version, decision="METADATA_ONLY", **overrides):
    return {
        "operation_id": str(uuid.uuid4()),
        "expected_metadata_checksum": version["metadata_checksum"],
        "decision": decision,
        "basis_reference": PRIVATE_REFERENCE,
        "rights_holder": "Synthetic test authors",
        "licence_type": "SYNTHETIC BIBLIOGRAPHIC ONLY",
        "retention_policy": "Synthetic isolated acceptance; no redistribution",
        **overrides,
    }


def _rights(scenario, version):
    with scenario.factory() as session:
        row = session.scalar(
            select(KnowledgeLegalMetadata).where(
                KnowledgeLegalMetadata.document_version_id == uuid.UUID(version["id"])
            )
        )
        assert row is not None
        return row


def _scoped_audit(scenario):
    with scenario.factory() as session:
        return session.scalars(
            select(AuditEvent).where(AuditEvent.event_type.startswith("standards.scoped_review."))
        ).all()


def test_exact_five_pins_projection_pending_and_immutable_decisions(scenario):
    versions = [
        scenario.version(publication_date="2025-05-07T00:00:00+02:00"),
        *[scenario.version() for _ in range(4)],
    ]
    unrelated = scenario.version()
    grant, _ = scenario.grant(
        versions,
        resources=[
            _pin(versions[0]),
            _pin(
                versions[1],
                metadata_fields=["title", "rights_status"],
                allowed_decisions=["DENY"],
            ),
            *[_pin(version) for version in versions[2:]],
        ],
    )
    assert len(grant["scope_checksum"]) == 64 and grant["status"] == "ACTIVE"
    assert grant in scenario.request("GET", SCOPED + "/grants")
    assert grant not in scenario.request("GET", SCOPED + "/grants", actor="other_reviewer")
    scenario.request("GET", _sources(grant), status=401, extra={"Authorization": ""})
    bundle = scenario.request("GET", _sources(grant))
    assert bundle["scope_checksum"] == grant["scope_checksum"]
    assert {row["version_id"]: row["metadata_checksum"] for row in bundle["sources"]} == {
        row["id"]: row["metadata_checksum"] for row in versions
    }
    detail = scenario.request("GET", f"{_sources(grant)}/{versions[0]['id']}")
    assert detail["metadata"]["publication_date"]
    assert detail["metadata"]["publisher"] == "Synthetic source publisher"
    assert detail["metadata"]["rights_status"] == "NOT_REVIEWED"
    restricted = scenario.request("GET", f"{_sources(grant)}/{versions[1]['id']}")
    assert set(restricted["metadata"]) == {"title", "rights_status"}
    assert restricted["allowed_decisions"] == ["DENY"]
    pending = scenario.request("GET", f"{_sources(grant)}/{versions[2]['id']}")
    assert pending["metadata"]["publication_date"] is None
    assert pending["metadata"]["effective_from"] is None
    assert PRIVATE_SOURCE_NOTE not in json.dumps(bundle)
    scenario.request("GET", f"{_sources(grant)}/{unrelated['id']}", status=403)
    scenario.request("GET", _sources(grant), actor="other_reviewer", status=403)
    scenario.request(
        "GET",
        _grant_path(grant, scenario.other_organisation_id) + "/sources",
        status=403,
    )
    scenario.request(
        "GET",
        _sources(grant),
        status=(401, 403),
        extra={"X-PrufAI-Organisation-ID": scenario.other_organisation_id},
    )
    scenario.request(
        "POST",
        _decision_path(grant, unrelated),
        status=403,
        payload=_decision(unrelated),
    )
    scenario.request(
        "POST",
        _decision_path(grant, versions[1]),
        status=403,
        payload=_decision(versions[1]),
    )
    scenario.request(
        "POST",
        _decision_path(grant, versions[0]),
        status=403,
        payload=_decision(versions[0], "ALLOW_SYNTHETIC_INGESTION"),
    )
    scenario.request(
        "POST",
        _decision_path(grant, versions[0]),
        status=409,
        payload=_decision(versions[0], expected_metadata_checksum="1" * 64),
    )

    action = _decision(versions[0])
    done = scenario.request("POST", _decision_path(grant, versions[0]), payload=action)
    assert done["metadata"]["rights_status"] == "APPROVED"
    assert len(done["decision_checksum"]) == 64 and done["allowed_decisions"] == []
    assert scenario.request("POST", _decision_path(grant, versions[0]), payload=action) == done
    scenario.request(
        "POST",
        _decision_path(grant, versions[0]),
        status=409,
        payload={**action, "retention_policy": "Altered payload for the same operation"},
    )
    scenario.request(
        "POST",
        _decision_path(grant, versions[0]),
        status=409,
        payload=_decision(versions[0], "DENY"),
    )
    denied = scenario.request(
        "POST", _decision_path(grant, versions[1]), payload=_decision(versions[1], "DENY")
    )
    assert denied["metadata"]["rights_status"] == "REJECTED"

    approved_rights = _rights(scenario, versions[0])
    assert approved_rights.legal_review_status is LegalReviewStatus.APPROVED
    assert approved_rights.decision_checksum == done["decision_checksum"]
    assert approved_rights.legal_reviewed_by_user_id == scenario.user_ids["reviewer"]
    assert approved_rights.usage_rights == "BIBLIOGRAPHIC_REFERENCE_ONLY"
    assert approved_rights.ingestion_allowed is False
    assert approved_rights.derivative_use_allowed is False
    assert approved_rights.redistribution_allowed is False
    assert approved_rights.ai_processing_allowed is False
    assert approved_rights.approved_sha256 is None
    assert approved_rights.basis_reference == PRIVATE_REFERENCE
    assert _rights(scenario, versions[1]).usage_rights == "CONTENT_USE_DENIED"
    for version in [*versions[2:], unrelated]:
        assert _rights(scenario, version).legal_review_status is LegalReviewStatus.NOT_REVIEWED

    with scenario.factory() as session:
        audit = session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "standards.source_rights.reviewed",
                AuditEvent.entity_id == versions[0]["id"],
            )
        ).one()
        assert audit.actor_id == scenario.user_ids["reviewer"]
        assert audit.metadata_json["decision_checksum"] == done["decision_checksum"]
        with pytest.raises(DBAPIError, match="SOURCE_RIGHTS_DECISION_IMMUTABLE"):
            session.execute(
                text(
                    "UPDATE knowledge_legal_metadata SET retention_policy = 'tampered' "
                    "WHERE id = :id"
                ),
                {"id": approved_rights.id},
            )
        session.rollback()
        with pytest.raises(DBAPIError, match="SCOPED_SOURCE_REVIEW_HISTORY_IMMUTABLE"):
            session.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": audit.id})
        session.rollback()

    scoped_events = _scoped_audit(scenario)
    scoped_events = [receipt for receipt in scoped_events if receipt.entity_id == grant["grant_id"]]
    submissions = [
        receipt
        for receipt in scoped_events
        if receipt.event_type == "standards.scoped_review.review_submitted"
    ]
    assert len(submissions) == 2
    assert {receipt.actor_id for receipt in submissions} == {scenario.user_ids["reviewer"]}
    assert {receipt.metadata_json["source_version_id"] for receipt in submissions} == {
        version["id"] for version in versions[:2]
    }
    assert any(
        receipt.event_type == "standards.scoped_review.denied"
        and receipt.metadata_json["source_version_id"] == unrelated["id"]
        for receipt in scoped_events
    )
    for receipt in scoped_events:
        assert PRIVATE_REFERENCE not in str(receipt.metadata_json)
        assert PRIVATE_REFERENCE not in (receipt.reason or "")


def test_grant_authority_replay_and_competing_grants(scenario):
    version = scenario.version()
    org = scenario.new_organisation()
    path = _org_grants(org)
    payload = _grant_payload(
        scenario.user_ids["reviewer"], [version], org, scenario.user_ids["issuer"]
    )
    for actor in ("author", "reviewer"):
        scenario.request("POST", path, actor=actor, status=403, payload=payload)
    for recipient in ("issuer", "other_issuer"):
        scenario.grant(
            [version], org=org, status=403, reviewer_user_id=str(scenario.user_ids[recipient])
        )
    scenario.grant(
        [version],
        org=org,
        status=403,
        resources=[_pin(version, metadata_checksum="0" * 64)],
    )
    scenario.request(
        "POST",
        path,
        actor="issuer",
        status=422,
        payload={**payload, "starts_at": "2030-01-01T10:00:00"},
    )
    for invalid_input in (
        {"business_approval_reference": "unverified-manifest"},
        {"resources": [_pin(version, version_id="*")]},
        {"resources": [_pin(version), _pin(version)]},
        {"resources": [_pin(version, metadata_fields=["storage_key"])]},
        {"resources": [_pin(version, allowed_decisions=["WITHDRAW"])]},
        {"starts_at": 1893456000},
    ):
        scenario.request(
            "POST", path, actor="issuer", status=422, payload={**payload, **invalid_input}
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(
            scenario.request, "POST", path, actor="issuer", status=(200, 201), payload=payload
        )
        replay = workers.submit(
            scenario.request,
            "POST",
            path,
            actor="issuer_second",
            status=(200, 201),
            payload=payload,
        )
        grant = first.result(timeout=15)
        assert replay.result(timeout=15) == grant
    scenario.request(
        "POST",
        path,
        actor="issuer",
        status=409,
        payload={**payload, "purpose": "Altered approval purpose"},
    )
    scenario.request(
        "POST",
        path,
        actor="issuer",
        status=409,
        payload={**payload, "operation_id": str(uuid.uuid4())},
    )
    scenario.request(
        "POST",
        _grant_path(grant) + "/revoke",
        status=403,
        payload={"operation_id": str(uuid.uuid4())},
    )
    scenario.request(
        "POST",
        _grant_path(grant) + "/suspend",
        actor="author",
        status=403,
        payload={"operation_id": str(uuid.uuid4())},
    )


def test_registration_author_and_version_author_excluded(scenario):
    original = scenario.version(actor="former_author")
    successor = scenario.version(source_id=original["source_id"], supersedes=original["id"])
    authored_elsewhere = scenario.version(
        actor="former_author", source_id=scenario.version()["source_id"]
    )
    with scenario.factory() as session:
        role = session.scalar(
            select(PlatformRoleAssignment).where(
                PlatformRoleAssignment.user_id == scenario.user_ids["former_author"]
            )
        )
        assert role is not None
        role.status = PlatformAssignmentStatus.SUSPENDED
        session.commit()
    identity = scenario.request("GET", "/api/v1/auth/me", actor="former_author")
    assert identity["platform_permissions"] == []
    for version in (successor, authored_elsewhere):
        scenario.grant(
            [version],
            reviewer_user_id=str(scenario.user_ids["former_author"]),
            status=(403, 409),
        )
        assert _rights(scenario, version).legal_review_status is LegalReviewStatus.NOT_REVIEWED


def test_withdrawal_unpins_source_and_future_edition_is_not_inherited(scenario):
    version = scenario.version()
    grant, _ = scenario.grant([version])
    successor = scenario.version(source_id=version["source_id"], supersedes=version["id"])
    scenario.request("GET", f"{_sources(grant)}/{successor['id']}", status=403)
    scenario.request(
        "POST", _decision_path(grant, successor), status=403, payload=_decision(successor)
    )
    scenario.request("GET", f"{_sources(grant)}/{version['id']}", status=403)
    scenario.request("POST", _decision_path(grant, version), status=403, payload=_decision(version))
    assert scenario.request("GET", _sources(grant))["sources"] == []
    assert _rights(scenario, version).legal_review_status is LegalReviewStatus.NOT_REVIEWED
    path = REGISTRY + f"/versions/{version['id']}/rights-review"
    for decision in ("METADATA_ONLY", "WITHDRAW"):
        payload = _decision(version, decision)
        del payload["operation_id"]
        scenario.request("POST", path, actor="issuer", payload=payload)
    scenario.request("GET", f"{_sources(grant)}/{version['id']}", status=(403, 409))
    scenario.request(
        "POST", _decision_path(grant, version), status=(403, 409), payload=_decision(version)
    )
    assert _rights(scenario, version).legal_review_status is LegalReviewStatus.APPROVED
    assert _rights(scenario, successor).legal_review_status is LegalReviewStatus.NOT_REVIEWED


def test_suspension_revocation_and_old_session(scenario):
    version = scenario.version()
    grant, payload = scenario.grant([version])
    path = _grant_path(grant)
    operation = {"operation_id": str(uuid.uuid4())}
    suspended = scenario.request("POST", path + "/suspend", actor="issuer", payload=operation)
    assert suspended["status"] == "SUSPENDED"
    assert (
        scenario.request("POST", path + "/suspend", actor="issuer", payload=operation) == suspended
    )
    scenario.request("GET", _sources(grant), status=403)
    scenario.request("POST", _decision_path(grant, version), status=403, payload=_decision(version))
    revoked = scenario.request(
        "POST", path + "/revoke", actor="issuer", payload={"operation_id": str(uuid.uuid4())}
    )
    assert revoked["status"] == "REVOKED"
    replay = scenario.request(
        "POST",
        _org_grants(grant["organisation_id"]),
        actor="issuer",
        status=(200, 201),
        payload=payload,
    )
    assert replay["grant_id"] == grant["grant_id"] and replay["status"] == "REVOKED"
    denied_reuse = scenario.request(
        "POST",
        _org_grants(grant["organisation_id"]),
        actor="issuer",
        status=409,
        payload={**payload, "operation_id": str(uuid.uuid4())},
    )
    assert denied_reuse["detail"] == "APPROVAL_REFERENCE_ALREADY_USED"
    successor, _ = scenario.grant([version], org=grant["organisation_id"])
    assert successor["grant_id"] != grant["grant_id"] and successor["status"] == "ACTIVE"
    scenario.request("GET", _sources(grant), status=403)
    assert _rights(scenario, version).legal_review_status is LegalReviewStatus.NOT_REVIEWED

    with scenario.factory() as session:
        event = session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type.startswith("standards.scoped_review."),
                AuditEvent.entity_id == grant["grant_id"],
            )
        ).first()
        assert event is not None
        for statement, identifier in (
            ("DELETE FROM audit_events WHERE id = :id", event.id),
            (
                "UPDATE scoped_source_review_grants SET status = 'ACTIVE' WHERE id = :id",
                uuid.UUID(grant["grant_id"]),
            ),
            (
                "UPDATE scoped_source_review_grants "
                "SET scope_checksum = repeat('0', 64), status = 'SUSPENDED' WHERE id = :id",
                uuid.UUID(successor["grant_id"]),
            ),
            (
                "DELETE FROM scoped_source_review_operations WHERE operation_id = :id",
                uuid.UUID(operation["operation_id"]),
            ),
        ):
            with pytest.raises(DBAPIError, match="SCOPED_SOURCE_REVIEW_HISTORY_IMMUTABLE"):
                session.execute(text(statement), {"id": identifier})
            session.rollback()


def test_schedule_activation_expiry_and_disabled_account(scenario):
    version = scenario.version()
    now = datetime.now(timezone.utc)
    starts = now + timedelta(seconds=2)
    expires = now + timedelta(seconds=5)
    grant, _ = scenario.grant(
        [version], starts_at=starts.isoformat(), expires_at=expires.isoformat()
    )
    assert grant["status"] == "APPROVED"
    scenario.request("GET", _sources(grant), status=403)
    time.sleep(max(0, (starts - datetime.now(timezone.utc)).total_seconds()) + 0.1)
    assert scenario.request("GET", _sources(grant))["status"] == "ACTIVE"
    time.sleep(max(0, (expires - datetime.now(timezone.utc)).total_seconds()) + 0.1)
    scenario.request("GET", _sources(grant), status=403)
    scenario.request("POST", _decision_path(grant, version), status=403, payload=_decision(version))
    assert _rights(scenario, version).legal_review_status is LegalReviewStatus.NOT_REVIEWED
    assert any(
        event.entity_id == grant["grant_id"] and event.event_type.endswith(".expired")
        for event in _scoped_audit(scenario)
    )

    disabled_grant, _ = scenario.grant(
        [version], reviewer_user_id=str(scenario.user_ids["disabled_reviewer"])
    )
    scenario.request("GET", _sources(disabled_grant), actor="disabled_reviewer")
    with scenario.factory() as session:
        user = session.get(User, scenario.user_ids["disabled_reviewer"])
        assert user is not None
        user.status = UserStatus.SUSPENDED
        session.commit()
    scenario.request("GET", _sources(disabled_grant), actor="disabled_reviewer", status=(401, 403))


def test_distinct_grant_operations_compete_for_one_scope(scenario):
    version = scenario.version()
    org = scenario.new_organisation()
    rendezvous = Barrier(2)

    def issue(actor):
        payload = _grant_payload(
            scenario.user_ids["reviewer"], [version], org, scenario.user_ids[actor]
        )
        rendezvous.wait(timeout=10)
        return scenario.request(
            "POST", _org_grants(org), actor=actor, status=(200, 201, 409), payload=payload
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(issue, "issuer")
        second = workers.submit(issue, "other_issuer")
        results = [first.result(timeout=15), second.result(timeout=15)]
    assert sum("grant_id" in result for result in results) == 1
    with scenario.factory() as session:
        rows = session.execute(
            text("SELECT count(*) FROM scoped_source_review_grants WHERE organisation_id = :org"),
            {"org": uuid.UUID(org)},
        ).scalar_one()
        assert rows == 1


def test_concurrent_rights_submissions_preserve_one_domain_decision(scenario):
    version = scenario.version()
    grant, _ = scenario.grant([version])
    path = _decision_path(grant, version)
    rendezvous = Barrier(2)

    def submit(decision, actor):
        payload = _decision(version, decision)
        rendezvous.wait(timeout=10)
        return scenario.request("POST", path, actor=actor, status=(200, 409), payload=payload)

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(submit, "METADATA_ONLY", "reviewer")
        second = workers.submit(submit, "DENY", "reviewer_second")
        results = [first.result(timeout=15), second.result(timeout=15)]
    decision_checksums = [
        result["decision_checksum"] for result in results if "decision_checksum" in result
    ]
    assert decision_checksums == [_rights(scenario, version).decision_checksum]
    with scenario.factory() as session:
        audit = session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "standards.source_rights.reviewed",
                AuditEvent.entity_id == version["id"],
            )
        ).one()
        assert audit.metadata_json["decision_checksum"] == decision_checksums[0]
        assert audit.metadata_json["scoped_grant_id"] == grant["grant_id"]


def _assert_pg_lock_wait(scenario, waiting_pid, locking_pid):
    deadline = time.monotonic() + 5
    with scenario.factory() as observer:
        while time.monotonic() < deadline:
            if observer.scalar(
                text("SELECT :locking = ANY(pg_blocking_pids(:waiting))"),
                {"locking": locking_pid, "waiting": waiting_pid},
            ):
                return
            time.sleep(0.01)
    pytest.fail("PostgreSQL did not report the expected row-lock dependency")


def test_committing_revocation_wins_against_a_waiting_review(scenario):
    approved, waiting = [scenario.version() for _ in range(2)]
    grant, _ = scenario.grant([approved, waiting])
    done = scenario.request("POST", _decision_path(grant, approved), payload=_decision(approved))
    revoked_updated = Event()
    review_attempted_lock = Event()
    release_revocation = Event()
    pids = {}
    bind = scenario.factory.kw["bind"]

    def pause_after_revocation_update(conn, cursor, statement, parameters, context, executemany):
        if statement.upper().startswith("UPDATE SCOPED_SOURCE_REVIEW_GRANTS"):
            pids["revocation"] = conn.connection.driver_connection.info.backend_pid
            revoked_updated.set()
            assert release_revocation.wait(timeout=15)

    def observe_review_lock_attempt(conn, cursor, statement, parameters, context, executemany):
        normalized = statement.upper()
        if (
            revoked_updated.is_set()
            and "FROM SCOPED_SOURCE_REVIEW_GRANTS" in normalized
            and "FOR UPDATE" in normalized
        ):
            pids["review"] = conn.connection.driver_connection.info.backend_pid
            review_attempted_lock.set()

    event.listen(bind, "after_cursor_execute", pause_after_revocation_update)
    event.listen(bind, "before_cursor_execute", observe_review_lock_attempt)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            revoked = workers.submit(
                scenario.request,
                "POST",
                _grant_path(grant) + "/revoke",
                actor="issuer",
                payload={"operation_id": str(uuid.uuid4())},
            )
            assert revoked_updated.wait(timeout=10)
            review = workers.submit(
                scenario.request,
                "POST",
                _decision_path(grant, waiting),
                status=403,
                payload=_decision(waiting),
            )
            try:
                assert review_attempted_lock.wait(timeout=5)
                _assert_pg_lock_wait(scenario, pids["review"], pids["revocation"])
            finally:
                release_revocation.set()
            assert revoked.result(timeout=15)["status"] == "REVOKED"
            review.result(timeout=15)
    finally:
        event.remove(bind, "after_cursor_execute", pause_after_revocation_update)
        event.remove(bind, "before_cursor_execute", observe_review_lock_attempt)
    assert review_attempted_lock.is_set()
    assert _rights(scenario, waiting).legal_review_status is LegalReviewStatus.NOT_REVIEWED
    assert _rights(scenario, approved).decision_checksum == done["decision_checksum"]
    with scenario.factory() as session:
        assert (
            session.scalar(
                select(AuditEvent.id).where(
                    AuditEvent.event_type == "standards.source_rights.reviewed",
                    AuditEvent.entity_id == waiting["id"],
                )
            )
            is None
        )


def test_display_metadata_drift_invalidates_the_grant_pin(scenario):
    version = scenario.version()
    grant, _ = scenario.grant([version])
    detail_path = f"{_sources(grant)}/{version['id']}"
    assert scenario.request("GET", detail_path)["metadata_checksum"] == version["metadata_checksum"]

    with scenario.factory() as session:
        # The legacy version checksum does not include the title stored on its
        # parent source. Simulate a changed projection only in this fixture DB.
        source = session.get(KnowledgeSource, uuid.UUID(version["source_id"]))
        assert source is not None
        source.title = "Changed synthetic parent source title"
        session.commit()

    scenario.request("GET", detail_path, status=403)
    assert scenario.request("GET", _sources(grant))["sources"] == []
    scenario.request("POST", _decision_path(grant, version), status=403, payload=_decision(version))
    assert _rights(scenario, version).legal_review_status is LegalReviewStatus.NOT_REVIEWED


def test_review_rechecks_exact_expiry_boundary_after_waiting_for_source_lock(scenario, monkeypatch):
    version = scenario.version()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=1)
    clock = [now]
    monkeypatch.setattr(scoped_service, "utcnow", lambda: clock[0])
    grant, _ = scenario.grant([version], expires_at=expires.isoformat())
    waited = Event()
    waiting = []
    bind = scenario.factory.kw["bind"]

    def observe_version_lock_attempt(conn, cursor, statement, parameters, context, executemany):
        normalized = statement.upper()
        if "FROM KNOWLEDGE_DOCUMENT_VERSIONS" in normalized and "FOR UPDATE" in normalized:
            waiting.append(conn.connection.driver_connection.info.backend_pid)
            waited.set()

    with ThreadPoolExecutor(max_workers=1) as worker, scenario.factory() as blocker:
        blocker.execute(
            text("SELECT id FROM knowledge_document_versions WHERE id = :id FOR UPDATE"),
            {"id": uuid.UUID(version["id"])},
        )
        blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
        event.listen(bind, "before_cursor_execute", observe_version_lock_attempt)
        try:
            review = worker.submit(
                scenario.request,
                "POST",
                _decision_path(grant, version),
                status=403,
                payload=_decision(version),
            )
            assert waited.wait(timeout=5)
            _assert_pg_lock_wait(scenario, waiting[0], blocker_pid)
            clock[0] = expires
            blocker.rollback()
            review.result(timeout=15)
        finally:
            event.remove(bind, "before_cursor_execute", observe_version_lock_attempt)
    assert _rights(scenario, version).legal_review_status is LegalReviewStatus.NOT_REVIEWED
    assert any(
        receipt.entity_id == grant["grant_id"]
        and receipt.event_type == "standards.scoped_review.expired"
        for receipt in _scoped_audit(scenario)
    )
