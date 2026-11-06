"""LiD legal and source boundaries on migrated PostgreSQL and private MinIO."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.authorization import build_tenant_context
from app.models import (
    AuditEvent,
    CertificationAuthorization,
    ResultVersion,
    RuleVersion,
    ScoringRun,
    StandardPackVersion,
)
from app.models.standard_pack import LidBlueprintProvenance
from app.result_release import service as releases
from tests.test_authoritative_standards_postgresql_minio_integration import DIGEST, ROOT, SYNTHETIC
from tests.test_authoritative_standards_postgresql_minio_integration import (
    configured as _configured,
)
from tests.test_authoritative_standards_postgresql_minio_integration import expert as _expert
from tests.test_authoritative_standards_postgresql_minio_integration import proof as _proof

configured = _configured
expert = _expert
proof = _proof
LID = ROOT + "/lid"
PLATFORM = "/api/v1/platform"


@pytest.fixture(scope="module")
def lid_draft(proof):
    sources = {
        row["key"]: proof.http("POST", LID + "/sources/" + row["key"] + "/register")
        for row in proof.http("GET", LID + "/sources")
    }
    assert set(sources) == {
        "bamf-lid-interactive",
        "bamf-lid-catalogue",
        "inttestv-2026-306",
        "einbtestv-2024-211",
        "intv-2026-218",
    }
    pins = {
        key: {
            "version_id": row["registered"]["id"],
            "metadata_checksum": row["registered"]["metadata_checksum"],
        }
        for key, row in sources.items()
    }
    proof.http("POST", LID + "/blueprints", status=409, json={"sources": pins})
    for key, row in sources.items():
        assert row["content_mode"] == "METADATA_ONLY"
        document = row["registered"]
        assert not document["reviewed"]
        if key == "bamf-lid-interactive":
            proof.rights(document, "METADATA_ONLY", actor="creator", status=403)
        proof.rights(
            document,
            "METADATA_ONLY",
            rights_holder=row["rights_holder"],
            licence_type="Bibliographic reference only",
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(
            lambda _: proof.http("POST", LID + "/blueprints", status=201, json={"sources": pins}),
            range(2),
        )
    assert first == second
    return first, pins


def _preview(proof, draft, count, **changes):
    return proof.http(
        "POST",
        LID + f"/blueprints/{draft['id']}/preview",
        json={
            "expected_pack_checksum": draft["checksum"],
            "as_of": "2026-11-06",
            "raw_correct": count,
            **changes,
        },
    )


def test_lid_draft_pins_catalogue_claims_and_exact_rights(proof, lid_draft):
    row, pins = lid_draft
    assert proof.http("GET", LID + "/blueprints/" + row["id"]) == row
    assert row["assessment_family"] == "INTEGRATION_KNOWLEDGE"
    assert row["exam_family"] == "LEBEN_IN_DEUTSCHLAND" and row["language"] == "de"
    assert row["cefr_level"] is None and row["supported_levels"] == []
    assert row["status"] == "DRAFT" and row["structural_validation"] == "PASS"
    assert row["allowed_classifications"] == ["PRACTICE"] and not row["delivery_ready"]
    assert len({jurisdiction["code"] for jurisdiction in row["jurisdictions"]}) == 16
    assert row["structure"] == {
        "general_items": 30,
        "state_items": 3,
        "total_items": 33,
        "options_per_item": 4,
        "correct_options_per_item": 1,
        "time_limit_seconds": 3600,
    }
    assert {pool["scope"]: pool["eligible_catalogue_count"] for pool in row["question_pools"]} == {
        "GENERAL_DE": 300,
        "BUNDESLAND_SPECIFIC": 10,
    }
    assert row["question_pool_schema"]["selected_state_catalogue_slots"] == 310
    assert row["question_pool_schema"]["nationwide_catalogue_slots"] == 460
    sources = {s["key"]: s for s in row["source_bindings"]}
    for key, source in sources.items():
        assert source["available"] and source["metadata_only"] and source["metadata_use_allowed"]
        assert len(source["rights_decision_checksum"]) == 64
        assert source["rights_review_actor_id"] == str(proof.expert.actors["approver"])
        assert source["document_version_id"] == pins[key]["version_id"]
        assert not any(
            source[key]
            for key in (
                "ingestion_allowed",
                "question_reuse_allowed",
                "image_reuse_allowed",
                "derivative_use_allowed",
                "redistribution_allowed",
            )
        )
        version = proof.http("GET", ROOT + f"/versions/{source['document_version_id']}")
        assert version["sha256"] is None and version["latest_ingestion"] is None
    claims = {claim["code"]: claim for claim in row["claim_ledger"]}
    assert set(claims) >= {
        "LID_CATALOGUE_EDITION",
        "LID_PREPARATION_POOL",
        "LID_FORM_COMPOSITION",
        "LID_ANSWER_STRUCTURE",
        "LID_DURATION",
        "LID_ORIENTATION_THRESHOLD",
        "LID_NATURALIZATION_TEST_THRESHOLD",
        "LID_NATURALIZATION_CERTIFICATE_CONDITIONS",
    }
    for claim in claims.values():
        assert claim["review_status"] == "EXPERT_REVIEW_REQUIRED" and len(claim["checksum"]) == 64
        for citation in claim["claims"]:
            source = sources[citation["source_key"]]
            assert citation["source_version_id"] == source["document_version_id"]
            assert citation["source_metadata_checksum"] == source["source_metadata_checksum"]
            assert citation["reference_url"].startswith("https://")
    legal = claims["LID_ORIENTATION_THRESHOLD"]["claims"][0]
    assert legal["reference_url"].endswith("/inttestv/__10.html")
    assert "2026/306/regelungstext.pdf" in legal["source_url"]
    wrong = {key: dict(pin) for key, pin in pins.items()}
    wrong["inttestv-2026-306"]["metadata_checksum"] = "0" * 64
    proof.http("POST", LID + "/blueprints", status=409, json={"sources": wrong})
    assert len(proof.http("GET", LID + "/blueprints")) == 1


def test_distinct_numerical_boundaries_and_no_live_rule_or_release(proof, lid_draft):
    row, pins = lid_draft
    for count in (0, 14, 15, 16, 17, 32, 33):
        preview = _preview(proof, row, count)
        assert preview["official_outcome"] == "UNDETERMINED"
        assert preview["administrative_recognition"] == "NOT_ESTABLISHED"
        assert not preview["official_result_created"]
        contexts = {item["purpose"]: item for item in preview["contexts"]}
        assert set(contexts) == {"ORIENTATION_COURSE_CONTEXT", "NATURALIZATION_KNOWLEDGE_CONTEXT"}
        for purpose, threshold in (
            ("ORIENTATION_COURSE_CONTEXT", 15),
            ("NATURALIZATION_KNOWLEDGE_CONTEXT", 17),
        ):
            item = contexts[purpose]
            assert item["threshold"] == threshold and item["rule_checksum"]
            assert item["numerical_outcome"] == (
                "THRESHOLD_MET" if count >= threshold else "BELOW_THRESHOLD"
            )
    for result in (
        _preview(proof, row, 33, as_of="2026-10-31"),
        _preview(proof, row, 33, expected_pack_checksum="0" * 64),
    ):
        assert all(item["numerical_outcome"] == "UNDETERMINED" for item in result["contexts"])
    for actor in ("candidate", "revoked"):
        proof.http("GET", LID + "/sources", actor=actor, status=403)
        proof.http("GET", LID + f"/blueprints/{row['id']}", actor=actor, status=403)
        proof.http("POST", LID + "/blueprints", actor=actor, status=403, json={"sources": pins})
    for action in ("request-review", "approve", "publish"):
        denied = proof.http(
            "POST",
            PLATFORM + f"/standard-pack-versions/{row['id']}/{action}",
            status=409,
        )
        assert denied["detail"] == "LID_DRAFT_PUBLICATION_BLOCKED"
    for rule in row["rules"]:
        proof.http("POST", PLATFORM + f"/rule-versions/{rule['id']}/approve", status=(400, 409))
        proof.http(
            "POST",
            PLATFORM + f"/rule-versions/{rule['id']}/dry-run",
            status=(400, 409),
            json={"facts": {}},
        )
    with proof.expert.factory() as session:
        for model in (ScoringRun, ResultVersion):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        for pin in row["rules"]:
            rule = session.get(RuleVersion, uuid.UUID(pin["id"]))
            assert rule.status.value == "DRAFT" and not rule.pack_bindings
            assert rule.approved_by_user_id is None


def test_exact_lid_pack_cannot_receive_certification_even_when_switch_is_enabled(
    proof, lid_draft, monkeypatch
):
    row, _ = lid_draft
    settings = proof.settings.model_copy(update={"certification_issuance_enabled": True})
    monkeypatch.setattr(releases, "get_settings", lambda: settings)
    organisation = uuid.UUID(proof.headers["revoked"]["X-PrufAI-Organisation-ID"])
    with proof.expert.factory() as session:
        context = build_tenant_context(session, proof.expert.actors["creator"], organisation)
        assert "certification.authorize" in context.platform_permissions
        pack = session.get(StandardPackVersion, uuid.UUID(row["id"]))
        assert pack is not None
        for scope in ("OFFICIAL_CERTIFICATION", "PRACTICE"):
            with pytest.raises(releases.ResultReleaseError, match="CERTIFICATION.NOT_AUTHORIZED"):
                releases.ResultReleaseService.authorize(
                    session,
                    context,
                    assessment_scope=scope,
                    provider_scope=pack.standard_pack.code,
                    standard_pack_version_id=pack.id,
                    operation_id=f"lid-{scope}-blocked",
                    evidence_reference="Synthetic negative authorization test",
                    effective_from=datetime.now(timezone.utc),
                )
        assert session.scalar(select(func.count()).select_from(CertificationAuthorization)) == 0


def test_legal_metadata_rejects_upload_real_minio_retains_only_original_evidence(proof, lid_draft):
    row, _ = lid_draft
    source = next(s for s in row["source_bindings"] if s["key"] == "bamf-lid-catalogue")
    pinned = {
        "id": source["document_version_id"],
        "metadata_checksum": source["source_metadata_checksum"],
    }
    proof.upload(pinned, status=409)
    assert proof.search(pinned)["results"] == []
    version, _ = proof.synthetic_version()
    proof.rights(version, "ALLOW_SYNTHETIC_INGESTION", digest=DIGEST)
    proof.upload(version)
    assert proof.storage.get(proof.object_key(version)) == SYNTHETIC
    assert proof.ingest(version)["status"] == "SUCCEEDED"
    proof.approve(version)
    assert proof.index(version)["execution_class"] == "TEST_ONLY"
    citation = proof.search(version, expected_document_checksum=DIGEST)["results"][0]["citation"]
    assert citation["document_version_id"] == version["id"] and citation["checksum"] == DIGEST
    proof.search(version, actor="revoked", status=403)
    proof.search(version, actor="candidate", status=403)


def test_historical_seal_and_stale_legal_source_fail_closed(proof, lid_draft):
    row, pins = lid_draft
    with proof.expert.factory() as session:
        for statement, identifier in (
            (
                "UPDATE standard_pack_lid_provenance SET manifest = '{}' "
                "WHERE standard_pack_version_id = :id",
                row["id"],
            ),
            ("UPDATE standard_pack_versions SET status = 'ACTIVE' WHERE id = :id", row["id"]),
            ("UPDATE rule_versions SET status = 'APPROVED' WHERE id = :id", row["rules"][0]["id"]),
        ):
            with (
                pytest.raises(DBAPIError, match="LID_PRACTICE_HISTORY_IMMUTABLE"),
                session.begin_nested(),
            ):
                session.execute(text(statement), {"id": uuid.UUID(identifier)})
        assert session.scalar(select(func.count()).select_from(LidBlueprintProvenance)) == 1
        events = list(session.scalars(select(AuditEvent).where(AuditEvent.entity_id == row["id"])))
        assert {event.event_type for event in events} >= {
            "standards.lid_draft.created",
            "standards.lid_draft.previewed",
            "standards.lid_draft.publication_denied",
        }
        assert all(
            set(event.metadata_json)
            <= {
                "pack_checksum",
                "manifest_checksum",
                "reason",
                "official_outcome",
                "target",
            }
            for event in events
        )
        with (
            pytest.raises(DBAPIError, match="LID_PRACTICE_HISTORY_IMMUTABLE"),
            session.begin_nested(),
        ):
            session.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": events[0].id})
    pin = pins["inttestv-2026-306"]
    document = proof.http("GET", ROOT + f"/versions/{pin['version_id']}")
    successor = proof.http(
        "POST",
        ROOT + f"/sources/{document['source_id']}/versions",
        status=201,
        json={
            "content_mode": "METADATA_ONLY",
            "version_label": "S3 technical successor fixture",
            "supersedes_document_version_id": pin["version_id"],
            "source_url": document["source_url"],
        },
    )
    assert _preview(proof, row, 17)["contexts"][1]["numerical_outcome"] == "THRESHOLD_MET"
    proof.rights(successor, "METADATA_ONLY")
    assert all(
        ctx["numerical_outcome"] == "UNDETERMINED" for ctx in _preview(proof, row, 17)["contexts"]
    )
    alternate = pins["bamf-lid-interactive"]
    proof.rights({"id": alternate["version_id"], **alternate}, "WITHDRAW")
    historical = proof.http("GET", LID + "/blueprints/" + row["id"])
    assert (
        historical["checksum"] == row["checksum"]
        and historical["claim_ledger"] == row["claim_ledger"]
    )
    assert historical["structural_validation"] == "REVIEW_REQUIRED"
    proof.http("POST", LID + "/blueprints", status=409, json={"sources": pins})
