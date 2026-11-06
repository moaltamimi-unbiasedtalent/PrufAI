import hashlib
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.authorization import AuthorizationError, TenantContext
from app.config import Settings
from app.knowledge.chunking import CHUNKER_NAME, CHUNKER_VERSION, chunk_page
from app.knowledge.embeddings import DeterministicTestEmbeddingProvider
from app.knowledge.storage import InMemoryObjectStorage
from app.models import (
    Base,
    KnowledgeDocumentPage,
    KnowledgeDocumentVersion,
)
from app.models.enums import (
    AssessmentFamily,
    KnowledgeAuthorityType,
    KnowledgeDocumentType,
)
from app.services.knowledge import KnowledgeRegistryService
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
        platform_permissions=frozenset(
            {
                "standards.authority.manage",
                "standards.source.manage",
                "standards.document.manage",
                "standards.document.read",
                "standards.document.review",
            }
        ),
    )


def _approved_fixture(
    session: Session,
    context: TenantContext,
    provider: str,
    *,
    previous: KnowledgeDocumentVersion | None = None,
) -> KnowledgeDocumentVersion:
    registry = KnowledgeRegistryService(storage=InMemoryObjectStorage())
    if previous is None:
        authority = registry.create_authority(
            session,
            context,
            code=f"{provider}_SYNTHETIC",
            name="PrüfAI synthetic testing authority",
            authority_type=KnowledgeAuthorityType.INTERNAL,
        )
        source_id = registry.create_source(
            session,
            context,
            authority_id=authority.id,
            source_code=f"{provider}_SYNTHETIC_TEST",
            title="Original synthetic test material",
            document_type=KnowledgeDocumentType.STANDARD,
            assessment_family=AssessmentFamily.LANGUAGE,
            provider_code=provider,
            language="de",
            cefr_level="B1",
        ).id
    else:
        source_id = previous.source_id
    version = registry.create_version(
        session,
        context,
        source_id=source_id,
        version_label="2" if previous else "1",
        content_mode="ORIGINAL_SYNTHETIC",
        supersedes_document_version_id=previous.id if previous else None,
    )
    content = f"Original synthetic fixture for {provider} reading {version.version_label}".encode()
    digest = hashlib.sha256(content).hexdigest()
    reviewer = replace(context, user_id=uuid.uuid4())
    assert version.metadata_checksum is not None
    registry.review_rights(
        session,
        reviewer,
        version_id=version.id,
        expected_metadata_checksum=version.metadata_checksum,
        decision="ALLOW_SYNTHETIC_INGESTION",
        basis_reference="Unit test original synthetic fixture",
        rights_holder="PrüfAI synthetic testing authority",
        licence_type="Internal test use",
        retention_policy="Discard with unit test",
        expected_sha256=digest,
    )
    registry.register_file(
        session, context, version_id=version.id, filename="synthetic.txt", content=content
    )
    registry.ingest(session, context, version_id=version.id)
    return registry.approve(session, reviewer, version_id=version.id, expected_sha256=digest)


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
    telc = _approved_fixture(retrieval_session, platform_context, "TELC")
    goethe = _approved_fixture(retrieval_session, platform_context, "GOETHE")
    settings = Settings(environment="test", knowledge_embedding_backend="deterministic")
    indexer = KnowledgeIndexService(settings)
    first = indexer.index(retrieval_session, platform_context, version_id=telc.id)
    first_results = RetrievalService(indexer).search_hybrid(
        retrieval_session, "reading", RetrievalScope(document_version_ids=(telc.id,))
    )
    second = indexer.index(retrieval_session, platform_context, version_id=telc.id)
    indexer.index(retrieval_session, platform_context, version_id=goethe.id)
    assert first.index_status.value == "READY"
    assert second.index_status.value == "READY"
    assert first.id == second.id
    results = RetrievalService(indexer).search_lexical(
        retrieval_session,
        "reading",
        RetrievalScope(provider_codes=("TELC",)),
    )
    assert results and {result.authority_code for result in results} == {"TELC_SYNTHETIC"}
    hybrid = RetrievalService(indexer).search_hybrid(
        retrieval_session,
        "reading",
        RetrievalScope(provider_codes=("TELC",)),
    )
    assert hybrid and hybrid[0].hybrid_rank == 1
    assert hybrid[0].matched_lexical and hybrid[0].matched_vector
    assert hybrid[0].chunk_id == first_results[0].chunk_id
    assert len(list(retrieval_session.scalars(select(KnowledgeDocumentPage)))) == 2


def test_exact_pin_never_substitutes_a_successor_or_uses_external_embedding(
    retrieval_session: Session, platform_context: TenantContext
) -> None:
    old = _approved_fixture(retrieval_session, platform_context, "SYNTHETIC")
    indexer = KnowledgeIndexService(Settings(environment="test"))
    indexer.index(retrieval_session, platform_context, version_id=old.id)
    historic = datetime.now(timezone.utc)
    current = _approved_fixture(retrieval_session, platform_context, "SYNTHETIC", previous=old)
    indexer.index(retrieval_session, platform_context, version_id=current.id)
    service = RetrievalService(indexer)
    assert (
        service.search_hybrid(
            retrieval_session, "reading", RetrievalScope(document_version_ids=(old.id,))
        )
        == []
    )
    assert (
        service.search_hybrid(
            retrieval_session,
            "reading",
            RetrievalScope(document_version_ids=(current.id,), expected_document_checksum="0" * 64),
        )
        == []
    )
    results = service.search_hybrid(
        retrieval_session,
        "reading",
        RetrievalScope(document_version_ids=(old.id,), as_of_date=historic),
    )
    assert [result.document_version_id for result in results] == [old.id]
    external = KnowledgeIndexService(
        Settings(environment="test", knowledge_embedding_backend="openai")
    )
    with pytest.raises(AuthorizationError, match="TEST_EMBEDDINGS_REQUIRED"):
        RetrievalService(external).search_vector(
            retrieval_session, "reading", RetrievalScope(document_version_ids=(current.id,))
        )


def test_shared_authorities_are_explicit() -> None:
    from app.services.retrieval import RetrievalScope

    scope = RetrievalScope(provider_codes=("TELC",), include_shared_authorities=("CEFR",))
    assert scope.provider_codes == ("TELC",)
    assert scope.include_shared_authorities == ("CEFR",)
