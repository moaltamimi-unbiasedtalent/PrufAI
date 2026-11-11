"""O2A synthetic assessment, privacy and object recovery assertions."""

from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from app.auth.api import Principal
from app.auth.service import AuthenticationService
from app.authorization import build_tenant_context
from app.config import Settings
from app.models import (
    AuditEvent,
    CandidateMediaAsset,
    KnowledgeDocumentVersion,
    MembershipRole,
    Organisation,
    OrganisationMembership,
    ResultVersion,
    Role,
    User,
)
from app.models.enums import OrganisationType, RoleScope
from app.privacy import service as privacy
from app.privacy.schemas import CaseIntake, Verification
from app.result_release.service import ResultReleaseService
from app.runtime.service import CandidateRuntimeService
from app.scoring.service import ScoringService
from app.scripts.seed_demo import GOLDEN_CANDIDATE_EMAIL, reconcile_demo
from app.services.knowledge import KnowledgeRegistryService
from network import request
from psycopg import sql
from recovery import PERMISSIONS, assert_private, engine_for, inventory, sha
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from tests.integration_support.privacy_cases import privacy_actors

TABLES = (
    "organisations",
    "organisation_memberships",
    "candidate_exam_attempts",
    "candidate_responses",
    "candidate_response_revisions",
    "candidate_media_assets",
    "candidate_response_media_bindings",
    "scoring_runs",
    "result_versions",
    "result_releases",
    "privacy_grants",
    "privacy_cases",
    "privacy_scopes",
    "knowledge_document_versions",
    "audit_events",
)


@contextmanager
def session_for(database):
    engine = engine_for(database)
    try:
        with Session(engine, expire_on_commit=False) as session:
            yield session
    finally:
        engine.dispose()


def seed_domains(database, password):
    settings = Settings(
        _env_file=None, environment="development", database_url=database.url
    )
    with session_for(database) as session:
        ids = reconcile_demo(session, settings, enabled=True, password=password)
        org = UUID(ids["organisation_id"])
        subject = UUID(ids["audio_candidate_user_id"])
        admin = session.scalar(
            select(User).where(User.normalized_email == "admin@demo.prufai.local")
        )
        assert admin
        admin_context = build_tenant_context(session, admin.id, org)

        def approved_result(email):
            candidate = session.scalar(
                select(User).where(User.normalized_email == email)
            )
            assert candidate
            context = build_tenant_context(session, candidate.id, org)
            assignment = CandidateRuntimeService.create_assignment(
                session,
                admin_context,
                candidate_user_id=candidate.id,
                exam_version_id=UUID(ids["golden_exam_version_id"]),
            )
            attempt = CandidateRuntimeService.create_attempt(
                session, context, assignment.id
            )
            attempt = CandidateRuntimeService.start(session, context, attempt.id)
            item = attempt.runtime_plan["modules"][0]["sections"][0]["tasks"][0][
                "items"
            ][0]
            CandidateRuntimeService.save_response(
                session,
                context,
                attempt.id,
                UUID(item["binding_id"]),
                payload={"option_id": "a"},
                client_operation_id=uuid4(),
                expected_revision=0,
            )
            CandidateRuntimeService.submit(session, context, attempt.id)
            run = ScoringService.score(session, admin_context, attempt.id)
            version = session.scalar(
                select(ResultVersion).where(ResultVersion.scoring_run_id == run.id)
            )
            assert version and version.status.value == "REVIEW_REQUIRED"
            ScoringService.request_review(session, admin_context, version.id)
            approved = ScoringService.approve_review(session, admin_context, version.id)
            assert approved.status.value == "APPROVED"
            return approved

        approved = approved_result(GOLDEN_CANDIDATE_EMAIL)
        release = ResultReleaseService.release(
            session, admin_context, approved.id, operation_id=str(uuid4())
        )
        pending = approved_result("candidate@demo.prufai.local")
        session.commit()
        privacy_email = "o2a-privacy-reviewer@example.invalid"
        privacy_actors(
            session,
            settings,
            organisation_id=org,
            subject_id=subject,
            reviewer_email=privacy_email,
            password=password,
        )

        def principal(email):
            login = AuthenticationService(settings).login(
                session, email=email, password=password
            )
            context = build_tenant_context(session, login.user.id, org)
            return Principal(login.user, login.session, context)

        operator = principal("admin@demo.prufai.local")
        case = privacy.intake(
            session,
            operator,
            org,
            CaseIntake(
                operation_id=uuid4(),
                subject_id=subject,
                purpose="ASSESSMENT_DELIVERY",
                request_type="ERASURE_REQUEST",
                request_receipt=uuid4(),
                categories=["ORIGINAL_AUDIO"],
            ),
        )
        session.commit()
        reviewer = principal(privacy_email)
        verified = privacy.verify(
            session,
            reviewer,
            org,
            case["id"],
            Verification(
                operation_id=uuid4(),
                case_checksum=case["case_checksum"],
                verification_receipt=uuid4(),
                controller_context="UNASSESSED",
            ),
        )
        assert verified["identity"] == "VERIFIED_TEST_ONLY"
        assert session.scalar(
            select(AuditEvent.id).where(
                AuditEvent.entity_id == str(case["id"]),
                AuditEvent.event_type == "privacy.case_created",
            )
        )
        foreign = Organisation(
            slug="o2a-other-synthetic",
            name="O2A synthetic second organisation",
            organisation_type=OrganisationType.LANGUAGE_SCHOOL,
        )
        email = "o2a-foreign-candidate@example.invalid"
        other = User(email=email, normalized_email=email)
        session.add_all([foreign, other])
        session.flush()
        AuthenticationService(settings).set_password(session, other, password)
        membership = OrganisationMembership(
            organisation_id=foreign.id, user_id=other.id
        )
        session.add(membership)
        session.flush()
        role = session.scalar(
            select(Role).where(Role.key == "EXAMINER", Role.scope == RoleScope.TENANT)
        )
        assert role
        session.add(MembershipRole(membership_id=membership.id, role_id=role.id))
        session.commit()
        return ids, release.id, pending.result_id, foreign.id, email


def snapshot(database, *, audit=True):
    result = {}
    for table in TABLES:
        if table == "audit_events" and not audit:
            continue
        statement = (
            sql.SQL(
                "SELECT count(*)::text || ':' || "
                "coalesce(jsonb_agg(to_jsonb(row) ORDER BY row.id)::text, '[]') "
                "FROM {} AS row"
            )
            .format(sql.Identifier(table))
            .as_string()
        )
        count, data = database.sql(statement).strip().split(b":", 1)
        result[table] = {"rows": int(count), "sha256": sha(data)}
        assert int(count) > 0, table
    return result


def objects(database, storage):
    with session_for(database) as session:
        media = list(session.scalars(select(CandidateMediaAsset)))
        sources = list(
            session.scalars(
                select(KnowledgeDocumentVersion).where(
                    KnowledgeDocumentVersion.storage_key.is_not(None)
                )
            )
        )
        assert len(media) == 2 and len(sources) == 2
        assert all(row.storage_bucket == storage.bucket for row in sources)
        refs = {
            row.storage_key: (row.sha256, row.byte_size, row.content_type)
            for row in media
        } | {
            row.storage_key: (row.sha256, row.file_size, row.mime_type)
            for row in sources
        }
    assert len(refs) == 4 and inventory(storage) == set(refs)
    assert_private(storage)
    client = storage.operator()
    verified = {}
    for key, (checksum, size, mime) in refs.items():
        head = client.head_object(Bucket=storage.bucket, Key=key)
        with client.get_object(Bucket=storage.bucket, Key=key)["Body"] as stream:
            data = stream.read(size + 1)
        assert len(data) == head["ContentLength"] == size
        assert sha(data) == checksum and head["ContentType"] == mime
        verified[key] = (
            data,
            mime,
            {k.lower(): v for k, v in head.get("Metadata", {}).items()},
        )
    return verified


def recover(source_db, source_storage, target_db, target_storage, head):
    assert (
        source_db.container != target_db.container
        and source_db.volume != target_db.volume
    )
    assert source_storage.volume != target_storage.volume
    db_snapshot = snapshot(source_db)
    source_objects = objects(source_db, source_storage)
    with tempfile.TemporaryDirectory(prefix="prufai-o2a-archive-") as folder:
        path = Path(folder)
        dump = source_db.dump()
        assert dump.startswith(b"PGDMP")
        (path / "database.dump").write_bytes(dump)
        manifest = {}
        for index, (key, (data, mime, metadata)) in enumerate(
            sorted(source_objects.items())
        ):
            name = f"object-{index}"
            (path / name).write_bytes(data)
            manifest[key] = {
                "name": name,
                "sha256": sha(data),
                "bytes": len(data),
                "type": mime,
                "metadata": metadata,
            }
        archive_manifest = json.dumps(
            {"database_sha256": sha(dump), "objects": manifest}, sort_keys=True
        ).encode()
        (path / "manifest.json").write_bytes(archive_manifest)
        assert path.stat().st_mode & 0o777 == 0o700
        assert all(file.stat().st_mode & 0o777 == 0o600 for file in path.iterdir())
        read_manifest = (path / "manifest.json").read_bytes()
        assert sha(read_manifest) == sha(archive_manifest)
        indexed = json.loads(read_manifest)
        database_dump = (path / "database.dump").read_bytes()
        assert sha(database_dump) == indexed["database_sha256"]
        assert inventory(target_storage) == set()
        target_db.restore(database_dump)
        for key, row in indexed["objects"].items():
            payload = (path / row["name"]).read_bytes()
            assert len(payload) == row["bytes"] and sha(payload) == row["sha256"]
            target_storage.operator().put_object(
                Bucket=target_storage.bucket,
                Key=key,
                Body=payload,
                ContentType=row["type"],
                Metadata=row["metadata"],
            )
    assert (
        target_db.sql("select version_num from alembic_version").decode().strip()
        == head
    )
    assert snapshot(target_db) == db_snapshot
    assert objects(target_db, target_storage) == source_objects
    for key in source_objects:
        assert (
            request(target_storage.port(), "GET", f"/{target_storage.bucket}/{key}")[0]
            == 403
        )
    with session_for(target_db) as session:
        try:
            with session.begin_nested():
                session.execute(
                    text("UPDATE privacy_cases SET checksum = repeat('0', 64)")
                )
                raise AssertionError("restored privacy case was mutable")
        except DBAPIError as exc:
            assert "PRIVACY_EVIDENCE_IMMUTABLE" in str(exc.orig)
    assert (
        target_db.sql("""
        SELECT count(*) FROM result_releases r
        JOIN result_versions v ON v.id = r.result_version_id
        JOIN scoring_runs s ON s.id = v.scoring_run_id
        WHERE r.scoring_run_id <> s.id OR r.attempt_id <> s.attempt_id
            OR r.organisation_id <> s.organisation_id
            OR v.scoring_run_checksum IS DISTINCT FROM s.checksum
    """).strip()
        == b"0"
    )
    return {
        "database_archive_sha256": sha(dump),
        "manifest_sha256": sha(archive_manifest),
        "tables": db_snapshot,
        "objects": len(source_objects),
        "privacy_immutability_and_release_lineage": "PASS",
        "anonymous_object_reads_denied": len(source_objects),
        "missing_or_orphaned_objects": 0,
        "checksum_and_metadata_mismatches": 0,
        "backup_scope": "plaintext synthetic archive; private temporary files removed after restore",
    }


def verify_restored_standards(database, storage, records, creator):
    from app.authorization import TenantContext

    registry = KnowledgeRegistryService(
        storage=storage.adapter("standards/*", write=False)
    )
    with session_for(database) as session:
        context = TenantContext(creator, None, None, platform_permissions=PERMISSIONS)
        for row in records:
            registry.verify_file(session, context, version_id=UUID(row["version"]))
