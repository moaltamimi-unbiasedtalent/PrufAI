import hashlib
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.authorization import AuthorizationError, TenantContext
from app.config import Settings
from app.knowledge.parsers import ParserRegistry, PdfParser
from app.knowledge.storage import (
    InMemoryObjectStorage,
    S3CompatibleObjectStorage,
    StorageError,
    build_object_storage,
    build_storage_key,
)
from app.models import Base, KnowledgeDocumentPage, KnowledgeIngestionJob
from app.models.enums import (
    AssessmentFamily,
    KnowledgeAuthorityType,
    KnowledgeDocumentType,
    KnowledgeScope,
    KnowledgeVersionStatus,
)
from app.services.knowledge import KnowledgeRegistryService, SourceConflict


def test_pdf_parser_contract() -> None:
    from io import BytesIO

    from pypdf import PdfWriter

    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(output)
    pages = PdfParser().parse(output.getvalue())
    assert len(pages) == 1
    assert pages[0].extraction_status == "EMPTY"


def test_storage_key_and_s3_round_trip_with_fake_client() -> None:
    import uuid

    class Body:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def read(self) -> bytes:
            return self.content

    class FakeClient:
        def __init__(self) -> None:
            self.objects: dict[str, bytes] = {}

        def put_object(self, **kwargs: object) -> None:
            self.objects[str(kwargs["Key"])] = bytes(kwargs["Body"])  # type: ignore[arg-type]

        def get_object(self, **kwargs: object) -> dict[str, Body]:
            content = self.objects[str(kwargs["Key"])]
            range_header = kwargs.get("Range")
            if isinstance(range_header, str):
                start_text, end_text = range_header.removeprefix("bytes=").split("-", 1)
                content = content[int(start_text) : int(end_text) + 1]
            return {"Body": Body(content)}

        def head_object(self, **kwargs: object) -> dict[str, int]:
            if str(kwargs["Key"]) not in self.objects:
                raise KeyError(kwargs["Key"])
            return {"ContentLength": len(self.objects[str(kwargs["Key"])])}

    source_id, version_id = uuid.uuid4(), uuid.uuid4()
    key = build_storage_key(source_id, version_id, "../../policy.pdf")
    assert key.startswith(f"standards/{source_id}/{version_id}/")
    assert ".." not in key
    client = FakeClient()
    storage = S3CompatibleObjectStorage(
        endpoint="http://minio",
        bucket="private",
        access_key="access",
        secret_key="secret",
        client=client,
    )
    storage.put(key, b"fixture", "text/plain")
    assert storage.exists(key)
    assert storage.get(key) == b"fixture"
    assert storage.size(key) == 7
    assert storage.read_range(key, 1, 3) == b"ixt"
    with pytest.raises(StorageError):
        storage.get("missing")


def test_production_storage_cannot_fall_back_to_memory() -> None:
    settings = Settings(
        environment="production",
        auth_rate_limit_backend="redis",
        object_storage_backend="memory",
    )
    with pytest.raises(StorageError, match="cannot use in-memory"):
        build_object_storage(settings)


@pytest.fixture
def knowledge_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def platform_context() -> TenantContext:
    return TenantContext(
        user_id=uuid.uuid4(),
        organisation_id=None,
        membership_id=None,
        platform_permissions=frozenset(
            {
                "standards.authority.manage",
                "standards.authority.read",
                "standards.source.manage",
                "standards.source.read",
                "standards.document.manage",
                "standards.document.read",
                "standards.document.review",
            }
        ),
    )


def create_fixture(
    session: Session,
    context: TenantContext,
    storage: InMemoryObjectStorage,
    *,
    effective_from: datetime | None = None,
    effective_until: datetime | None = None,
) -> tuple[KnowledgeRegistryService, object, object, object]:
    service = KnowledgeRegistryService(storage=storage)
    authority = service.create_authority(
        session,
        context,
        code="PRUFAI",
        name="PrüfAI",
        authority_type=KnowledgeAuthorityType.INTERNAL,
    )
    source = service.create_source(
        session,
        context,
        authority_id=authority.id,
        source_code="PRUFAI_POLICY",
        title="Synthetic PrüfAI policy",
        document_type=KnowledgeDocumentType.POLICY,
        assessment_family=AssessmentFamily.LANGUAGE,
        cefr_level="B1",
        scope=KnowledgeScope.PLATFORM_SHARED,
    )
    version = service.create_version(
        session,
        context,
        source_id=source.id,
        version_label="2026.1",
        content_mode="ORIGINAL_SYNTHETIC",
        effective_from=effective_from or datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_until=effective_until,
    )
    return service, authority, source, version


def allow_source(service, session, context, version, content):
    reviewer = replace(context, user_id=uuid.uuid4())
    service.review_rights(
        session,
        reviewer,
        version_id=version.id,
        expected_metadata_checksum=version.metadata_checksum,
        decision="ALLOW_SYNTHETIC_INGESTION",
        basis_reference="fixture:original-synthetic",
        rights_holder="PrüfAI synthetic fixture author",
        licence_type="INTERNAL_SYNTHETIC_TEST_ONLY",
        retention_policy="ISOLATED_TEST_FIXTURE",
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )
    return reviewer


def test_metadata_only_rights_are_independent_exact_and_withdrawable(
    knowledge_session: Session, platform_context: TenantContext
) -> None:
    storage = InMemoryObjectStorage()
    service, _, source, _ = create_fixture(knowledge_session, platform_context, storage)
    version = service.create_version(
        knowledge_session, platform_context, source_id=source.id, version_label="metadata-only"
    )
    reviewer = replace(platform_context, user_id=uuid.uuid4())
    request = {
        "version_id": version.id,
        "expected_metadata_checksum": version.metadata_checksum,
        "decision": "METADATA_ONLY",
        "basis_reference": "fixture:metadata-review",
        "rights_holder": "Synthetic fixture owner",
        "licence_type": "REFERENCE_ONLY",
        "retention_policy": "ISOLATED_TEST_FIXTURE",
    }
    with pytest.raises(AuthorizationError, match="INDEPENDENT_RIGHTS"):
        service.review_rights(knowledge_session, platform_context, **request)
    with pytest.raises(SourceConflict, match="METADATA_CHECKSUM_MISMATCH"):
        service.review_rights(
            knowledge_session, reviewer, **{**request, "expected_metadata_checksum": "0" * 64}
        )
    decision = service.review_rights(knowledge_session, reviewer, **request)
    assert decision.legal_review_status.value == "APPROVED" and not decision.ingestion_allowed
    assert service.review_rights(knowledge_session, reviewer, **request).id == decision.id
    with pytest.raises(SourceConflict, match="RIGHTS_DECISION_IMMUTABLE"):
        service.set_legal_metadata(
            knowledge_session, platform_context, version_id=version.id, rights_holder="changed"
        )
    with pytest.raises(SourceConflict, match="CONTENT_RIGHTS_REQUIRED"):
        service.register_file(
            knowledge_session,
            platform_context,
            version_id=version.id,
            filename="denied.txt",
            content=b"Original synthetic denial fixture",
        )
    assert not storage.objects and version.sha256 is None
    checksum = decision.decision_checksum
    service.review_rights(knowledge_session, reviewer, **{**request, "decision": "WITHDRAW"})
    assert version.status == KnowledgeVersionStatus.RETIRED
    assert (
        decision.decision_checksum == checksum
        and decision.legal_reviewed_by_user_id == reviewer.user_id
    )


def test_text_ingestion_provenance_and_approval_boundary(
    knowledge_session: Session, platform_context: TenantContext
) -> None:
    storage = InMemoryObjectStorage()
    service, _, source, version = create_fixture(knowledge_session, platform_context, storage)
    content = b"TEST FIXTURE\nNot an official examination standard."
    with pytest.raises(SourceConflict, match="SOURCE_CONTENT_RIGHTS_REQUIRED"):
        service.register_file(
            knowledge_session,
            platform_context,
            version_id=version.id,
            filename="policy.txt",
            content=content,
        )
    reviewer = allow_source(service, knowledge_session, platform_context, version, content)
    service.register_file(
        knowledge_session,
        platform_context,
        version_id=version.id,
        filename="policy.txt",
        content=content,
        declared_mime_type="text/plain",
    )
    job = service.ingest(knowledge_session, platform_context, version_id=version.id)
    assert job.status.value == "SUCCEEDED"
    stored = knowledge_session.get(type(version), version.id)
    assert stored is not None and stored.status is KnowledgeVersionStatus.REVIEW_REQUIRED
    page = knowledge_session.scalar(select(KnowledgeDocumentPage))
    assert page is not None and page.page_number == 1
    provenance = service.provenance(knowledge_session, page_id=page.id)
    assert provenance["source_code"] == source.source_code
    assert provenance["page"] == 1
    assert provenance["checksum"] == version.sha256
    assert (
        service.approved_at(
            knowledge_session, source_id=source.id, at=datetime(2026, 5, 1, tzinfo=timezone.utc)
        )
        == []
    )
    approved = service.approve(
        knowledge_session,
        reviewer,
        version_id=version.id,
        expected_sha256=version.sha256,
    )
    assert approved.status is KnowledgeVersionStatus.APPROVED
    assert service.approved_at(
        knowledge_session, source_id=source.id, at=datetime(2026, 5, 1, tzinfo=timezone.utc)
    ) == [approved]
    with pytest.raises(ValueError, match="IMMUTABLE"):
        service.register_file(
            knowledge_session,
            platform_context,
            version_id=version.id,
            filename="again.txt",
            content=b"changed",
            declared_mime_type="text/plain",
        )


def test_authority_duplicates_lid_taxonomy_and_rights_defaults(
    knowledge_session: Session, platform_context: TenantContext
) -> None:
    storage = InMemoryObjectStorage()
    service = KnowledgeRegistryService(storage=storage)
    authority = service.create_authority(
        knowledge_session,
        platform_context,
        code="BAMF",
        name="BAMF",
        authority_type=KnowledgeAuthorityType.FEDERAL_AUTHORITY,
    )
    with pytest.raises(ValueError, match="already exists"):
        service.create_authority(
            knowledge_session,
            platform_context,
            code="bamf",
            name="Duplicate",
            authority_type=KnowledgeAuthorityType.FEDERAL_AUTHORITY,
        )
    lid = service.create_source(
        knowledge_session,
        platform_context,
        authority_id=authority.id,
        source_code="LID",
        title="Leben in Deutschland registry entry",
        document_type=KnowledgeDocumentType.REGULATION,
        assessment_family=AssessmentFamily.INTEGRATION_KNOWLEDGE,
        exam_family="LEBEN_IN_DEUTSCHLAND",
    )
    assert lid.cefr_level is None
    with pytest.raises(ValueError, match="must not use a CEFR"):
        service.create_source(
            knowledge_session,
            platform_context,
            authority_id=authority.id,
            source_code="LID_BAD",
            title="Invalid LiD",
            document_type=KnowledgeDocumentType.REGULATION,
            assessment_family=AssessmentFamily.INTEGRATION_KNOWLEDGE,
            cefr_level="B1",
        )
    version = service.create_version(
        knowledge_session, platform_context, source_id=lid.id, version_label="1"
    )
    legal = service.set_legal_metadata(knowledge_session, platform_context, version_id=version.id)
    assert legal.redistribution_allowed is None
    assert legal.commercial_use_allowed is None


def test_platform_permission_and_date_validation(
    knowledge_session: Session, platform_context: TenantContext
) -> None:
    storage = InMemoryObjectStorage()
    service, authority, source, _ = create_fixture(knowledge_session, platform_context, storage)
    tenant_context = TenantContext(
        user_id=platform_context.user_id,
        organisation_id=None,
        membership_id=None,
        permissions=frozenset({"standards.document.manage"}),
    )
    with pytest.raises(AuthorizationError):
        service.create_authority(
            knowledge_session,
            tenant_context,
            code="TENANT",
            name="Tenant",
            authority_type=KnowledgeAuthorityType.OTHER,
        )
    with pytest.raises(ValueError, match="effective_until"):
        service.create_version(
            knowledge_session,
            platform_context,
            source_id=source.id,
            version_label="bad-dates",
            effective_from=datetime.now(timezone.utc),
            effective_until=datetime.now(timezone.utc) - timedelta(days=1),
        )
    assert authority.code == "PRUFAI"


def test_current_and_historical_approved_resolution(
    knowledge_session: Session, platform_context: TenantContext
) -> None:
    storage = InMemoryObjectStorage()
    service, _, source, first = create_fixture(
        knowledge_session,
        platform_context,
        storage,
        effective_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        effective_until=datetime(2025, 12, 31, tzinfo=timezone.utc),
    )
    second = service.create_version(
        knowledge_session,
        platform_context,
        source_id=source.id,
        version_label="2026.2",
        content_mode="ORIGINAL_SYNTHETIC",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        supersedes_document_version_id=first.id,
    )
    for version, filename, content in (
        (first, "v1.txt", b"synthetic knowledge v1"),
        (second, "v2.txt", b"synthetic knowledge v2"),
    ):
        reviewer = allow_source(service, knowledge_session, platform_context, version, content)
        service.register_file(
            knowledge_session,
            platform_context,
            version_id=version.id,
            filename=filename,
            content=content,
            declared_mime_type="text/plain",
        )
        service.ingest(knowledge_session, platform_context, version_id=version.id)
        service.approve(
            knowledge_session, reviewer, version_id=version.id, expected_sha256=version.sha256
        )
    service.supersede(
        knowledge_session,
        platform_context,
        old_version_id=first.id,
        new_version_id=second.id,
    )
    assert service.get_current_approved_versions(knowledge_session, source_id=source.id) == [second]
    assert service.get_approved_versions_as_of(
        knowledge_session,
        source_id=source.id,
        at=datetime(2025, 6, 1, tzinfo=timezone.utc),
    ) == [first]
    assert service.get_approved_versions_as_of(
        knowledge_session,
        source_id=source.id,
        at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    ) == [second]


def test_failed_ingestion_is_not_approvable_and_can_retry(
    knowledge_session: Session, platform_context: TenantContext
) -> None:
    class FailingParser:
        name = "synthetic-failing"
        version = "1"

        def can_parse(self, mime_type: str) -> bool:
            return mime_type == "text/plain"

        def parse(self, content: bytes):
            raise ValueError("synthetic parser failure")

    storage = InMemoryObjectStorage()
    service, _, _, version = create_fixture(knowledge_session, platform_context, storage)
    service.parsers = ParserRegistry([FailingParser()])
    content = b"TEST FIXTURE"
    reviewer = allow_source(service, knowledge_session, platform_context, version, content)
    service.register_file(
        knowledge_session,
        platform_context,
        version_id=version.id,
        filename="bad.txt",
        content=content,
        declared_mime_type="text/plain",
    )
    job = service.ingest(knowledge_session, platform_context, version_id=version.id)
    assert job.status.value == "FAILED"
    assert version.status is KnowledgeVersionStatus.REJECTED
    with pytest.raises(ValueError, match="only successfully ingested"):
        service.approve(
            knowledge_session, reviewer, version_id=version.id, expected_sha256=version.sha256
        )
    assert knowledge_session.scalar(
        select(KnowledgeIngestionJob).where(KnowledgeIngestionJob.id == job.id)
    )
    service.parsers = ParserRegistry()
    recovered = service.ingest(knowledge_session, platform_context, version_id=version.id)
    assert recovered.status.value == "SUCCEEDED" and recovered.id != job.id
    assert (
        service.ingest(knowledge_session, platform_context, version_id=version.id).id
        == recovered.id
    )
