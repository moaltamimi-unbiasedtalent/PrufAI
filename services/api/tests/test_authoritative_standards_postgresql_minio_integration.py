"""S1 HTTP acceptance against isolated Alembic PostgreSQL/pgvector and private MinIO."""

import base64
import hashlib
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.auth.api import auth_settings_dependency
from app.config import Settings, get_settings
from app.db import get_db_session
from app.knowledge.storage import S3CompatibleObjectStorage, build_object_storage
from app.main import app
from app.models import (
    KnowledgeDocumentVersion,
    KnowledgeIngestionJob,
    KnowledgeSource,
    MembershipRole,
    Organisation,
    OrganisationMembership,
    PlatformRoleAssignment,
    Role,
    User,
)
from app.models.enums import KnowledgeScope, OrganisationType, RoleScope
from app.models.retrieval import KnowledgeChunk, KnowledgeChunkEmbedding
from app.services.knowledge import seed_knowledge_authorities
from tests.alembic_helpers import repository_alembic_head
from tests.test_expert_references_postgresql_integration import PASSWORD, ExpertScenario
from tests.test_expert_references_postgresql_integration import expert as _expert

expert = _expert

REQUIRED = any(
    os.getenv(flag) == "1"
    for flag in (
        "PRUFAI_REQUIRE_POSTGRES_INTEGRATION",
        "PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION",
    )
)
REQUIRED_ENV = (
    "POSTGRES_INTEGRATION_URL",
    "OBJECT_STORAGE_ENDPOINT",
    "OBJECT_STORAGE_ACCESS_KEY",
    "OBJECT_STORAGE_SECRET_KEY",
    "OBJECT_STORAGE_BUCKET",
)
ROOT = "/api/v1/platform/standards"
PACK_ROOT = "/api/v1/platform"
SYNTHETIC = (
    "Eigenes synthetisches Testdokument. Der Fahrradtreff am Bahnhof beginnt am Samstag. "
    "Bei starkem Regen treffen sich die Teilnehmenden am Sonntag. Diese Aufgabe enthält "
    "keine externen Prüfungsunterlagen und erlaubt keinen Rückschluss auf offizielle Prüfungen."
).encode()
DIGEST = hashlib.sha256(SYNTHETIC).hexdigest()


@pytest.fixture(scope="module")
def configured():
    missing = [key for key in REQUIRED_ENV if not os.getenv(key)]
    if missing:
        if REQUIRED:
            pytest.fail("Required S1 services not configured: " + ", ".join(missing))
        pytest.skip("S1 acceptance requires both Alembic PostgreSQL and real MinIO")


@dataclass
class StandardsProof:
    expert: ExpertScenario = field(repr=False)
    settings: Settings = field(repr=False)
    storage: S3CompatibleObjectStorage = field(repr=False)
    headers: dict[str, dict[str, str]] = field(repr=False)

    def http(self, method, path, *, actor="creator", status=200, json=None):
        # Each call has a fresh HTTP client and DB session. The separate browser
        # journey additionally restarts the actual API process.
        with TestClient(app) as client:
            response = client.request(method, path, headers=self.headers[actor], json=json)
        assert response.status_code in ((status,) if isinstance(status, int) else status), (
            method,
            path,
            response.status_code,
            response.text,
        )
        return response.json()

    def rights(self, version, decision, *, actor="approver", status=200, digest=None, **extra):
        payload = {
            "expected_metadata_checksum": version["metadata_checksum"],
            "decision": decision,
            "rights_holder": "PrüfAI S1 synthetic test authors",
            "licence_type": "S1 TEST ONLY",
            "basis_reference": "synthetic:s1-fixture:independent-review",
            "retention_policy": "Isolated acceptance evidence; no redistribution",
            **extra,
        }
        if digest is not None:
            payload["expected_sha256"] = digest
        return self.http(
            "POST",
            ROOT + f"/versions/{version['id']}/rights-review",
            actor=actor,
            status=status,
            json=payload,
        )

    def upload(self, version, content=SYNTHETIC, *, status=200):
        return self.http(
            "POST",
            ROOT + f"/versions/{version['id']}/file",
            status=status,
            json={
                "filename": "s1-original.txt",
                "declared_mime_type": "text/plain",
                "content_base64": base64.b64encode(content).decode(),
            },
        )

    def synthetic_version(self, *, source_id=None, supersedes=None):
        marker = uuid.uuid4().hex
        if source_id is None:
            authority = self.http(
                "POST",
                ROOT + "/authorities",
                status=201,
                json={
                    "code": "S1_INTERNAL_" + marker,
                    "name": "Synthetic S1 test author",
                    "authority_type": "INTERNAL",
                },
            )
            source_id = self.http(
                "POST",
                ROOT + "/sources",
                status=201,
                json={
                    "authority_id": authority["id"],
                    "source_code": "S1_TEST_" + marker,
                    "title": "Original test document",
                    "document_type": "STANDARD",
                    "assessment_family": "LANGUAGE",
                    "language": "de",
                    "cefr_level": "B1",
                },
            )["id"]
        version = self.http(
            "POST",
            ROOT + f"/sources/{source_id}/versions",
            status=201,
            json={
                "version_label": "Synthetic " + marker,
                "content_mode": "ORIGINAL_SYNTHETIC",
                "rights_holder": "PrüfAI S1 synthetic test authors",
                "licence_type": "S1 TEST ONLY",
                "supersedes_document_version_id": supersedes,
            },
        )
        return version, source_id

    def ingest(self, version, *, status=200):
        return self.http("POST", ROOT + f"/versions/{version['id']}/ingest", status=status)

    def approve(self, version, *, actor="approver", status=200):
        return self.http(
            "POST",
            ROOT + f"/versions/{version['id']}/approve",
            actor=actor,
            status=status,
            json={"expected_sha256": DIGEST},
        )

    def index(self, version, *, status=200):
        return self.http(
            "POST",
            ROOT + "/retrieval/index",
            status=status,
            json={
                "document_version_id": version["id"],
            },
        )

    def search(self, version, *, actor="creator", status=200, **scope):
        return self.http(
            "POST",
            ROOT + "/retrieval/search",
            actor=actor,
            status=status,
            json={
                "query": "Fahrradtreff Bahnhof Samstag",
                "mode": "HYBRID",
                "scope": {"document_version_ids": [version["id"]], "language": "de", **scope},
            },
        )

    def object_key(self, version):
        with self.expert.factory() as session:
            stored = session.get(KnowledgeDocumentVersion, uuid.UUID(version["id"]))
            assert stored.storage_bucket == self.storage.bucket
            assert stored.file_size == len(SYNTHETIC)
            assert stored.sha256 == DIGEST
            assert stored.storage_key
            return stored.storage_key


@pytest.fixture(scope="module")
def proof(configured, expert):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=expert.database_url,
        object_storage_backend="s3",
        object_storage_bucket="prufai-s1-" + uuid.uuid4().hex,
        object_storage_endpoint=os.environ["OBJECT_STORAGE_ENDPOINT"],
        object_storage_access_key=os.environ["OBJECT_STORAGE_ACCESS_KEY"],
        object_storage_secret_key=os.environ["OBJECT_STORAGE_SECRET_KEY"],
        knowledge_embedding_backend="deterministic",
        knowledge_reranker_backend="deterministic",
    )
    storage = build_object_storage(settings)
    assert isinstance(storage, S3CompatibleObjectStorage)
    storage.client.create_bucket(Bucket=storage.bucket)
    storage.client.head_bucket(Bucket=storage.bucket)

    with expert.factory() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == (
            repository_alembic_head()
        )
        assert session.scalar(text("SELECT extname FROM pg_extension WHERE extname='vector'")) == (
            "vector"
        )
        admin = session.scalar(
            select(Role).where(
                Role.key == "PRUFAI_SUPER_ADMIN",
                Role.scope == RoleScope.PLATFORM,
            )
        )
        tenant_admin = session.scalar(
            select(Role).where(
                Role.key == "INSTITUTION_ADMIN",
                Role.scope == RoleScope.TENANT,
            )
        )
        assert admin is not None and tenant_admin is not None
        for actor in ("creator", "approver"):
            session.add(PlatformRoleAssignment(user_id=expert.actors[actor], role_id=admin.id))
        organisation = Organisation(
            name="Synthetic S1 fixture institution",
            slug="s1-" + uuid.uuid4().hex,
            organisation_type=OrganisationType.LANGUAGE_SCHOOL,
        )
        session.add(organisation)
        session.flush()
        membership = OrganisationMembership(
            organisation_id=organisation.id,
            user_id=expert.actors["revoked"],
        )
        session.add(membership)
        session.flush()
        # Remove the expert fixture's scoped platform role to make this a tenant-only identity.
        assignment = session.scalar(
            select(PlatformRoleAssignment).where(
                PlatformRoleAssignment.user_id == expert.actors["revoked"],
            )
        )
        assert assignment is not None
        session.delete(assignment)
        session.add(MembershipRole(membership_id=membership.id, role_id=tenant_admin.id))
        session.commit()
        org_id = str(organisation.id)
        emails = {
            key: session.get(User, expert.actors[key]).email
            for key in ("creator", "approver", "candidate", "revoked")
        }

    def session_dependency():
        with expert.factory() as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides.update(
        {
            get_db_session: session_dependency,
            get_settings: lambda: settings,
            auth_settings_dependency: lambda: settings,
        }
    )
    try:
        headers = {}
        with TestClient(app) as client:
            for actor, email in emails.items():
                response = client.post(
                    "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
                )
                assert response.status_code == 200, response.text
                headers[actor] = {"Authorization": "Bearer " + response.json()["access_token"]}
        headers["revoked"]["X-PrufAI-Organisation-ID"] = org_id
        yield StandardsProof(expert, settings, storage, headers)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        for page in storage.client.get_paginator("list_objects_v2").paginate(Bucket=storage.bucket):
            for item in page.get("Contents", []):
                storage.client.delete_object(Bucket=storage.bucket, Key=item["Key"])
        storage.client.delete_bucket(Bucket=storage.bucket)


def test_curated_metadata_independent_review_and_sealed_b1_pack(proof):
    with proof.expert.factory() as session:
        seed_knowledge_authorities(session)
    entries = {row["key"]: row for row in proof.http("GET", ROOT + "/catalogue")}
    assert set(entries) == {
        "cefr-companion-2020",
        "gast-dtz-information",
        "bamf-lid-catalogue",
        "inttestv-2026-306",
    }
    assert "978-92-871-8621-8" in entries["cefr-companion-2020"]["version_label"]
    assert entries["gast-dtz-information"]["publication_date"] is None
    lid = entries["bamf-lid-catalogue"]
    assert lid["assessment_family"] == "INTEGRATION_KNOWLEDGE"
    assert lid["cefr_level"] is None
    assert "07.05.2025" in lid["version_label"]
    regulation = entries["inttestv-2026-306"]
    assert regulation["publication_date"].startswith("2026-10-20")
    assert datetime.fromisoformat(regulation["effective_from"]).timestamp() == (
        datetime.fromisoformat("2026-11-01T00:00:00+01:00").timestamp()
    )
    registered = {}
    for key, entry in entries.items():
        assert entry["content_mode"] == "METADATA_ONLY"
        endpoint = ROOT + f"/catalogue/{key}/register"
        row = proof.http("POST", endpoint)
        assert proof.http("POST", endpoint) == row
        assert row["sha256"] is None and len(row["metadata_checksum"]) == 64
        registered[key] = {**row, "id": row["document_version_id"]}
        detail = proof.http("GET", ROOT + "/versions/" + row["document_version_id"])
        assert detail["source_url"] == (entry["edition_url"] or entry["source_url"])

    cefr = registered["cefr-companion-2020"]
    draft_path = PACK_ROOT + "/standard-packs/cefr-b1-foundation"
    pin = {"source_version_id": cefr["id"], "expected_metadata_checksum": cefr["metadata_checksum"]}
    proof.http("POST", draft_path, status=409, json=pin)
    proof.rights(cefr, "METADATA_ONLY", actor="creator", status=403)
    proof.rights(
        cefr,
        "METADATA_ONLY",
        rights_holder="Council of Europe",
        licence_type="Bibliographic reference only",
    )
    proof.upload(cefr, status=(400, 409))
    proof.ingest(cefr, status=(400, 409))
    proof.index(cefr, status=400)
    assert proof.search(cefr)["results"] == []
    proof.rights(cefr, "ALLOW_SYNTHETIC_INGESTION", digest=DIGEST, status=400)
    proof.http("POST", draft_path, status=409, json={**pin, "expected_metadata_checksum": "0" * 64})
    proof.http(
        "POST",
        draft_path,
        status=409,
        json={**pin, "source_version_id": registered["bamf-lid-catalogue"]["id"]},
    )
    draft = proof.http("POST", draft_path, status=201, json=pin)
    assert proof.http("POST", draft_path, status=201, json=pin) == draft
    assert draft["status"] == "DRAFT" and draft["supported_levels"] == ["B1"]
    assert draft["allowed_classifications"] == ["PRACTICE"]
    assert len(draft["checksum"]) == 64
    assert {row["skill"] for row in draft["constructs"]} == {
        "READING",
        "LISTENING",
        "WRITING",
        "SPEAKING",
    }
    assert len(draft["source_bindings"]) == 1
    assert draft["source_bindings"][0]["document_version_id"] == cefr["id"]
    assert (
        draft["source_bindings"][0]["source_url"] == entries["cefr-companion-2020"]["edition_url"]
    )
    assert draft["source_bindings"][0]["source_metadata_checksum"] == cefr["metadata_checksum"]
    assert any("EXPERT" in blocker for blocker in draft["publication_blockers"])
    assert any("RIGHTS" in blocker for blocker in draft["publication_blockers"])
    path = PACK_ROOT + f"/standard-pack-versions/{draft['id']}"
    assert proof.http("GET", path)["checksum"] == draft["checksum"]
    for action in ("approve", "publish"):
        proof.http("POST", path + "/" + action, actor="approver", status=409)
    assert proof.http("GET", path)["status"] == "DRAFT"


def test_private_upload_integrity_index_and_exact_pgvector_citation(proof):
    version, _ = proof.synthetic_version()
    proof.upload(version, status=(400, 409))
    proof.ingest(version, status=(400, 409))
    proof.rights(version, "ALLOW_SYNTHETIC_INGESTION", actor="creator", status=403, digest=DIGEST)
    proof.rights(version, "ALLOW_SYNTHETIC_INGESTION", digest=DIGEST)
    proof.upload(version, b"changed-before-registration", status=(400, 409))
    stored = proof.upload(version)
    assert stored["sha256"] == DIGEST
    assert proof.upload(version)["sha256"] == DIGEST
    proof.upload(version, b"changed-after-registration", status=(400, 409))

    key = proof.object_key(version)
    fresh_storage = build_object_storage(proof.settings)
    assert isinstance(fresh_storage, S3CompatibleObjectStorage)
    assert fresh_storage is not proof.storage and fresh_storage.get(key) == SYNTHETIC
    anonymous_url = f"{proof.settings.object_storage_endpoint}/{proof.storage.bucket}/{quote(key)}"
    with pytest.raises(HTTPError) as error:
        urlopen(anonymous_url, timeout=5)
    assert error.value.code == 403
    error.value.close()

    path = ROOT + f"/versions/{version['id']}"
    assert proof.http("GET", path + "/integrity")["sha256"] == DIGEST
    job = proof.ingest(version)
    assert job["status"] == "SUCCEEDED" and job["pages_detected"] >= 1
    assert proof.ingest(version)["id"] == job["id"]
    proof.approve(version, actor="creator", status=403)
    proof.approve(version)
    indexed = proof.index(version)
    assert indexed["status"] == "SUCCEEDED" and indexed["execution_class"] == "TEST_ONLY"
    assert proof.index(version)["id"] == indexed["id"]

    result = proof.search(version, expected_document_checksum=DIGEST)["results"]
    assert proof.search(version, expected_document_checksum="0" * 64)["results"] == []
    assert proof.search({**version, "id": str(uuid.uuid4())})["results"] == []
    assert result and any(row["matched_vector"] for row in result)
    citation = result[0]["citation"]
    assert citation["document_version_id"] == version["id"]
    assert citation["checksum"] == DIGEST
    assert citation["page_number"] == 1
    assert citation["citation_allowed"] is True and citation["text_display_allowed"] is False
    assert "text" not in result[0] and "section_title" not in result[0]
    assert proof.search(version)["results"][0]["citation"] == citation
    assert not any(secret in str(result) for secret in (key, proof.storage.bucket, anonymous_url))
    with proof.expert.factory() as session:
        chunk = session.scalar(
            select(KnowledgeChunk).where(
                KnowledgeChunk.document_version_id == uuid.UUID(version["id"]),
            )
        )
        assert chunk is not None
        assert (
            session.scalar(
                select(KnowledgeChunkEmbedding.id).where(
                    KnowledgeChunkEmbedding.chunk_id == chunk.id,
                )
            )
            is not None
        )

    for actor in ("candidate", "revoked"):
        proof.search(version, actor=actor, status=403)
        proof.http("GET", path + "/integrity", actor=actor, status=403)
        proof.rights(version, "WITHDRAW", actor=actor, status=403)
    assert proof.search(version, assessment_family="INTEGRATION_KNOWLEDGE")["results"] == []
    # Selecting a configured external provider cannot turn synthetic technical
    # permission into external authorization. There is no real provider call.
    override = app.dependency_overrides[get_settings]
    app.dependency_overrides[get_settings] = lambda: proof.settings.model_copy(
        update={"knowledge_embedding_backend": "openai"}
    )
    try:
        denied = proof.search(version, status=403)
        assert denied["detail"] == "STANDARDS_SYNTHETIC_TEST_EMBEDDINGS_REQUIRED"
        proof.index(version, status=403)
    finally:
        app.dependency_overrides[get_settings] = override


def test_tamper_fails_then_exact_bytes_recover_and_withdrawal_is_not_substituted(proof):
    version, source_id = proof.synthetic_version()
    proof.rights(version, "ALLOW_SYNTHETIC_INGESTION", digest=DIGEST)
    proof.upload(version)
    key = proof.object_key(version)
    proof.storage.put(key, b"X" + SYNTHETIC[1:], "text/plain")
    broken = proof.ingest(version)
    assert broken["status"] == "FAILED" and broken["error_code"] == "SOURCE_OBJECT_INTEGRITY_FAILED"
    detail = proof.http("GET", ROOT + f"/versions/{version['id']}")
    assert detail["latest_ingestion"]["id"] == broken["id"]
    proof.approve(version, status=(400, 409))
    proof.index(version, status=(400, 409))
    proof.storage.put(key, SYNTHETIC, "text/plain")
    repaired = proof.ingest(version)
    assert repaired["status"] == "SUCCEEDED"
    assert repaired["id"] != broken.get("id")
    proof.approve(version)
    proof.index(version)
    assert proof.search(version)["results"]
    proof.rights(version, "WITHDRAW")
    assert proof.search(version)["results"] == []
    proof.http("GET", ROOT + f"/versions/{version['id']}/integrity", status=(400, 403, 409))

    successor, _ = proof.synthetic_version(source_id=source_id, supersedes=version["id"])
    proof.rights(successor, "ALLOW_SYNTHETIC_INGESTION", digest=DIGEST)
    proof.upload(successor)
    assert proof.ingest(successor)["status"] == "SUCCEEDED"
    proof.approve(successor)
    proof.index(successor)
    assert (
        proof.search(successor)["results"][0]["citation"]["document_version_id"] == successor["id"]
    )
    assert proof.search(version)["results"] == []
    with proof.expert.factory() as session:
        old = session.get(KnowledgeDocumentVersion, uuid.UUID(version["id"]))
        new = session.get(KnowledgeDocumentVersion, uuid.UUID(successor["id"]))
        assert old.status.value == "RETIRED" and old.sha256 == DIGEST
        assert new.supersedes_document_version_id == old.id

        for table, update, error in (
            (
                "knowledge_document_versions",
                "sha256 = repeat('0', 64)",
                "SOURCE_OBJECT_IDENTITY_IMMUTABLE",
            ),
            (
                "knowledge_document_versions",
                "version_label = 'changed'",
                "SOURCE_VERSION_HISTORY_IMMUTABLE",
            ),
            (
                "knowledge_legal_metadata",
                "ingestion_allowed = false",
                "SOURCE_RIGHTS_DECISION_IMMUTABLE",
            ),
        ):
            column = "document_version_id" if table == "knowledge_legal_metadata" else "id"
            with pytest.raises(DBAPIError, match=error), session.begin_nested():
                session.execute(
                    text(f"UPDATE {table} SET {update} WHERE {column} = :id"), {"id": old.id}
                )


def test_legacy_tenant_private_source_is_absent_from_platform_metadata_and_retrieval(proof):
    _, shared_id = proof.synthetic_version()
    with proof.expert.factory() as session:
        shared = session.get(KnowledgeSource, uuid.UUID(shared_id))
        private = KnowledgeSource(
            authority_id=shared.authority_id,
            source_code="S1_PRIVATE_" + uuid.uuid4().hex,
            title="Legacy tenant private test source",
            scope=KnowledgeScope.TENANT_PRIVATE,
            assessment_family=shared.assessment_family,
            document_type=shared.document_type,
        )
        session.add(private)
        session.flush()
        version = KnowledgeDocumentVersion(
            source_id=private.id,
            version_label="Legacy private test version",
            created_at=datetime.now(timezone.utc),
        )
        session.add(version)
        session.flush()
        job = KnowledgeIngestionJob(
            document_version_id=version.id,
            created_at=datetime.now(timezone.utc),
            created_by_user_id=proof.expert.actors["creator"],
        )
        session.add(job)
        session.flush()
        source_id, version_id, job_id = map(str, (private.id, version.id, job.id))
        session.commit()

    registry = PACK_ROOT + "/control-center/source-registry?limit=100&kind="
    for kind, hidden in (("sources", source_id), ("documents", version_id), ("ingestions", job_id)):
        assert hidden not in {row["id"] for row in proof.http("GET", registry + kind)["items"]}
    proof.http("GET", ROOT + f"/sources/{source_id}", status=404)
    proof.http("GET", ROOT + f"/versions/{version_id}", status=404)
    assert proof.search({"id": version_id})["results"] == []
