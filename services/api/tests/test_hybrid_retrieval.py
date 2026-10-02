import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.authorization import TenantContext
from app.config import Settings
from app.knowledge.chunking import CHUNKER_NAME, CHUNKER_VERSION, chunk_page
from app.knowledge.embeddings import DeterministicTestEmbeddingProvider
from app.models import (
    Base,
    KnowledgeAuthority,
    KnowledgeDocumentPage,
    KnowledgeDocumentVersion,
    KnowledgeIngestionJob,
    KnowledgeLegalMetadata,
    KnowledgeSource,
)
from app.models.enums import (
    AssessmentFamily,
    ExtractionStatus,
    KnowledgeAuthorityType,
    KnowledgeDocumentType,
    KnowledgeIngestionStatus,
    KnowledgeScope,
    KnowledgeVersionStatus,
)
from app.services.retrieval import KnowledgeIndexService, RetrievalScope, RetrievalService


@pytest.fixture
def retrieval_session() -> Session:
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


@pytest.fixture
def platform_context() -> TenantContext:
    return TenantContext(
        user_id=uuid.uuid4(),
        organisation_id=None,
        membership_id=None,
        platform_permissions=frozenset({"standards.document.manage", "standards.document.read"}),
    )


def _indexed_fixture(
    session: Session, context: TenantContext, provider: str
) -> KnowledgeDocumentVersion:
    now = datetime.now(timezone.utc)
    authority = KnowledgeAuthority(
        code=provider,
        name=provider,
        authority_type=KnowledgeAuthorityType.EXAM_PROVIDER,
        created_at=now,
        updated_at=now,
    )
    session.add(authority)
    session.flush()
    source = KnowledgeSource(
        authority_id=authority.id,
        source_code=f"{provider}_TEST",
        title="TEST FIXTURE — NOT OFFICIAL MATERIAL",
        document_type=KnowledgeDocumentType.STANDARD,
        scope=KnowledgeScope.PLATFORM_SHARED,
        status="ACTIVE",
        assessment_family=AssessmentFamily.LANGUAGE,
        provider_code=provider,
        language="de",
        cefr_level="B1",
        created_at=now,
        updated_at=now,
    )
    session.add(source)
    session.flush()
    version = KnowledgeDocumentVersion(
        source_id=source.id,
        version_label="1",
        status=KnowledgeVersionStatus.APPROVED,
        created_at=now,
        created_by_user_id=context.user_id,
        approved_at=now,
        approved_by_user_id=context.user_id,
    )
    session.add(version)
    session.flush()
    session.add(KnowledgeLegalMetadata(document_version_id=version.id))
    job = KnowledgeIngestionJob(
        document_version_id=version.id,
        status=KnowledgeIngestionStatus.SUCCEEDED,
        created_by_user_id=context.user_id,
        created_at=now,
    )
    session.add(job)
    session.flush()
    session.add(
        KnowledgeDocumentPage(
            document_version_id=version.id,
            ingestion_job_id=job.id,
            page_number=1,
            text_content=f"TEST FIXTURE — NOT OFFICIAL MATERIAL {provider} reading",
            normalized_text=f"TEST FIXTURE — NOT OFFICIAL MATERIAL {provider} reading",
            content_hash=(provider.lower() * 64)[:64],
            character_count=50,
            extraction_status=ExtractionStatus.EXTRACTED,
            parser_name="test",
            parser_version="1",
            created_at=now,
        )
    )
    session.commit()
    return version


def test_page_local_chunking_is_deterministic_and_non_transforming() -> None:
    text = "TEST FIXTURE — NOT OFFICIAL MATERIAL\n\nParagraph one.\n\nParagraph two."
    first = chunk_page(text, page_number=3, target_size=20, max_size=40, overlap=4)
    second = chunk_page(text, page_number=3, target_size=20, max_size=40, overlap=4)
    assert first == second
    assert all(item.page_number == 3 for item in first)
    assert "TEST FIXTURE" in " ".join(item.text_content for item in first)
    assert CHUNKER_NAME == "prufai_page_chunker"
    assert CHUNKER_VERSION == "1.0"
    assert all(len(item.content_hash) == 64 for item in first)


def test_deterministic_embeddings_are_reproducible_and_dimension_checked() -> None:
    provider = DeterministicTestEmbeddingProvider()
    assert provider.embed_query("same") == provider.embed_query("same")
    assert len(provider.embed_query("same")) == 384
    with pytest.raises(ValueError):
        DeterministicTestEmbeddingProvider(dimension=3)


def test_retrieval_scope_rejects_cefr_for_lid() -> None:
    from app.models.enums import AssessmentFamily
    from app.services.retrieval import RetrievalScope

    with pytest.raises(ValueError, match="CEFR"):
        RetrievalScope(assessment_family=AssessmentFamily.INTEGRATION_KNOWLEDGE, cefr_level="B1")


def test_provider_isolation_and_idempotent_indexing(
    retrieval_session: Session, platform_context: TenantContext
) -> None:
    telc = _indexed_fixture(retrieval_session, platform_context, "TELC")
    _indexed_fixture(retrieval_session, platform_context, "GOETHE")
    settings = Settings(environment="test", knowledge_embedding_backend="deterministic")
    indexer = KnowledgeIndexService(settings)
    first = indexer.index(retrieval_session, platform_context, version_id=telc.id)
    second = indexer.index(retrieval_session, platform_context, version_id=telc.id)
    assert first.index_status.value == "READY"
    assert second.index_status.value == "READY"
    results = RetrievalService(indexer).search_lexical(
        retrieval_session,
        "reading",
        RetrievalScope(provider_codes=("TELC",)),
    )
    assert results and {result.authority_code for result in results} == {"TELC"}
    assert len(list(retrieval_session.scalars(select(KnowledgeDocumentPage)))) == 2
