from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    Base,
    KnowledgeAuthority,
    KnowledgeDocumentVersion,
    KnowledgeSource,
    StandardPackModule,
)
from app.models.enums import (
    AssessmentFamily,
    KnowledgeAuthorityType,
    KnowledgeDocumentType,
    KnowledgeScope,
    KnowledgeVersionStatus,
    PackValidationStatus,
    StandardPackBindingType,
    StandardPackStatus,
)
from app.standard_packs import StandardPackService


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _approved_evidence(session: Session) -> KnowledgeDocumentVersion:
    now = datetime.now(timezone.utc)
    suffix = uuid.uuid4().hex[:8]
    authority = KnowledgeAuthority(
        code=f"SYNTHETIC_{suffix}",
        name="TEST FIXTURE — NOT OFFICIAL STANDARD",
        authority_type=KnowledgeAuthorityType.INTERNAL,
        created_at=now,
        updated_at=now,
    )
    session.add(authority)
    session.flush()
    source = KnowledgeSource(
        authority_id=authority.id,
        source_code=f"SYNTHETIC_STANDARD_{suffix}",
        title="TEST FIXTURE — NOT OFFICIAL STANDARD",
        document_type=KnowledgeDocumentType.STANDARD,
        scope=KnowledgeScope.PLATFORM_SHARED,
        assessment_family=AssessmentFamily.LANGUAGE,
        created_at=now,
        updated_at=now,
    )
    session.add(source)
    session.flush()
    version = KnowledgeDocumentVersion(
        source_id=source.id,
        version_label="fixture-1",
        status=KnowledgeVersionStatus.APPROVED,
        created_at=now,
    )
    session.add(version)
    session.flush()
    return version


def _pack_with_version(session: Session, levels: list[str] | None = None):
    suffix = uuid.uuid4().hex[:8]
    pack = StandardPackService.create_pack(
        session,
        code=f"SYNTHETIC_B1_{suffix}",
        name="TEST FIXTURE — NOT OFFICIAL STANDARD",
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
    )
    version = StandardPackService.create_version(
        session, pack, version_label="1", supported_levels=levels or ["B1"]
    )
    evidence = _approved_evidence(session)
    StandardPackService.bind_source(
        session,
        version,
        binding_type=StandardPackBindingType.NORMATIVE,
        document_version_id=evidence.id,
    )
    session.flush()
    return pack, version


def test_language_pack_lifecycle_and_published_immutability(db: Session) -> None:
    pack, version = _pack_with_version(db)
    result = StandardPackService.validate(db, version)
    assert result.status is PackValidationStatus.PASS
    StandardPackService.transition(db, version, StandardPackStatus.HUMAN_REVIEW)
    StandardPackService.transition(db, version, StandardPackStatus.APPROVED)
    StandardPackService.transition(db, version, StandardPackStatus.PUBLISHED)
    assert version.configuration_checksum == result.checksum
    with pytest.raises(ValueError, match="immutable"):
        StandardPackService.bind_source(
            db,
            version,
            binding_type=StandardPackBindingType.SUPPORTING,
            knowledge_source_id=uuid.uuid4(),
        )
    with pytest.raises(ValueError, match="invalid Standard Pack transition"):
        StandardPackService.transition(db, version, StandardPackStatus.DRAFT)
    assert pack.code.startswith("SYNTHETIC_B1_")


def test_lid_cefr_and_official_certification_are_rejected(db: Session) -> None:
    pack = StandardPackService.create_pack(
        db,
        code="SYNTHETIC_LID",
        name="TEST FIXTURE — NOT OFFICIAL STANDARD",
        assessment_family=AssessmentFamily.INTEGRATION_KNOWLEDGE,
        exam_family="LEBEN_IN_DEUTSCHLAND",
        language="de",
    )
    version = StandardPackService.create_version(
        db, pack, version_label="1", supported_levels=["B1"]
    )
    result = StandardPackService.validate(db, version)
    assert {issue.code for issue in result.issues} >= {
        "PACK.LID_CEFR_NOT_ALLOWED",
        "PACK.MISSING_NORMATIVE_SOURCE",
    }
    version.supported_levels = []
    version.allowed_classifications = ["OFFICIAL_CERTIFICATION"]
    result = StandardPackService.validate(db, version)
    assert "PACK.OFFICIAL_CLASSIFICATION_NOT_AUTHORIZED" in {issue.code for issue in result.issues}


def test_historical_resolution_and_deterministic_export(db: Session) -> None:
    pack, first = _pack_with_version(db)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first.effective_from = start
    first.effective_until = start + timedelta(days=365)
    StandardPackService.transition(db, first, StandardPackStatus.HUMAN_REVIEW)
    StandardPackService.transition(db, first, StandardPackStatus.APPROVED)
    StandardPackService.transition(db, first, StandardPackStatus.PUBLISHED)
    StandardPackService.transition(db, first, StandardPackStatus.ACTIVE)
    second = StandardPackService.create_version(
        db,
        pack,
        version_label="2",
        effective_from=start + timedelta(days=365),
        supersedes_version_id=first.id,
    )
    evidence = _approved_evidence(db)
    StandardPackService.bind_source(
        db, second, binding_type=StandardPackBindingType.NORMATIVE, document_version_id=evidence.id
    )
    StandardPackService.transition(db, second, StandardPackStatus.HUMAN_REVIEW)
    StandardPackService.transition(db, second, StandardPackStatus.APPROVED)
    StandardPackService.transition(db, second, StandardPackStatus.PUBLISHED)
    StandardPackService.transition(db, second, StandardPackStatus.ACTIVE)
    assert (
        StandardPackService.resolve(db, pack.id, as_of=datetime(2026, 6, 1, tzinfo=timezone.utc)).id
        == first.id
    )
    assert (
        StandardPackService.resolve(db, pack.id, as_of=datetime(2027, 6, 1, tzinfo=timezone.utc)).id
        == second.id
    )
    exported = StandardPackService.export(second)
    assert exported["pack_checksum"] == second.configuration_checksum
    assert "storage_key" not in str(exported)
    assert StandardPackService.import_payload(exported)["version_label"] == "2"
    with pytest.raises(ValueError, match="executable"):
        StandardPackService.import_payload(
            {
                "schema_version": "1",
                "version_label": "x",
                "modules": [],
                "source_bindings": [],
                "code": "eval(x)",
            }
        )


def test_checksum_and_diff_capture_structural_change(db: Session) -> None:
    _, first = _pack_with_version(db)
    _, second = _pack_with_version(db, ["A2", "B1"])
    first.modules.append(
        StandardPackModule(
            code="READING",
            name="Synthetic reading",
            sequence=1,
            created_at=datetime.now(timezone.utc),
        )
    )
    assert StandardPackService.checksum(first) != StandardPackService.checksum(second)
    diff = StandardPackService.diff(first, second)
    assert diff["modules_changed"] is True
