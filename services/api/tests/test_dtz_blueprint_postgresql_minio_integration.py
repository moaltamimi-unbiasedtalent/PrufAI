"""Required S2 HTTP proof with Alembic PostgreSQL, pgvector and private MinIO."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.models import AuditEvent, ItemScore, ResultVersion, RubricVersion, RuleVersion, ScoringRun
from app.models.standard_pack import DtzBlueprintProvenance
from app.writing_assessment.service import RubricError, RubricService
from tests.test_authoritative_standards_postgresql_minio_integration import DIGEST, ROOT, SYNTHETIC
from tests.test_authoritative_standards_postgresql_minio_integration import (
    configured as _configured,
)
from tests.test_authoritative_standards_postgresql_minio_integration import expert as _expert
from tests.test_authoritative_standards_postgresql_minio_integration import proof as _proof

configured = _configured
expert = _expert
proof = _proof
DTZ = ROOT + "/dtz"
PLATFORM = "/api/v1/platform"


@pytest.fixture(scope="module")
def draft(proof):
    sources = proof.http("GET", DTZ + "/sources")
    assert len(sources) == 8
    assert len(proof.http("GET", ROOT + "/catalogue")) == 4
    sources = {
        item["key"]: proof.http("POST", DTZ + "/sources/" + item["key"] + "/register")
        for item in sources
    }
    pins = {
        key: {
            "version_id": entry["registered"]["id"],
            "metadata_checksum": entry["registered"]["metadata_checksum"],
        }
        for key, entry in sources.items()
    }
    request = {"sources": pins}
    proof.http("POST", DTZ + "/blueprints", status=409, json=request)
    for key, entry in sources.items():
        item = entry["registered"]
        assert not item["reviewed"] and entry["content_mode"] == "METADATA_ONLY"
        assert entry["external_content_rights"] == "NOT_APPROVED"
        detail = proof.http("GET", ROOT + f"/versions/{item['id']}")
        assert detail["sha256"] is None and detail["latest_ingestion"] is None
        assert detail["source_url"] == (entry["edition_url"] or entry["source_url"])
        if key == "gast-dtz-overview":
            proof.rights(item, "METADATA_ONLY", actor="creator", status=403)
        proof.rights(
            item,
            "METADATA_ONLY",
            rights_holder=entry["rights_holder"],
            licence_type="Bibliographic reference only",
        )
    with ThreadPoolExecutor(max_workers=2) as worker:
        first, second = worker.map(
            lambda _: proof.http("POST", DTZ + "/blueprints", status=201, json=request),
            range(2),
        )
    assert first == second
    assert proof.http("GET", DTZ + "/blueprints/" + first["id"]) == first
    return first, request


def _preview(draft_version, **changes):
    rule = next(item for item in draft_version["rules"] if item["code"].endswith("OVERALL_B1"))
    return {
        "expected_pack_checksum": draft_version["checksum"],
        "rule_version_id": rule["id"],
        "expected_rule_checksum": rule["checksum"],
        "as_of": "2026-11-06",
        "domains": {"listening_reading": "A2", "writing": "B1", "speaking": "B1"},
        **changes,
    }


def test_reviewed_metadata_concurrent_draft_and_exact_conflict_ledger(proof, draft):
    row, request = draft
    assert row["assessment_family"] == "LANGUAGE"
    assert row["exam_family"] == "DEUTSCH_TEST_FUER_ZUWANDERER" and row["language"] == "de"
    assert row["supported_levels"] == ["A2", "B1"] and row["allowed_classifications"] == [
        "PRACTICE"
    ]
    assert row["status"] == "DRAFT" and row["review_status"] == "REVIEW_REQUIRED"
    assert row["structural_validation"] == "PASS"
    assert len(row["checksum"]) == len(row["manifest_checksum"]) == 64
    assert row["speaking_fidelity"] == "UNSUPPORTED_FOR_FULL_FIDELITY"
    assert (
        row["effective_from"] is None and row["regulatory_candidate_effective_from"] == "2026-11-01"
    )
    skills = {section["skill"]: section for section in row["sections"]}
    assert list(skills) == ["LISTENING", "READING", "WRITING", "SPEAKING"]
    for skill, totals in [("LISTENING", [4, 5, 8, 3]), ("READING", [5, 5, 6, 3, 6])]:
        section = skills[skill]
        assert section["scoring_domain"] == "LISTENING_READING"
        assert section["item_total"] == sum(totals)
        assert [sum(t["item_count"] for t in g["tasks"]) for g in section["groups"]] == totals
    assert skills["WRITING"]["groups"][0]["tasks"][0]["task_choice_count"] == 2
    assert skills["SPEAKING"]["item_total"] is None
    speaking_tasks = [task for group in skills["SPEAKING"]["groups"] for task in group["tasks"]]
    assert {t["interaction"] for t in speaking_tasks} == {
        "RECORDED_PRACTICE",
        "INTERACTIVE_PAIR_EXAM",
        "EXAMINER_MEDIATED_EXAM",
    }
    assert all(
        t["response_mode"] == "LIVE_ORAL" and t["synthetic_example"] is None
        for t in speaking_tasks
        if t["interaction"] != "RECORDED_PRACTICE"
    )
    bindings = {source["key"]: source for source in row["source_bindings"]}
    assert set(bindings) == set(request["sources"])
    assert all(
        source["available"]
        and source["rights_status"] == "BIBLIOGRAPHIC_REFERENCE_ONLY"
        and not source["ingestion_allowed"]
        and not source["redistribution_allowed"]
        for source in bindings.values()
    )
    ledger = {claim["code"]: claim for claim in row["claim_ledger"]}
    assert {code for code, record in ledger.items() if record["status"] == "SOURCE_CONFLICT"} >= {
        "DTZ_SPEAKING_TASK_COUNT",
        "DTZ_SPEAKING_CRITERIA",
        "DTZ_WRITING_CRITERIA",
    }
    for comparison in ledger.values():
        assert comparison["reviewing_expert"] is None and comparison["resolution_evidence"] is None
        for claim in comparison["claims"]:
            pin = request["sources"][claim["source_key"]]
            assert claim["source_version_id"] == pin["version_id"]
            assert claim["source_metadata_checksum"] == pin["metadata_checksum"]
            assert claim["verified_on"] == "2026-11-06"
            assert claim["source_url"].startswith("https://")
    legal = next(
        claim
        for claim in ledger["DTZ_OVERALL_B1"]["claims"]
        if claim["source_key"] == "inttestv-2026-306"
    )
    assert legal["reference_url"] == "https://www.gesetze-im-internet.de/inttestv/__10.html"
    assert "306/regelungstext.pdf" in legal["source_url"]
    wrong = {key: dict(pin) for key, pin in request["sources"].items()}
    wrong["gast-dtz-overview"]["metadata_checksum"] = "0" * 64
    proof.http("POST", DTZ + "/blueprints", status=409, json={"sources": wrong})
    wrong["gast-dtz-overview"] = dict(wrong["gast-dtz-faq"])
    proof.http("POST", DTZ + "/blueprints", status=409, json={"sources": wrong})
    assert len(proof.http("GET", DTZ + "/blueprints")) == 1


def test_exact_draft_rule_boundaries_no_live_result_records(proof, draft):
    row, _ = draft
    path = DTZ + "/blueprints/" + row["id"] + "/preview"
    for listening_reading, writing, speaking, expected in [
        ("B1", "B1", "B1", "B1"),
        ("A2", "B1", "B1", "B1"),
        ("B1", "A2", "B1", "B1"),
        ("BELOW_A2", "B1", "B1", "UNDETERMINED"),
        ("B1", "BELOW_A2", "B1", "UNDETERMINED"),
        ("B1", "B1", "A2", "UNDETERMINED"),
        ("B1", "A2", None, "UNDETERMINED"),
        ("B1", None, "B1", "UNDETERMINED"),
    ]:
        result = proof.http(
            "POST",
            path,
            json=_preview(
                row,
                domains={
                    "listening_reading": listening_reading,
                    "writing": writing,
                    "speaking": speaking,
                },
            ),
        )
        assert result["execution_class"] == "DRAFT_PREVIEW" and result["outcome"] == expected
        assert result["official_result_created"] is False
        assert result["rule"]["checksum"] == _preview(row)["expected_rule_checksum"]
    assert proof.http("POST", path, json=_preview(row, as_of="2026-11-01"))["outcome"] == "B1"
    assert proof.http("POST", path, json=_preview(row, as_of="2026-10-31"))["outcome"] == (
        "UNDETERMINED"
    )
    for wrong in [
        {"rule_version_id": None},
        {"rule_version_id": str(uuid.uuid4())},
        {"expected_rule_checksum": "1" * 64},
        {"expected_pack_checksum": "2" * 64},
    ]:
        assert proof.http("POST", path, json=_preview(row, **wrong))["outcome"] == "UNDETERMINED"
    for wrong in [
        {"candidate_id": str(uuid.uuid4())},
        {"exam_version_id": str(uuid.uuid4())},
        {"domains": {"speaking": 101}},
        {"objective_answer": "INVALID"},
    ]:
        proof.http("POST", path, status=422, json=_preview(row, **wrong))
    for answer, expected, status in [
        ("B", "1", "SCORED"),
        ("A", "0", "SCORED"),
        (None, "0", "UNANSWERED"),
    ]:
        objective = proof.http("POST", path, json=_preview(row, objective_answer=answer))[
            "objective"
        ]
        assert objective["status"] == status
        assert (
            objective["awarded_points"] == expected and objective["combined_maximum_points"] == 45
        )
    with proof.expert.factory() as session:
        for model in (ScoringRun, ItemScore, ResultVersion):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_review_required_rubrics_publication_delivery_and_tenant_denial(proof, draft):
    row, request = draft
    path = DTZ + "/blueprints/" + row["id"]
    assert {rubric["skill"] for rubric in row["rubrics"]} == {"WRITING", "SPEAKING"}
    assert all(
        r["review_status"] == "REVIEW_REQUIRED" and r["assessment_mode"] == "HUMAN_ONLY"
        for r in row["rubrics"]
    )
    with proof.expert.factory() as session:
        for rubric in row["rubrics"]:
            version = session.get(RubricVersion, uuid.UUID(rubric["id"]))
            assert version.criteria == [] and version.performance_levels == []
            assert version.rubric.organisation_id is None and version.approved_at is None
            with pytest.raises(RubricError, match="DTZ_DRAFT_RUBRIC_REVIEW_REQUIRED"):
                RubricService.assert_executable(version)
        for rule in row["rules"]:
            version = session.get(RuleVersion, uuid.UUID(rule["id"]))
            assert version.status.value == "DRAFT" and version.pack_bindings == []
            assert all(binding.knowledge_page_id is None for binding in version.evidence_bindings)
    generic = PLATFORM + "/standard-pack-versions/" + row["id"]
    for action in ("approve", "publish", "request-review"):
        denial = proof.http("POST", generic + "/" + action, actor="approver", status=409)
        assert denial["detail"] == "DTZ_DRAFT_PUBLICATION_BLOCKED"
    for rule in row["rules"]:
        proof.http("POST", PLATFORM + f"/rule-versions/{rule['id']}/approve", status=(400, 409))
        proof.http(
            "POST",
            PLATFORM + f"/rule-versions/{rule['id']}/dry-run",
            status=(400, 409),
            json={"facts": {}},
        )
    exam = proof.http(
        "POST",
        PLATFORM + "/exams",
        status=201,
        json={
            "code": "S2_SYNTHETIC_DENIAL",
            "name": "S2 delivery denial fixture",
            "scope_type": "PLATFORM_SHARED",
        },
    )
    denial = proof.http(
        "POST",
        PLATFORM + f"/exams/{exam['id']}/versions",
        status=400,
        json={
            "version_label": "cannot-deliver",
            "standard_pack_version_id": row["id"],
        },
    )
    assert denial["detail"] == "DTZ_DRAFT_DELIVERY_BLOCKED"
    organisation = proof.headers["revoked"]["X-PrufAI-Organisation-ID"]
    assert (
        proof.http("GET", f"/api/v1/organisations/{organisation}/standards/dtz", actor="revoked")[
            "published_versions"
        ]
        == []
    )
    proof.http(
        "GET", f"/api/v1/organisations/{uuid.uuid4()}/standards/dtz", actor="revoked", status=403
    )
    for actor in ("candidate", "revoked"):
        proof.http("GET", path, actor=actor, status=403)
        proof.http("GET", DTZ + "/sources", actor=actor, status=403)
        proof.http("POST", DTZ + "/blueprints", actor=actor, status=403, json=request)
        proof.http("POST", path + "/preview", actor=actor, status=403, json=_preview(row))


def test_private_minio_original_evidence_does_not_replace_official_metadata(proof, draft):
    row, _ = draft
    official = next(s for s in row["source_bindings"] if s["key"] == "gast-dtz-overview")
    pinned = {
        "id": official["document_version_id"],
        "metadata_checksum": official["source_metadata_checksum"],
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
    result = proof.search(version, expected_document_checksum=DIGEST)["results"]
    assert result[0]["citation"]["document_version_id"] == version["id"]
    assert result[0]["citation"]["checksum"] == DIGEST
    assert result[0]["matched_vector"] is True
    assert proof.search(pinned)["results"] == []
    for actor in ("candidate", "revoked"):
        proof.search(version, actor=actor, status=403)
        proof.http("GET", ROOT + f"/versions/{version['id']}/integrity", actor=actor, status=403)


def test_immutable_history_and_source_withdrawal_fail_closed(proof, draft):
    row, request = draft
    path = DTZ + "/blueprints/" + row["id"]
    with proof.expert.factory() as session:
        pack_id = uuid.UUID(row["id"])
        rule_id = uuid.UUID(row["rules"][0]["id"])
        rubric_id = uuid.UUID(row["rubrics"][0]["id"])
        task_id = uuid.UUID(row["sections"][0]["groups"][0]["tasks"][0]["id"])
        binding_id = uuid.UUID(row["source_bindings"][0]["id"])
        for sql, identifier in [
            (
                "UPDATE standard_pack_dtz_provenance SET manifest = '{}' "
                "WHERE standard_pack_version_id = :id",
                pack_id,
            ),
            ("UPDATE standard_pack_versions SET status = 'ACTIVE' WHERE id = :id", pack_id),
            ("UPDATE rule_versions SET status = 'APPROVED' WHERE id = :id", rule_id),
            ("UPDATE rubric_versions SET status = 'ACTIVE' WHERE id = :id", rubric_id),
            (
                "UPDATE standard_pack_task_specs SET max_items = max_items + 1 WHERE id = :id",
                task_id,
            ),
            ("DELETE FROM standard_pack_source_bindings WHERE id = :id", binding_id),
        ]:
            with (
                pytest.raises(DBAPIError, match="DTZ_PREVIEW_HISTORY_IMMUTABLE"),
                session.begin_nested(),
            ):
                session.execute(text(sql), {"id": identifier})
        assert session.scalar(select(func.count()).select_from(DtzBlueprintProvenance)) == 1
        audit = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type.in_(
                        ["standards.dtz_draft.created", "standards.dtz_draft.previewed"]
                    ),
                )
            )
        )
        assert sum(event.event_type.endswith(".created") for event in audit) == 1
        assert any(event.event_type.endswith(".previewed") for event in audit)
        assert all(
            set(event.metadata_json) <= {"pack_checksum", "manifest_checksum", "outcome", "reason"}
            for event in audit
        )
        with (
            pytest.raises(DBAPIError, match="DTZ_PREVIEW_HISTORY_IMMUTABLE"),
            session.begin_nested(),
        ):
            session.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": audit[0].id})
    overview = request["sources"]["gast-dtz-overview"]
    original = proof.http("GET", ROOT + f"/versions/{overview['version_id']}")
    base = {
        "content_mode": "METADATA_ONLY",
        "supersedes_document_version_id": overview["version_id"],
        "source_url": original["source_url"],
    }
    version_endpoint = ROOT + f"/sources/{original['source_id']}/versions"
    successor = proof.http(
        "POST",
        version_endpoint,
        status=201,
        json={**base, "version_label": "S2 technical successor fixture"},
    )
    future = proof.http(
        "POST",
        version_endpoint,
        status=201,
        json={
            **base,
            "version_label": "S2 future successor fixture",
            "effective_from": "2099-01-01T00:00:00Z",
        },
    )
    third_party = proof.http(
        "POST",
        version_endpoint,
        status=201,
        json={
            **base,
            "version_label": "S2 third-party successor fixture",
            "source_url": "https://third-party.example.invalid/overview",
        },
    )
    for item in (future, third_party):
        proof.rights(item, "METADATA_ONLY")
    assert proof.http("POST", path + "/preview", json=_preview(row))["outcome"] == "B1"
    proof.rights(successor, "METADATA_ONLY")
    rejected = proof.http("POST", DTZ + "/blueprints", status=409, json=request)
    assert rejected["detail"] == "DTZ_SOURCE_SUCCESSOR_REVIEWED"
    preview = proof.http("POST", path + "/preview", json=_preview(row))
    assert preview["reason"] == "DTZ_SOURCE_SUCCESSOR_REVIEWED"
    stale = proof.http("GET", path)
    assert (
        next(item for item in stale["source_bindings"] if item["key"] == "gast-dtz-overview")[
            "available"
        ]
        is False
    )
    source = request["sources"]["gast-dtz-faq"]
    proof.rights({"id": source["version_id"], **source}, "WITHDRAW")
    preview = proof.http("POST", path + "/preview", json=_preview(row))
    assert preview["outcome"] == "UNDETERMINED" and preview["rule"] is None
    assert preview["reason"] == "DTZ_SOURCE_RIGHTS_OR_VERSION_STALE"
    withdrawn = proof.http("GET", path)
    assert (
        withdrawn["checksum"] == row["checksum"]
        and withdrawn["claim_ledger"] == row["claim_ledger"]
    )
    assert (
        next(s for s in withdrawn["source_bindings"] if s["key"] == "gast-dtz-faq")["available"]
        is False
    )
    proof.http("POST", DTZ + "/blueprints", status=409, json=request)
