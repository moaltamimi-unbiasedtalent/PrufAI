"""Synthetic offline authority recovery on an isolated Alembic PostgreSQL database."""

import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from app.authorization import AuthorizationError, TenantContext
from app.initial_review_authority import runtime
from app.initial_review_authority.recovery import execute
from app.initial_review_authority.trust import Envelope, RecoveryError, Target, TrustRoot
from app.models import (
    AuditEvent,
    InitialReviewAuthority,
    KnowledgeLegalMetadata,
    PlatformRoleAssignment,
    ScopedSourceReviewGrant,
)
from app.models.enums import LegalReviewStatus, PlatformAssignmentStatus
from app.scripts import recover_initial_review_authority as cli
from app.services.organisations import OrganisationService
from app.writing_assessment.service import canonical_checksum
from tests.test_scoped_source_review_postgresql_integration import (
    CONTROL,
    REGISTRY,
    SCOPED,
    _grant_payload,
)
from tests.test_scoped_source_review_postgresql_integration import (
    scenario as scoped_scenario,
)

scenario = scoped_scenario

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL")
    and os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") != "1",
    reason="Initial review authority requires real Alembic PostgreSQL",
)


@pytest.fixture(scope="module")
def plan(scenario):
    # Test-only keys are generated only for this test database.
    keys = [Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()]
    with scenario.factory() as session:
        name, port, cluster, oid = session.execute(
            text("""
            SELECT current_database(), inet_server_port(),
                   (SELECT system_identifier::text FROM pg_control_system()),
                   (SELECT oid::int8 FROM pg_database WHERE datname = current_database())
            """)
        ).one()
    target = Target(
        environment="DISPOSABLE_TEST",
        database=name,
        port=port,
        cluster_id=cluster,
        database_oid=oid,
    )
    trust = TrustRoot(
        target=target,
        organisation_id=uuid.UUID(scenario.organisation_id),
        approver_id=scenario.user_ids["issuer"],
        approver_public_key=keys[0].public_key().public_bytes_raw().hex(),
        executor_id=scenario.user_ids["other_issuer"],
        executor_public_key=keys[1].public_key().public_bytes_raw().hex(),
        executor_uid=1000,
    )
    versions = [scenario.version() for _ in range(5)]
    now = datetime.now(timezone.utc)
    grant = _grant_payload(
        scenario.user_ids["reviewer"],
        versions,
        scenario.organisation_id,
        scenario.user_ids["other_reviewer"],
        starts_at=(now - timedelta(seconds=15)).isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
    )
    manifest = {
        "version": 1,
        "action": "ESTABLISH",
        "target": target.model_dump(),
        "organisation_id": scenario.organisation_id,
        "approver_id": str(trust.approver_id),
        "executor_id": str(trust.executor_id),
        "beneficiary_id": str(scenario.user_ids["other_reviewer"]),
        "beneficiary_identity_evidence": "1" * 64,
        "approval_evidence": "2" * 64,
        "revocation_responsibility": str(trust.approver_id),
        "reason": "INITIAL_SCOPED_REVIEW_AUTHORITY",
        "nonce": str(uuid.uuid4()),
        "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(hours=2)).isoformat(),
        "grant": grant,
    }

    def signed(document):
        digest = canonical_checksum(document)
        message = b"PrufAI initial review authority v1\x00" + bytes.fromhex(digest)
        return Envelope(
            manifest=document,
            manifest_digest=digest,
            approver_signature=keys[0].sign(b"APPROVER\x00" + message).hex(),
            executor_signature=keys[1].sign(b"EXECUTOR\x00" + message).hex(),
        )

    return trust, manifest, signed, versions


def _run(scenario, trust, envelope, *, dry_run=False):
    with scenario.factory() as session:
        return execute(session, trust, envelope, dry_run=dry_run)


def _without_admins(scenario):
    with scenario.factory() as session:
        session.execute(
            update(PlatformRoleAssignment)
            .where(
                PlatformRoleAssignment.user_id.in_(
                    [scenario.user_ids["issuer"], scenario.user_ids["other_issuer"]]
                )
            )
            .values(status=PlatformAssignmentStatus.SUSPENDED)
        )
        session.commit()


def test_independent_proof_fail_closed_and_existing_admin_still_works(scenario, plan):
    trust, manifest, signed, versions = plan
    envelope = signed(manifest)
    path = f"{SCOPED}/orgs/{scenario.organisation_id}/grants"
    scenario.request("POST", path, actor="other_reviewer", status=403, payload=manifest["grant"])
    for bad, reason in (
        (
            envelope.model_copy(update={"approver_signature": "0" * 128}),
            "INVALID_MANIFEST_OR_SIGNATURE",
        ),
        (
            envelope.model_copy(update={"executor_signature": envelope.approver_signature}),
            "INVALID_MANIFEST_OR_SIGNATURE",
        ),
        (
            envelope.model_copy(update={"manifest": {**manifest, "reason": "ALTERED"}}),
            "MANIFEST_DIGEST_MISMATCH",
        ),
        (
            signed({**manifest, "organisation_id": scenario.other_organisation_id}),
            "INVALID_MANIFEST_OR_SIGNATURE|UNTRUSTED_OR_REVOKED_APPROVAL",
        ),
        (
            signed({**manifest, "beneficiary_id": manifest["executor_id"]}),
            "INVALID_MANIFEST_OR_SIGNATURE",
        ),
        (
            signed({**manifest, "target": {**manifest["target"], "cluster_id": "1"}}),
            "UNTRUSTED_OR_REVOKED_APPROVAL",
        ),
    ):
        with pytest.raises(RecoveryError, match=reason):
            _run(scenario, trust, bad)
    root_for_another_db = trust.model_copy(
        update={
            "target": trust.target.model_copy(
                update={"database_oid": trust.target.database_oid + 1}
            )
        }
    )
    with pytest.raises(RecoveryError, match="DATABASE_IDENTITY_MISMATCH"):
        _run(scenario, root_for_another_db, envelope)
    with pytest.raises(RecoveryError, match="UNTRUSTED_OR_REVOKED_APPROVAL"):
        _run(
            scenario,
            trust.model_copy(update={"revoked_nonces": {uuid.UUID(manifest["nonce"])}}),
            envelope,
        )
    with pytest.raises(RecoveryError, match="EXISTING_PLATFORM_ADMINISTRATIVE_PATH"):
        _run(scenario, trust, envelope)
    scenario.grant(versions[:1], actor="issuer")
    with scenario.factory() as session:
        assert session.get(InitialReviewAuthority, 1) is None


def test_offline_recovery_exact_issuance_and_revocation(
    scenario, plan, monkeypatch, tmp_path, capsys
):
    trust, manifest, signed, versions = plan
    envelope = signed(manifest)
    _without_admins(scenario)
    now = datetime.now(timezone.utc)
    expired_grant = _grant_payload(
        scenario.user_ids["reviewer"],
        versions,
        scenario.organisation_id,
        scenario.user_ids["other_reviewer"],
        starts_at=(now - timedelta(hours=2)).isoformat(),
        expires_at=(now - timedelta(hours=1)).isoformat(),
    )
    expired = signed(
        {
            **manifest,
            "nonce": str(uuid.uuid4()),
            "starts_at": (now - timedelta(hours=3)).isoformat(),
            "expires_at": (now - timedelta(minutes=10)).isoformat(),
            "grant": expired_grant,
        }
    )
    with pytest.raises(RecoveryError, match="APPROVAL_OUTSIDE_VALIDITY_WINDOW"):
        _run(scenario, trust, expired)
    cli_trust = trust.model_copy(update={"executor_uid": os.geteuid()})
    packet = tmp_path / "approved-test-only-envelope.json"
    packet.write_text(envelope.model_dump_json())
    packet.chmod(0o600)
    with scenario.factory() as session:
        disposable_url = session.get_bind().engine.url.render_as_string(hide_password=False)
    with monkeypatch.context() as local:
        local.setattr(cli, "load_trust", lambda: cli_trust)
        local.setattr(cli, "getpass", lambda _: disposable_url)
        local.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
        args = [
            "recover_initial_review_authority",
            "--envelope",
            str(packet),
            "--database",
            trust.target.database,
            "--confirm-sha256",
            envelope.manifest_digest,
            "--dry-run",
        ]
        local.setattr(sys, "argv", args)
        cli.main()
        assert "DRY_RUN ESTABLISHED" in capsys.readouterr().out
        local.setattr(sys, "argv", [*args[:-2], "0" * 64, "--apply"])
        with pytest.raises(SystemExit, match="OPERATOR_CONFIRMATION_MISMATCH"):
            cli.main()
    with scenario.factory() as session:
        assert session.get(InitialReviewAuthority, 1) is None

    barrier = Barrier(2)

    def consume():
        barrier.wait(timeout=5)
        return _run(scenario, trust, envelope)

    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(consume), pool.submit(consume)]
        assert sorted(call.result(timeout=15) for call in calls) == [
            "ALREADY_ESTABLISHED",
            "ESTABLISHED",
        ]
    with pytest.raises(RecoveryError, match="INITIAL_AUTHORITY_ALREADY_CONSUMED"):
        _run(scenario, trust, signed({**manifest, "nonce": str(uuid.uuid4())}))

    beneficiary = "other_reviewer"
    request = manifest["grant"]
    path = f"{SCOPED}/orgs/{scenario.organisation_id}/grants"
    scenario.request("POST", path, actor=beneficiary, status=403, payload=request)
    monkeypatch.setattr(runtime, "load_trust", lambda: trust)
    for actor, org, payload in (
        ("reviewer", scenario.organisation_id, request),
        (beneficiary, scenario.other_organisation_id, request),
        (beneficiary, scenario.organisation_id, {**request, "operation_id": str(uuid.uuid4())}),
        (beneficiary, scenario.organisation_id, {**request, "expires_at": manifest["expires_at"]}),
        (
            beneficiary,
            scenario.organisation_id,
            {**request, "resources": request["resources"][:-1]},
        ),
    ):
        scenario.request(
            "POST", f"{SCOPED}/orgs/{org}/grants", actor=actor, status=403, payload=payload
        )
    with monkeypatch.context() as local:
        local.setattr(runtime, "get_settings", lambda: SimpleNamespace(environment="production"))
        scenario.request("POST", path, actor=beneficiary, status=403, payload=request)
    monkeypatch.setattr(runtime, "utcnow", lambda: datetime.fromisoformat(manifest["expires_at"]))
    scenario.request("POST", path, actor=beneficiary, status=403, payload=request)
    monkeypatch.setattr(runtime, "utcnow", lambda: datetime.now(timezone.utc))
    grant = scenario.request("POST", path, actor=beneficiary, payload=request)
    assert scenario.request("POST", path, actor=beneficiary, payload=request) == grant
    assert len(scenario.request("GET", f"{path}/{grant['grant_id']}/sources")["sources"]) == 5

    version_path = f"{REGISTRY}/versions/{versions[0]['id']}"
    for method, route in (
        ("GET", f"{REGISTRY}/sources"),
        ("GET", f"{CONTROL}/overview"),
        ("GET", f"{CONTROL}/certification"),
        ("GET", version_path),
        ("GET", f"/api/v1/organisations/{scenario.other_organisation_id}/exam-assignments"),
        ("GET", f"/api/v1/candidate/audio-assets/{uuid.uuid4()}/stream"),
        ("POST", f"/api/v1/platform/standard-pack-versions/{uuid.uuid4()}/publish"),
        ("POST", f"{version_path}/ingest"),
        ("POST", f"{version_path}/rights-review"),
        ("POST", f"{version_path}/approve"),
        ("POST", f"{version_path}/reject"),
    ):
        # Request validation may reject missing write bodies even earlier than RBAC.
        scenario.request(method, route, actor=beneficiary, status=(403, 422))
    with scenario.factory() as session:
        with pytest.raises(AuthorizationError):
            OrganisationService.assign_platform_role(
                session,
                TenantContext(scenario.user_ids[beneficiary], None, None),
                user_id=scenario.user_ids[beneficiary],
                role_key="PRUFAI_SUPER_ADMIN",
            )

    revocation = signed(
        {
            **manifest,
            "action": "SUSPEND",
            "reason": "END_SCOPED_REVIEW_AUTHORITY",
            "nonce": str(uuid.uuid4()),
            "grant": None,
            "original_manifest_digest": envelope.manifest_digest,
        }
    )
    assert (
        _run(scenario, trust, signed({**revocation.manifest, "action": "REVOKE"}), dry_run=True)
        == "ENDED"
    )
    scenario.request("GET", f"{path}/{grant['grant_id']}/sources")
    assert _run(scenario, trust, revocation) == "ENDED"
    assert _run(scenario, trust, revocation) == "ALREADY_ENDED"
    assert _run(scenario, trust, envelope) == "ALREADY_ENDED"
    scenario.request("POST", path, actor=beneficiary, status=403, payload=request)
    scenario.request("GET", f"{path}/{grant['grant_id']}/sources", status=403)
    with scenario.factory() as session:
        assert session.get(InitialReviewAuthority, 1).end_action == "SUSPEND"
        assert (
            session.get(ScopedSourceReviewGrant, uuid.UUID(grant["grant_id"])).status == "REVOKED"
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeLegalMetadata)
                .where(KnowledgeLegalMetadata.legal_review_status != LegalReviewStatus.NOT_REVIEWED)
            )
            == 0
        )
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.event_type.like("standards.initial_review.%"))
        ).all()
        assert [item.event_type for item in events].count("standards.initial_review.consumed") == 1
        assert any(item.actor_id == trust.executor_id for item in events)
        for statement in (
            "DELETE FROM initial_review_authority",
            "UPDATE initial_review_authority SET ended_at = NULL",
            "DELETE FROM audit_events WHERE event_type = 'standards.initial_review.established'",
        ):
            with pytest.raises(DBAPIError, match="INITIAL_REVIEW_AUTHORITY_IMMUTABLE"):
                with session.begin_nested():
                    session.execute(text(statement))
