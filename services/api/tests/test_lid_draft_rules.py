"""Unit proof for exact bibliographic pins and the two LiD draft comparisons."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError, TenantContext
from app.knowledge.catalogue import ENTRIES
from app.lid_policy import LID_RULE_PREVIEW_ONLY
from app.models import Base, RuleVersion
from app.models.enums import RuleStatus
from app.rules.service import RulesService
from app.services.knowledge import KnowledgeRegistryService
from app.standard_packs.dtz_sources import DTZ_ENTRIES
from app.standard_packs.lid_rules import evaluate_lid_preview
from app.standard_packs.lid_service import create_lid_blueprint
from app.standard_packs.lid_sources import (
    BAMF_CATALOGUE,
    EINBTESTV,
    INTTESTV,
    INTV,
    LID_ENTRIES,
    lid_sources,
    register_lid_source,
    resolve_lid_sources,
)
from app.writing_assessment.service import canonical_checksum

_NOW = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
_CREATE = frozenset(
    {"standards.authority.manage", "standards.source.manage", "standards.document.manage"}
)
_REVIEW = frozenset({"standards.document.review"})


def _review(session, reviewer, document, *, decision="METADATA_ONLY"):
    return KnowledgeRegistryService().review_rights(
        session,
        reviewer,
        version_id=document.id,
        expected_metadata_checksum=document.metadata_checksum,
        decision=decision,
        basis_reference="LiD unit fixture, bibliographic reference only",
        rights_holder="Official publisher",
        licence_type="Bibliographic reference only",
        retention_policy="Bibliographic record",
    )


@pytest.fixture
def registry_clock(monkeypatch):
    from app.standard_packs import dtz_sources

    class FixedClock(datetime):
        current = _NOW

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    monkeypatch.setattr(dtz_sources, "datetime", FixedClock)
    return FixedClock


@pytest.fixture
def prepared(registry_clock):
    engine = create_engine("sqlite+pysqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            creator = TenantContext(
                uuid.uuid4(), None, None, platform_permissions=_CREATE | _REVIEW
            )
            reviewer = TenantContext(uuid.uuid4(), None, None, platform_permissions=_REVIEW)
            documents = {key: register_lid_source(session, creator, key) for key in LID_ENTRIES}
            pins = {key: (row.id, row.metadata_checksum) for key, row in documents.items()}
            with pytest.raises(ValueError, match="LID_REVIEWED_METADATA_SOURCE_REQUIRED"):
                resolve_lid_sources(session, pins)
            with pytest.raises(AuthorizationError, match="INDEPENDENT_RIGHTS_REVIEW"):
                _review(session, creator, documents[EINBTESTV])
            for document in documents.values():
                _review(session, reviewer, document)
            pack = create_lid_blueprint(session, creator, pins)
            session.commit()
            yield session, pack, documents, creator, reviewer
    finally:
        engine.dispose()


def _preview(session, pack, count, *, total=33, as_of=_NOW, rules=None):
    return evaluate_lid_preview(
        session,
        pack,
        pack.lid_provenance.manifest["rules"] if rules is None else rules,
        raw_correct_count=count,
        total_item_count=total,
        as_of=as_of,
    )


def _outcomes(preview):
    assert preview["official_outcome"] == "UNDETERMINED"
    assert preview["administrative_recognition"] == "NOT_ESTABLISHED"
    return [row["numerical_outcome"] for row in preview["contexts"]]


def test_registry_reuses_exact_shared_editions_and_seals_claims(prepared):
    session, pack, documents, creator, _ = prepared
    manifest = pack.lid_provenance.manifest
    assert LID_ENTRIES[BAMF_CATALOGUE] is ENTRIES[BAMF_CATALOGUE]
    assert LID_ENTRIES[INTTESTV] is ENTRIES[INTTESTV]
    assert LID_ENTRIES[INTV] is DTZ_ENTRIES[INTV]
    assert all(source["registered"]["reviewed"] for source in lid_sources(session))
    for key, document in documents.items():
        assert register_lid_source(session, creator, key).id == document.id
        snapshot = manifest["sources"][key]
        assert snapshot["metadata_use_allowed"]
        assert not any(
            snapshot[permission]
            for permission in (
                "ingestion_allowed",
                "question_reuse_allowed",
                "image_reuse_allowed",
                "derivative_use_allowed",
                "redistribution_allowed",
            )
        )
        assert document.sha256 is None and document.storage_key is None
        assert snapshot["rights_review_actor_id"] != str(creator.user_id)
    for claim in manifest["claim_ledger"]:
        assert claim["checksum"] == canonical_checksum(
            {key: value for key, value in claim.items() if key != "checksum"}
        )
        for observation in claim["claims"]:
            source = manifest["sources"][observation["source_key"]]
            assert observation["source_version_id"] == source["document_version_id"]
            assert observation["source_metadata_checksum"] == source["source_metadata_checksum"]
            assert observation["reference_url"].startswith("https://")


def test_draft_thresholds_boundaries_and_invalid_scores(prepared):
    session, pack, *_ = prepared
    below = "BELOW_THRESHOLD"
    met = "THRESHOLD_MET"
    for correct, expected in [
        (0, [below, below]),
        (14, [below, below]),
        (15, [met, below]),
        (16, [met, below]),
        (17, [met, met]),
        (32, [met, met]),
        (33, [met, met]),
    ]:
        result = _preview(session, pack, correct)
        assert _outcomes(result) == expected
        assert [row["purpose"] for row in result["contexts"]] == [
            "ORIENTATION_COURSE_CONTEXT",
            "NATURALIZATION_KNOWLEDGE_CONTEXT",
        ]
        assert [row["threshold"] for row in result["contexts"]] == [15, 17]
        assert all(len(row["rule_checksum"]) == 64 for row in result["contexts"])
    for correct, total in [(None, 33), (17, 32), (34, 33), (True, 33)]:
        assert _outcomes(_preview(session, pack, correct, total=total)) == ["UNDETERMINED"] * 2
    assert _outcomes(_preview(session, pack, 33, rules=[])) == ["UNDETERMINED"] * 2
    earlier = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    assert _outcomes(_preview(session, pack, 33, as_of=earlier)) == ["UNDETERMINED"] * 2
    rule_id = uuid.UUID(pack.lid_provenance.manifest["rules"][0]["id"])
    rule = session.get(RuleVersion, rule_id)
    assert rule is not None
    with pytest.raises(ValueError, match=LID_RULE_PREVIEW_ONLY):
        RulesService.dry_run(rule, {"score": {"raw_correct_count": 33, "total_item_count": 33}})
    rule.status = RuleStatus.APPROVED
    assert _outcomes(_preview(session, pack, 33)) == ["UNDETERMINED"] * 2


@pytest.mark.parametrize(
    "change",
    ["reviewed_successor", "withdrawal", "successor_withdrawn", "successor_expired"],
)
def test_rule_preview_fails_closed_after_source_changes(prepared, registry_clock, change):
    session, pack, documents, creator, reviewer = prepared
    source = documents[EINBTESTV]
    if change == "withdrawal":
        _review(session, reviewer, source, decision="WITHDRAW")
    else:
        successor = KnowledgeRegistryService().create_version(
            session,
            creator,
            source_id=source.source_id,
            version_label="Technical successor fixture",
            effective_from=_NOW,
            effective_until=_NOW + timedelta(days=1),
            source_url=LID_ENTRIES[EINBTESTV].url,
            supersedes_document_version_id=source.id,
        )
        assert _outcomes(_preview(session, pack, 17)) == ["THRESHOLD_MET"] * 2
        _review(session, reviewer, successor)
        assert _outcomes(_preview(session, pack, 17)) == ["UNDETERMINED"] * 2
        if change == "successor_withdrawn":
            _review(session, reviewer, successor, decision="WITHDRAW")
        if change == "successor_expired":
            registry_clock.current = _NOW + timedelta(days=1)
    assert _outcomes(_preview(session, pack, 17)) == ["UNDETERMINED"] * 2
    sources = {row["key"]: row for row in lid_sources(session)}
    assert not sources[EINBTESTV]["registered"]["reviewed"]
