"""Required S4 proof on Alembic PostgreSQL and real private MinIO."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.authorization import build_tenant_context
from app.lid_editorial.collections import validate_question
from app.main import app
from app.models import (
    AuditEvent,
    CertificateRecord,
    ExamVersion,
    LidEditorialCollection,
    LidEditorialPublication,
    LidEditorialReviewReceipt,
    MembershipRole,
    OrganisationMembership,
    PlatformRoleAssignment,
    QuestionBankItemVersion,
    Role,
    StandardPackVersion,
    User,
)
from app.models.enums import RoleScope
from app.question_bank.service import QuestionBankService
from tests.test_authoritative_standards_postgresql_minio_integration import DIGEST, ROOT, SYNTHETIC
from tests.test_authoritative_standards_postgresql_minio_integration import (
    configured as _configured,
)
from tests.test_authoritative_standards_postgresql_minio_integration import expert as _expert
from tests.test_authoritative_standards_postgresql_minio_integration import proof as _proof
from tests.test_expert_references_postgresql_integration import PASSWORD
from tests.test_lid_governance_postgresql_minio_integration import lid_draft as _lid_draft

configured = _configured
expert = _expert
proof = _proof
lid_draft = _lid_draft
EDITORIAL = ROOT + "/lid/editorial/collections"
QUALIFICATIONS = "/api/v1/platform/evaluation/examiner-qualifications"
REVIEWERS = {"FACTUAL": "examiner-a", "ASSESSMENT": "examiner-b", "RIGHTS": "adjudicator"}


@pytest.fixture(scope="module")
def editorial(proof, lid_draft):
    draft, _ = lid_draft
    org = proof.headers["revoked"]["X-PrufAI-Organisation-ID"]
    actors = ("creator", "approver", *REVIEWERS.values())
    with proof.expert.factory() as session:
        admin = session.scalar(
            select(Role).where(Role.key == "PRUFAI_SUPER_ADMIN", Role.scope == RoleScope.PLATFORM)
        )
        tenant = session.scalar(
            select(Role).where(Role.key == "INSTITUTION_ADMIN", Role.scope == RoleScope.TENANT)
        )
        assert admin is not None and tenant is not None
        for actor in actors:
            user = proof.expert.actors[actor]
            membership = OrganisationMembership(organisation_id=uuid.UUID(org), user_id=user)
            session.add(membership)
            session.flush()
            session.add(MembershipRole(membership_id=membership.id, role_id=tenant.id))
            if actor not in {"creator", "approver"}:
                session.add(PlatformRoleAssignment(user_id=user, role_id=admin.id))
        session.commit()
        users = {
            actor: session.get(User, proof.expert.actors[actor]).email
            for actor in REVIEWERS.values()
        }
    with TestClient(app) as client:
        for actor, email in users.items():
            login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
            assert login.status_code == 200, login.text
            proof.headers[actor] = {"Authorization": "Bearer " + login.json()["access_token"]}
    with pytest.MonkeyPatch.context() as patch:
        # The inherited proof fixture migrated and verified this fresh child database.
        database = urlparse(proof.expert.database_url).path.removeprefix("/")
        patch.setenv("PRUFAI_DISPOSABLE_DATABASE", database)
        payload = {
            "organisation_id": org,
            "blueprint_version_id": draft["id"],
            "expected_blueprint_checksum": draft["checksum"],
            "operation_id": str(uuid.uuid4()),
        }
        created = proof.http("POST", EDITORIAL, status=201, json=payload)
        assert proof.http("POST", EDITORIAL, status=201, json=payload) == created
        proof.http(
            "POST", EDITORIAL, status=409, json={**payload, "expected_blueprint_checksum": "a" * 64}
        )
        yield created


def _path(collection):
    return f"{EDITORIAL}/{collection['id']}"


def _item(collection, item):
    return _path(collection) + "/items/" + item["item_version_id"]


def _preview(proof, collection, *, status=200, state="DE-BE", reviewed_only=False):
    return proof.http(
        "POST",
        _path(collection) + "/preview",
        status=status,
        json={
            "expected_collection_checksum": collection["checksum"],
            "bundesland": state,
            "reviewed_only": reviewed_only,
        },
    )


def _submit(proof, collection, item, *, operation=None, checksum=None, status=200):
    return proof.http(
        "POST",
        _item(collection, item) + "/review/submit",
        status=status,
        json={
            "expected_checksum": checksum or item["checksum"],
            "operation_id": operation or str(uuid.uuid4()),
        },
    )


def _qualify(proof, collection, actor, stage):
    row = proof.http(
        "POST",
        QUALIFICATIONS,
        status=201,
        json={
            "operation_id": str(uuid.uuid4()),
            "examiner_id": str(proof.expert.actors[actor]),
            "organisation_id": collection["organisation_id"],
            "kind": f"LID_{stage}",
            "language": "de",
            "standard_pack_version_id": collection["pack"]["id"],
            "standard_pack_checksum": collection["pack"]["checksum"],
            "origin": "SYNTHETIC_TEST",
            "credential_reference": f"synthetic:s4:{stage.lower()}",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )
    proof.http(
        "POST",
        QUALIFICATIONS + f"/{row['id']}/approve",
        actor="approver",
        json={
            "expected_checksum": row["checksum"],
            "review_reference": "synthetic:s4:independent-credential-check",
        },
    )
    return row


def _decision(item, qualification, stage="FACTUAL", *, decision="APPROVE", **extra):
    return {
        "expected_checksum": item["checksum"],
        "operation_id": str(uuid.uuid4()),
        "stage": stage,
        "decision": decision,
        "qualification_id": qualification["id"],
        "qualification_checksum": qualification["checksum"],
        "reason": "SYNTHETIC_TEST only; private reason for the acceptance fixture.",
        **extra,
    }


def _review_source(proof, source):
    proof.rights(
        {"id": source["version_id"], **source},
        "METADATA_ONLY",
        rights_holder=source["rights_holder"],
        licence_type="Bibliographic reference only",
    )


def test_persisted_original_content_private_preview_and_publication_denials(proof, editorial):
    row = proof.http("GET", _path(editorial))
    assert row["counts"]["drafted"] == 33 and row["counts"]["approved"] == 0
    assert row["counts"]["published"] == 0 and row["pack"]["status"] == "DRAFT"
    assert row["cefr_level"] is None and not row["official_certification"]
    assert all(item["authorship"] == "AI_ASSISTED_DRAFT" for item in row["items"])
    assert not any("correct_option_id" in item for item in row["items"])
    view = _preview(proof, row)
    assert view["mode"] == "CONTENT_EDITORIAL_PREVIEW" and view["duration_seconds"] == 3600
    assert not view["official_certification"] and len(view["checksum"]) == 64
    assert {item["item_version_id"] for item in view["items"]} == {
        item["item_version_id"] for item in row["items"]
    }
    assert [item["jurisdiction"] for item in view["items"]] == ["DE"] * 30 + ["DE-BE"] * 3
    for item in view["items"]:
        assert len(item["options"]) == 4 and item["correct_option_id"]
        assert item["claim"]["source_key"] == item["source"]["source_key"]
        assert len(item["source"]["metadata_checksum"]) == len(item["key_checksum"]) == 64
        assert item["claim"]["reference_url"].startswith("https://")
    assert _preview(proof, row, state="DE-BY", status=409)["detail"] == "CONTENT_UNAVAILABLE"
    assert (
        _preview(proof, row, reviewed_only=True, status=409)["detail"] == "PRACTICE_FORM_NOT_READY"
    )
    path = _item(row, row["items"][0])
    proof.http("GET", path, actor="candidate", status=403)
    proof.http(
        "POST",
        _path(row) + "/preview",
        actor="candidate",
        status=403,
        json={"expected_collection_checksum": row["checksum"], "bundesland": "DE-BE"},
    )
    proof.http("GET", EDITORIAL + f"?organisation_id={uuid.uuid4()}", status=403)
    bank_path = "/api/v1/platform/question-bank/item-versions/" + row["items"][0]["item_version_id"]
    for action in ("approve", "publish"):
        assert proof.http("POST", bank_path + "/" + action, status=400)["detail"] == (
            "LID_EDITORIAL_ITEM_REQUIRES_GOVERNED_PATH"
        )
    for action in ("approve", "publish"):
        denied = proof.http(
            "POST",
            _path(row) + "/" + action,
            actor="approver",
            status=409,
            json={
                "expected_collection_checksum": row["checksum"],
                "operation_id": str(uuid.uuid4()),
            },
        )
        assert denied["detail"] == "PRACTICE_FORM_NOT_READY"
    proof.headers["certifier"] = {
        **proof.headers["approver"],
        "X-PrufAI-Organisation-ID": row["organisation_id"],
    }
    auth = proof.http(
        "POST",
        f"/api/v1/organisations/{row['organisation_id']}/certification-authorizations",
        actor="certifier",
        status=409,
        json={
            "assessment_scope": "OFFICIAL_CERTIFICATION",
            "provider_scope": row["pack"]["code"],
            "standard_pack_version_id": row["pack"]["id"],
            "operation_id": str(uuid.uuid4()),
            "evidence_reference": "Synthetic acceptance request; no provider authority.",
            "effective_from": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert auth["detail"] == "CERTIFICATION.NOT_AUTHORIZED"
    generic = "/api/v1/platform/standard-pack-versions/" + row["pack"]["id"] + "/publish"
    proof.http("POST", generic, actor="approver", status=409)
    with proof.expert.factory() as session:
        pack = session.get(StandardPackVersion, uuid.UUID(row["pack"]["id"]))
        general, berlin = [
            session.get(QuestionBankItemVersion, uuid.UUID(row["items"][i]["item_version_id"]))
            for i in (0, -1)
        ]
        tasks = {
            task.code: task.id
            for module in pack.modules
            for section in module.sections
            for task in section.tasks
        }
        context = build_tenant_context(
            session, proof.expert.actors["creator"], uuid.UUID(row["organisation_id"])
        )
        for item, wrong_task in ((general, "BUNDESLAND_SPECIFIC"), (berlin, "GENERAL_DE")):
            with pytest.raises(ValueError, match="LID_EDITORIAL_EXACT_ITEM_BINDING_REQUIRED"):
                QuestionBankService.bind_pack(session, context, item, pack.id, tasks[wrong_task])
        with pytest.raises(ValueError, match="LID_EDITORIAL_INVALID_ANSWER_OPTIONS"):
            validate_question(
                {
                    **general.content,
                    "correct_option_ids": [
                        option["option_id"] for option in general.content["options"][:2]
                    ],
                }
            )
        assert session.scalar(select(func.count()).select_from(LidEditorialCollection)) == 1
        assert session.scalar(select(func.count()).select_from(LidEditorialPublication)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExamVersion)
                .where(
                    ExamVersion.standard_pack_version_id == uuid.UUID(row["pack"]["id"]),
                )
            )
            == 0
        )
        assert session.scalar(select(func.count()).select_from(CertificateRecord)) == 0


def test_independent_rejection_idempotency_and_successor_preserve_history(proof, editorial):
    item = editorial["items"][0]
    path = _item(editorial, item)
    _submit(proof, editorial, item, checksum="f" * 64, status=409)
    operation = str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(
            lambda _: _submit(proof, editorial, item, operation=operation), range(2)
        )
    assert first == second and first["item_checksum"] == item["checksum"]
    qualification = _qualify(proof, editorial, "examiner-a", "FACTUAL")
    decision = _decision(item, qualification, decision="REJECT")
    proof.http("POST", path + "/review/decision", status=403, json=decision)
    proof.http("POST", path + "/review/decision", actor="approver", status=403, json=decision)
    receipt = proof.http("POST", path + "/review/decision", actor="examiner-a", json=decision)
    assert receipt["evidence_class"] == "SYNTHETIC_TEST" and receipt["decision"] == "REJECT"
    assert "reason" not in receipt and "rights" not in receipt
    assert (
        proof.http("POST", path + "/review/decision", actor="examiner-a", json=decision) == receipt
    )
    proof.http(
        "POST",
        path + "/review/decision",
        actor="examiner-a",
        status=409,
        json={**decision, "reason": "Changed replay must not overwrite the decision."},
    )
    old = proof.http("GET", path)
    payload = {
        "expected_checksum": old["provenance_checksum"],
        "operation_id": str(uuid.uuid4()),
        "prompt": old["prompt"],
        "options": [opt["display_text"] for opt in old["options"]],
        "correct_index": [opt["option_id"] for opt in old["options"]].index(
            old["correct_option_id"]
        ),
        "rationale": old["rationale"] + " Überarbeiteter Entwurf für den isolierten Workflowtest.",
    }
    proof.http("POST", path + "/successor", status=409, json={**payload, "options": ["gleich"] * 4})
    successor = proof.http("POST", path + "/successor", status=201, json=payload)
    assert (
        successor["item_id"] == old["item_id"]
        and successor["item_version_id"] != old["item_version_id"]
    )
    assert successor["supersedes_version_id"] == old["item_version_id"]
    assert len(successor["history"]) == 2 and successor["maturity"] == "AI_ASSISTED_DRAFT"
    assert proof.http("GET", path)["review_receipts"][-1] == receipt
    assert (
        proof.http("POST", path + "/successor", status=201, json=payload)["item_version_id"]
        == successor["item_version_id"]
    )
    _submit(proof, editorial, old, status=409)
    _preview(proof, editorial, status=409)  # old exact collection checksum is stale
    with proof.expert.factory() as session:
        for query, identifier in (
            (
                "UPDATE question_bank_item_versions SET content = '{}' WHERE id = :id",
                old["item_version_id"],
            ),
            ("DELETE FROM lid_editorial_review_receipts WHERE id = :id", receipt["id"]),
        ):
            with (
                pytest.raises(DBAPIError, match="LID_EDITORIAL_HISTORY_IMMUTABLE"),
                session.begin_nested(),
            ):
                session.execute(text(query), {"id": uuid.UUID(identifier)})


def test_reviewed_only_30_plus_three_is_test_only_and_never_publishable(proof, editorial):
    row = proof.http("GET", _path(editorial))
    view = _preview(proof, row)
    seen_sources = set()
    for item in view["items"]:
        source = item["source"]
        if source["version_id"] not in seen_sources:
            _review_source(proof, source)
            seen_sources.add(source["version_id"])
    qualifications = {
        stage: _qualify(proof, row, actor, stage) for stage, actor in REVIEWERS.items()
    }
    rights = {
        "basis_reference": "synthetic:s4:review-logic-only",
        "rights_holder_status": "NO_EXCLUSIVE_RIGHTS_ASSERTED",
        "practice_use_allowed": True,
        "display_allowed": True,
        "redistribution_allowed": False,
        "derivative_use_allowed": False,
    }
    for item in row["items"]:
        _submit(proof, row, item)
        for stage, actor in REVIEWERS.items():
            receipt = proof.http(
                "POST",
                _item(row, item) + "/review/decision",
                actor=actor,
                json=_decision(
                    item,
                    qualifications[stage],
                    stage,
                    **({"rights": rights} if stage == "RIGHTS" else {}),
                ),
            )
            assert receipt["evidence_class"] == "SYNTHETIC_TEST"
    reviewed = _preview(proof, row, reviewed_only=True)
    assert len(reviewed["items"]) == 33 and reviewed["reviewed_only"] is True
    result = proof.http("GET", _path(row))
    assert result["counts"]["test_review_decisions"] == 99
    assert result["counts"]["approved"] == result["counts"]["published"] == 0
    assert (
        result["counts"]["general_pool_eligible"] == result["counts"]["berlin_pool_eligible"] == 0
    )
    for action in ("approve", "publish"):
        proof.http(
            "POST",
            _path(row) + "/" + action,
            actor="approver",
            status=409,
            json={
                "expected_collection_checksum": row["checksum"],
                "operation_id": str(uuid.uuid4()),
            },
        )
    with proof.expert.factory() as session:
        audit = list(
            session.scalars(select(AuditEvent).where(AuditEvent.event_type.like("lid.editorial.%")))
        )
        assert audit and all(
            "reason" not in event.metadata_json and "rights" not in event.metadata_json
            for event in audit
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(LidEditorialReviewReceipt)
                .where(
                    LidEditorialReviewReceipt.evidence_class == "VERIFIED_CREDENTIAL",
                )
            )
            == 0
        )


def test_source_withdrawal_revokes_reviewed_pool_and_private_minio_stays_rights_gated(
    proof, editorial
):
    row = proof.http("GET", _path(editorial))
    item = proof.http("GET", _item(row, row["items"][0]))
    source = item["source"]
    pin = {"id": source["version_id"], **source}
    proof.upload(pin, status=409)
    proof.rights(pin, "WITHDRAW")
    assert (
        _preview(proof, row, reviewed_only=True, status=409)["detail"] == "PRACTICE_FORM_NOT_READY"
    )
    old = proof.http("GET", _item(row, row["items"][0]))
    assert old["key_checksum"] == item["key_checksum"] and old["checksum"] == item["checksum"]
    now = datetime.now(timezone.utc)
    edition = proof.http(
        "POST",
        ROOT + f"/sources/{source['source_id']}/versions",
        status=201,
        json={
            "version_label": "Synthetic later bibliography edition for S4 workflow",
            "content_mode": "METADATA_ONLY",
            "source_url": source["edition_url"],
            "supersedes_document_version_id": source["version_id"],
            "publication_date": now.isoformat(),
            "retrieved_at": now.isoformat(),
            "rights_holder": source["rights_holder"],
            "licence_type": "Bibliographic reference only",
        },
    )
    today = now.astimezone(ZoneInfo("Europe/Berlin")).date()
    proposed = {
        "version_id": edition["id"],
        "metadata_checksum": edition["metadata_checksum"],
        "reference_url": item["claim"]["reference_url"],
        "locator": item["claim"]["locator"],
        "fact": item["claim"]["fact"],
        "last_verified": today.isoformat(),
        "recheck_after": (today + timedelta(days=60)).isoformat(),
    }
    payload = {
        "expected_checksum": item["provenance_checksum"],
        "operation_id": str(uuid.uuid4()),
        "prompt": item["prompt"],
        "options": [opt["display_text"] for opt in item["options"]],
        "correct_index": [opt["option_id"] for opt in item["options"]].index(
            item["correct_option_id"]
        ),
        "rationale": item["rationale"],
        "replacement_source": proposed,
    }
    path = _item(row, row["items"][0]) + "/successor"
    assert proof.http("POST", path, status=409, json=payload)["detail"] == (
        "LID_EDITORIAL_REVIEWED_METADATA_SOURCE_REQUIRED"
    )
    proof.rights(
        edition,
        "METADATA_ONLY",
        rights_holder=source["rights_holder"],
        licence_type="Bibliographic reference only",
    )
    assert (
        proof.http(
            "POST",
            path,
            status=409,
            json={**payload, "replacement_source": {**proposed, "metadata_checksum": "0" * 64}},
        )["detail"]
        == "LID_EDITORIAL_EXACT_SOURCE_PIN_MISMATCH"
    )
    successor = proof.http("POST", path, status=201, json=payload)
    assert successor["source"]["version_id"] == edition["id"]
    assert successor["supersedes_version_id"] == old["item_version_id"]
    assert not successor["review_receipts"] and successor["maturity"] == "AI_ASSISTED_DRAFT"
    earlier = proof.http("GET", _item(row, row["items"][0]))
    assert earlier["source"]["version_id"] == old["source"]["version_id"]
    assert earlier["review_receipts"] == old["review_receipts"]
    version, _ = proof.synthetic_version()
    proof.rights(version, "ALLOW_SYNTHETIC_INGESTION", digest=DIGEST)
    proof.upload(version)
    assert proof.storage.get(proof.object_key(version)) == SYNTHETIC
    assert proof.ingest(version)["status"] == "SUCCEEDED"
    proof.approve(version)
    assert proof.index(version)["execution_class"] == "TEST_ONLY"
    assert (
        proof.search(version, expected_document_checksum=DIGEST)["results"][0]["citation"][
            "checksum"
        ]
        == DIGEST
    )
    proof.search(version, actor="candidate", status=403)
