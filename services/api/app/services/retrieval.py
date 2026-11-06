from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, cast

from sqlalchemy import ColumnElement, func, or_, select, text
from sqlalchemy.orm import Session, aliased

from app.audit import AuditService
from app.auth.security import utcnow
from app.authorization import AuthorizationError, TenantContext
from app.config import Settings, get_settings
from app.knowledge.chunking import CHUNKER_NAME, CHUNKER_VERSION, chunk_page
from app.knowledge.embeddings import (
    DeterministicTestEmbeddingProvider,
    EmbeddingError,
    EmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    validate_dimension,
)
from app.models import (
    KnowledgeAuthority,
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeDocumentPage,
    KnowledgeDocumentVersion,
    KnowledgeIndexJob,
    KnowledgeIndexProfile,
    KnowledgeLegalMetadata,
    KnowledgeSource,
)
from app.models.enums import (
    AssessmentFamily,
    KnowledgeIndexJobStatus,
    KnowledgeIndexStatus,
    KnowledgeScope,
    KnowledgeVersionStatus,
)
from app.services.knowledge import content_rights_permit


def _typed_score_map(rows: Iterable[tuple[object, object]]) -> dict[uuid.UUID, float]:
    """Normalize SQLAlchemy numeric result rows at the database boundary."""
    scores: dict[uuid.UUID, float] = {}
    for raw_id, raw_score in rows:
        if not isinstance(raw_id, uuid.UUID):
            raise TypeError("retrieval result key is not a UUID")
        if not isinstance(raw_score, (int, float)):
            raise TypeError("retrieval score is not numeric")
        scores[raw_id] = float(raw_score)
    return scores


@dataclass(frozen=True)
class RetrievalScope:
    assessment_family: Optional[AssessmentFamily] = None
    provider_codes: tuple[str, ...] = ()
    authority_codes: tuple[str, ...] = ()
    source_ids: tuple[uuid.UUID, ...] = ()
    exam_family: Optional[str] = None
    language: Optional[str] = None
    cefr_level: Optional[str] = None
    skill: Optional[str] = None
    document_type: Optional[str] = None
    as_of_date: Optional[datetime] = None
    include_shared_authorities: tuple[str, ...] = ()
    document_version_ids: tuple[uuid.UUID, ...] = ()
    expected_document_checksum: str | None = None

    def __post_init__(self) -> None:
        if self.cefr_level and self.cefr_level not in {"A1", "A2", "B1", "B2", "C1", "C2"}:
            raise ValueError("unsupported CEFR level")
        if self.assessment_family is AssessmentFamily.INTEGRATION_KNOWLEDGE and self.cefr_level:
            raise ValueError("LiD/integration knowledge cannot use a CEFR filter")
        if self.expected_document_checksum is not None and len(self.document_version_ids) != 1:
            raise ValueError("a checksum requires exactly one document version")


@dataclass
class CitationReadyResult:
    chunk_id: uuid.UUID
    text: str
    authority_id: uuid.UUID
    authority_code: str
    authority_name: str
    source_id: uuid.UUID
    source_title: str
    document_version_id: uuid.UUID
    version_label: str
    document_checksum: Optional[str]
    page_number: int
    section_title: Optional[str]
    assessment_family: str
    exam_family: Optional[str]
    language: Optional[str]
    cefr_level: Optional[str]
    skill: Optional[str]
    publication_date: Optional[datetime]
    effective_from: Optional[datetime]
    effective_until: Optional[datetime]
    source_url: Optional[str]
    legal_summary: dict[str, object]
    chunker_name: str
    chunker_version: str
    embedding_profile: Optional[str] = None
    lexical_rank: Optional[int] = None
    lexical_score: Optional[float] = None
    vector_rank: Optional[int] = None
    vector_score: Optional[float] = None
    hybrid_rank: Optional[int] = None
    fusion_score: Optional[float] = None
    rerank_rank: Optional[int] = None
    rerank_score: Optional[float] = None
    reranking_profile: Optional[str] = None
    matched_lexical: bool = False
    matched_vector: bool = False


class KnowledgeIndexService:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    def provider(self) -> EmbeddingProvider:
        if self.settings.knowledge_embedding_backend == "deterministic":
            if self.settings.environment == "production":
                raise EmbeddingError("production embeddings cannot use test provider")
            return DeterministicTestEmbeddingProvider(self.settings.knowledge_embedding_dimension)
        return OpenAICompatibleEmbeddingProvider(
            api_key=self.settings.knowledge_embedding_api_key,
            model=self.settings.knowledge_embedding_model,
            dimension=self.settings.knowledge_embedding_dimension,
            timeout_seconds=self.settings.knowledge_embedding_timeout_seconds,
        )

    def require_synthetic_execution(self) -> None:
        if (
            self.settings.environment == "production"
            or self.settings.knowledge_embedding_backend != "deterministic"
        ):
            raise AuthorizationError("STANDARDS_SYNTHETIC_TEST_EMBEDDINGS_REQUIRED")

    def _profile_key(self) -> str:
        return ":".join(
            [
                CHUNKER_NAME,
                CHUNKER_VERSION,
                self.settings.knowledge_embedding_backend,
                self.settings.knowledge_embedding_model,
                str(self.settings.knowledge_embedding_dimension),
            ]
        )

    def active_profile(self, session: Session) -> KnowledgeIndexProfile | None:
        return session.scalar(
            select(KnowledgeIndexProfile).where(
                KnowledgeIndexProfile.profile_id == self._profile_key(),
                KnowledgeIndexProfile.active.is_(True),
            )
        )

    def ensure_profile(self, session: Session) -> KnowledgeIndexProfile:
        profile_id = self._profile_key()
        lookup = select(KnowledgeIndexProfile).where(KnowledgeIndexProfile.profile_id == profile_id)
        profile = session.scalar(lookup)
        if profile is None and session.get_bind().dialect.name == "postgresql":
            key = int.from_bytes(
                hashlib.sha256(profile_id.encode()).digest()[:4], "big", signed=True
            )
            session.execute(text("SELECT pg_advisory_xact_lock(4130037, :key)"), {"key": key})
            profile = session.scalar(lookup)
        if profile is None:
            provider = self.provider()
            profile = KnowledgeIndexProfile(
                profile_id=profile_id,
                chunker_name=CHUNKER_NAME,
                chunker_version=CHUNKER_VERSION,
                chunk_configuration=json.dumps(
                    {"target_size": 900, "max_size": 1200, "overlap": 80}
                ),
                embedding_provider=provider.provider,
                embedding_model=provider.model,
                embedding_dimension=provider.dimension,
                similarity_metric="cosine",
                active=True,
                created_at=utcnow(),
            )
            session.add(profile)
            session.flush()
        return profile

    def index(
        self,
        session: Session,
        context: TenantContext,
        *,
        version_id: uuid.UUID,
        profile_id: Optional[uuid.UUID] = None,
        expected_document_checksum: str | None = None,
    ) -> KnowledgeIndexJob:
        if (
            context.organisation_id is not None
            or "standards.document.manage" not in context.platform_permissions
        ):
            raise AuthorizationError("platform standards document authority required")
        version = session.get(
            KnowledgeDocumentVersion, version_id, with_for_update=True, populate_existing=True
        )
        if version is None:
            raise ValueError("document version does not exist")
        if version.status is not KnowledgeVersionStatus.APPROVED:
            raise ValueError("only approved knowledge can be indexed")
        if not _indexable_content(version):
            raise AuthorizationError("STANDARDS_CONTENT_RIGHTS_REQUIRED")
        if expected_document_checksum is not None and expected_document_checksum != version.sha256:
            raise ValueError("STANDARDS_SOURCE_CHECKSUM_MISMATCH")
        self.require_synthetic_execution()
        profile = self.ensure_profile(session)
        provider = self.provider()
        if (
            profile_id is not None
            and profile_id != profile.id
            or not profile.active
            or profile.chunker_name != CHUNKER_NAME
            or profile.chunker_version != CHUNKER_VERSION
            or profile.embedding_provider != provider.provider
            or profile.embedding_model != provider.model
            or profile.embedding_dimension != provider.dimension
        ):
            raise ValueError("configured index profile pin does not match")
        existing = session.scalar(
            select(KnowledgeIndexJob)
            .where(
                KnowledgeIndexJob.document_version_id == version.id,
                KnowledgeIndexJob.status == KnowledgeIndexJobStatus.SUCCEEDED,
                KnowledgeIndexJob.index_status == KnowledgeIndexStatus.READY,
            )
            .order_by(KnowledgeIndexJob.created_at.desc())
            .limit(1)
        )
        if existing is not None:
            if existing.profile_id != profile.id:
                raise ValueError("a new index profile requires a governed source successor")
            return existing
        if session.scalar(
            select(KnowledgeChunk.id)
            .where(KnowledgeChunk.document_version_id == version.id)
            .limit(1)
        ):
            raise ValueError("existing unsealed source chunks cannot be replaced")
        job = KnowledgeIndexJob(
            document_version_id=version.id,
            profile_id=profile.id,
            status=KnowledgeIndexJobStatus.RUNNING,
            index_status=KnowledgeIndexStatus.INDEXING,
            started_at=utcnow(),
            created_at=utcnow(),
            created_by_user_id=context.user_id,
        )
        session.add(job)
        session.flush()
        AuditService.record_security(
            session,
            event_type="standards.indexing.started",
            entity_type="knowledge_index_job",
            entity_id=str(job.id),
            actor_id=context.user_id,
            metadata={"document_version_id": str(version.id), "profile_id": profile.profile_id},
        )
        try:
            with session.begin_nested():
                count = self._persist_chunks(session, version, profile, provider)
                job.status = KnowledgeIndexJobStatus.SUCCEEDED
                job.index_status = KnowledgeIndexStatus.READY
                job.chunks_created = count
                job.embeddings_created = count
                job.finished_at = utcnow()
                AuditService.record_security(
                    session,
                    event_type="standards.indexing.succeeded",
                    entity_type="knowledge_index_job",
                    entity_id=str(job.id),
                    actor_id=context.user_id,
                    metadata={"chunks": count, "embeddings": count},
                )
        except Exception as exc:
            job.status = KnowledgeIndexJobStatus.FAILED
            job.index_status = KnowledgeIndexStatus.FAILED
            job.finished_at = utcnow()
            job.error_code = type(exc).__name__[:80]
            job.safe_error_message = "knowledge indexing failed"
            AuditService.record_security(
                session,
                event_type="standards.indexing.failed",
                entity_type="knowledge_index_job",
                entity_id=str(job.id),
                actor_id=context.user_id,
                metadata={"error_code": job.error_code},
            )
        session.commit()
        return job

    @staticmethod
    def _persist_chunks(
        session: Session,
        version: KnowledgeDocumentVersion,
        profile: KnowledgeIndexProfile,
        provider: EmbeddingProvider,
    ) -> int:
        pages = list(
            session.scalars(
                select(KnowledgeDocumentPage)
                .where(KnowledgeDocumentPage.document_version_id == version.id)
                .order_by(KnowledgeDocumentPage.page_number)
            )
        )
        chunks = [
            KnowledgeChunk(
                document_version_id=version.id,
                document_page_id=page.id,
                chunk_index=draft.chunk_index,
                text_content=draft.text_content,
                content_hash=draft.content_hash,
                character_start=draft.character_start,
                character_end=draft.character_end,
                section_title=draft.section_title,
                token_count=draft.token_count,
                chunker_name=CHUNKER_NAME,
                chunker_version=CHUNKER_VERSION,
                created_at=utcnow(),
            )
            for page in pages
            for draft in chunk_page(page.normalized_text, page_number=page.page_number)
        ]
        if not chunks:
            raise ValueError("no versioned source text to index")
        session.add_all(chunks)
        session.flush()
        vectors = provider.embed_documents([chunk.text_content for chunk in chunks])
        if len(vectors) != len(chunks):
            raise EmbeddingError("embedding provider returned an incomplete batch")
        for chunk, vector in zip(chunks, vectors, strict=True):
            values = validate_dimension(vector, profile.embedding_dimension)
            session.add(
                KnowledgeChunkEmbedding(
                    chunk_id=chunk.id,
                    profile_id=profile.id,
                    embedding_provider=profile.embedding_provider,
                    embedding_model=profile.embedding_model,
                    embedding_dimension=len(values),
                    embedding=values,
                    content_hash=chunk.content_hash,
                    created_at=utcnow(),
                )
            )
        return len(chunks)


def _indexable_content(version: KnowledgeDocumentVersion) -> bool:
    legal = version.legal_metadata
    return (
        content_rights_permit(version) and legal is not None and legal.ai_processing_allowed is True
    )


class RetrievalService:
    def __init__(self, index_service: Optional[KnowledgeIndexService] = None) -> None:
        self.index_service = index_service or KnowledgeIndexService()

    def _base_versions(
        self, session: Session, scope: RetrievalScope
    ) -> list[KnowledgeDocumentVersion]:
        point = scope.as_of_date or utcnow()
        if point.tzinfo is None:
            point = point.replace(tzinfo=timezone.utc)
        successor = aliased(KnowledgeDocumentVersion)
        query = (
            select(KnowledgeDocumentVersion)
            .join(KnowledgeSource)
            .join(KnowledgeAuthority)
            .where(
                KnowledgeSource.scope == KnowledgeScope.PLATFORM_SHARED,
                KnowledgeSource.status == "ACTIVE",
                KnowledgeAuthority.status == "ACTIVE",
                KnowledgeDocumentVersion.content_mode == "ORIGINAL_SYNTHETIC",
                or_(
                    KnowledgeDocumentVersion.effective_from.is_(None),
                    KnowledgeDocumentVersion.effective_from <= point,
                ),
                or_(
                    KnowledgeDocumentVersion.effective_until.is_(None),
                    KnowledgeDocumentVersion.effective_until > point,
                ),
                ~select(successor.id)
                .where(
                    successor.source_id == KnowledgeDocumentVersion.source_id,
                    successor.supersedes_document_version_id == KnowledgeDocumentVersion.id,
                    successor.approved_at <= point,
                    or_(successor.effective_from.is_(None), successor.effective_from <= point),
                )
                .exists(),
            )
        )
        if scope.as_of_date is None:
            query = query.where(KnowledgeDocumentVersion.status == KnowledgeVersionStatus.APPROVED)
        else:
            query = query.where(
                KnowledgeDocumentVersion.status.in_(
                    [KnowledgeVersionStatus.APPROVED, KnowledgeVersionStatus.SUPERSEDED]
                ),
                KnowledgeDocumentVersion.approved_at <= point,
            )
        if scope.document_version_ids:
            query = query.where(KnowledgeDocumentVersion.id.in_(scope.document_version_ids))
        if scope.expected_document_checksum is not None:
            query = query.where(KnowledgeDocumentVersion.sha256 == scope.expected_document_checksum)
        if scope.source_ids:
            query = query.where(KnowledgeSource.id.in_(scope.source_ids))
        if scope.assessment_family:
            query = query.where(KnowledgeSource.assessment_family == scope.assessment_family)
        if scope.provider_codes:
            query = query.where(KnowledgeSource.provider_code.in_(scope.provider_codes))
        if scope.authority_codes:
            query = query.where(KnowledgeAuthority.code.in_(scope.authority_codes))
        if scope.exam_family:
            query = query.where(KnowledgeSource.exam_family == scope.exam_family)
        if scope.language:
            query = query.where(KnowledgeSource.language == scope.language)
        if scope.cefr_level:
            query = query.where(KnowledgeSource.cefr_level == scope.cefr_level)
        if scope.skill:
            query = query.where(KnowledgeSource.skill == scope.skill)
        if scope.document_type:
            query = query.where(KnowledgeSource.document_type == scope.document_type)
        versions = session.scalars(
            query.order_by(KnowledgeDocumentVersion.id)
            .with_for_update(read=True, of=KnowledgeDocumentVersion)
            .execution_options(populate_existing=True)
        )
        return [version for version in versions if _indexable_content(version)]

    @staticmethod
    def _indexed_chunk(profile_id: uuid.UUID) -> tuple[ColumnElement[bool], ...]:
        return (
            KnowledgeChunkEmbedding.profile_id == profile_id,
            KnowledgeChunkEmbedding.content_hash == KnowledgeChunk.content_hash,
            select(KnowledgeIndexJob.id)
            .where(
                KnowledgeIndexJob.document_version_id == KnowledgeChunk.document_version_id,
                KnowledgeIndexJob.profile_id == profile_id,
                KnowledgeIndexJob.status == KnowledgeIndexJobStatus.SUCCEEDED,
                KnowledgeIndexJob.index_status == KnowledgeIndexStatus.READY,
            )
            .exists(),
        )

    def _results(
        self, session: Session, versions: list[KnowledgeDocumentVersion], profile_id: uuid.UUID
    ) -> list[CitationReadyResult]:
        if not versions:
            return []
        version_ids = [v.id for v in versions]
        rows = session.execute(
            select(
                KnowledgeChunk,
                KnowledgeDocumentPage,
                KnowledgeSource,
                KnowledgeAuthority,
                KnowledgeDocumentVersion,
                KnowledgeLegalMetadata,
            )
            .join(
                KnowledgeDocumentPage, KnowledgeDocumentPage.id == KnowledgeChunk.document_page_id
            )
            .join(
                KnowledgeDocumentVersion,
                KnowledgeDocumentVersion.id == KnowledgeChunk.document_version_id,
            )
            .join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocumentVersion.source_id)
            .join(KnowledgeAuthority, KnowledgeAuthority.id == KnowledgeSource.authority_id)
            .outerjoin(
                KnowledgeLegalMetadata,
                KnowledgeLegalMetadata.document_version_id == KnowledgeDocumentVersion.id,
            )
            .join(KnowledgeChunkEmbedding, KnowledgeChunkEmbedding.chunk_id == KnowledgeChunk.id)
            .where(
                KnowledgeChunk.document_version_id.in_(version_ids),
                *self._indexed_chunk(profile_id),
            )
        )
        output = []
        for chunk, page, source, authority, version, legal in rows:
            output.append(
                CitationReadyResult(
                    chunk_id=chunk.id,
                    text=chunk.text_content,
                    authority_id=authority.id,
                    authority_code=authority.code,
                    authority_name=authority.name,
                    source_id=source.id,
                    source_title=source.title,
                    document_version_id=version.id,
                    version_label=version.version_label,
                    document_checksum=version.sha256,
                    page_number=page.page_number,
                    section_title=chunk.section_title,
                    assessment_family=source.assessment_family.value,
                    exam_family=source.exam_family,
                    language=source.language,
                    cefr_level=source.cefr_level,
                    skill=source.skill,
                    publication_date=version.publication_date,
                    effective_from=version.effective_from,
                    effective_until=version.effective_until,
                    source_url=version.source_url,
                    legal_summary={
                        "copyright_status": legal.copyright_status if legal else "UNKNOWN",
                        "legal_review_status": legal.legal_review_status.value
                        if legal
                        else "NOT_REVIEWED",
                        "redistribution_allowed": legal.redistribution_allowed if legal else None,
                        "ai_processing_allowed": legal.ai_processing_allowed if legal else None,
                    },
                    chunker_name=chunk.chunker_name,
                    chunker_version=chunk.chunker_version,
                    embedding_profile=profile_id.hex,
                )
            )
        return output

    def search_lexical(
        self, session: Session, query: str, scope: RetrievalScope, limit: int = 10
    ) -> list[CitationReadyResult]:
        if not query.strip() or len(query) > 2000:
            raise ValueError("query must be 1-2000 characters")
        limit = min(max(limit, 1), 50)
        versions = self._base_versions(session, scope)
        if not versions:
            return []
        self.index_service.require_synthetic_execution()
        profile = self.index_service.active_profile(session)
        if profile is None:
            return []
        scores: dict[uuid.UUID, float] = {}
        if session.get_bind().dialect.name == "postgresql":
            lexical_score = func.ts_rank_cd(
                func.to_tsvector("simple", KnowledgeChunk.text_content),
                func.plainto_tsquery("simple", query),
            ).label("lexical_score")
            rows = session.execute(
                select(KnowledgeChunk.id, lexical_score)
                .join(
                    KnowledgeChunkEmbedding, KnowledgeChunkEmbedding.chunk_id == KnowledgeChunk.id
                )
                .where(
                    KnowledgeChunk.document_version_id.in_([item.id for item in versions]),
                    lexical_score > 0,
                    *self._indexed_chunk(profile.id),
                )
                .order_by(lexical_score.desc(), KnowledgeChunk.id)
                .limit(limit)
            )
            scores = _typed_score_map(cast(Iterable[tuple[object, object]], rows))
            results = [
                item
                for item in self._results(session, versions, profile.id)
                if item.chunk_id in scores
            ]
            results.sort(key=lambda item: scores[item.chunk_id], reverse=True)
        else:
            results = self._results(session, versions, profile.id)
        terms = {term.lower() for term in query.split() if term}
        ranked = sorted(
            results, key=lambda item: sum(term in item.text.lower() for term in terms), reverse=True
        )[:limit]
        for rank, result in enumerate(ranked, 1):
            result.lexical_rank = rank
            result.lexical_score = scores.get(
                result.chunk_id, float(sum(term in result.text.lower() for term in terms))
            )
            result.matched_lexical = result.lexical_score > 0
        return [result for result in ranked if result.matched_lexical]

    def search_vector(
        self, session: Session, query: str, scope: RetrievalScope, limit: int = 10
    ) -> list[CitationReadyResult]:
        versions = self._base_versions(session, scope)
        if not versions:
            return []
        self.index_service.require_synthetic_execution()
        profile = self.index_service.active_profile(session)
        if profile is None:
            return []
        query_vector = self.index_service.provider().embed_query(query)
        distances: dict[uuid.UUID, float] = {}
        if session.get_bind().dialect.name == "postgresql":
            distance = KnowledgeChunkEmbedding.embedding.cosine_distance(query_vector).label(
                "distance"
            )
            rows = session.execute(
                select(KnowledgeChunk.id, distance)
                .join(
                    KnowledgeChunkEmbedding, KnowledgeChunkEmbedding.chunk_id == KnowledgeChunk.id
                )
                .where(
                    KnowledgeChunk.document_version_id.in_([item.id for item in versions]),
                    *self._indexed_chunk(profile.id),
                )
                .order_by(distance.asc(), KnowledgeChunk.id)
                .limit(min(max(limit, 1), 50))
            )
            distances = _typed_score_map(cast(Iterable[tuple[object, object]], rows))
            ranked = [
                item
                for item in self._results(session, versions, profile.id)
                if item.chunk_id in distances
            ]
            ranked.sort(key=lambda item: distances[item.chunk_id])
        else:
            results = self._results(session, versions, profile.id)
            # SQLite tests use deterministic ordering; PostgreSQL uses pgvector above.
            ranked = sorted(
                results,
                key=lambda item: hashlib.sha256((query + str(item.chunk_id)).encode()).hexdigest(),
            )[: min(max(limit, 1), 50)]
        for rank, result in enumerate(ranked, 1):
            result.vector_rank = rank
            result.vector_score = (
                1.0 - distances[result.chunk_id] if result.chunk_id in distances else 1.0 / rank
            )
            result.matched_vector = True
        del query_vector
        return ranked

    def search_hybrid(
        self, session: Session, query: str, scope: RetrievalScope, limit: int = 10
    ) -> list[CitationReadyResult]:
        lexical = self.search_lexical(session, query, scope, limit * 2)
        vector = self.search_vector(session, query, scope, limit * 2)
        by_id: dict[uuid.UUID, CitationReadyResult] = {}
        for result in lexical + vector:
            by_id.setdefault(result.chunk_id, result)
        lexical_ranks = {result.chunk_id: index for index, result in enumerate(lexical, 1)}
        vector_ranks = {result.chunk_id: index for index, result in enumerate(vector, 1)}
        for result in by_id.values():
            if result.chunk_id in lexical_ranks:
                result.matched_lexical = True
                result.lexical_rank = lexical_ranks[result.chunk_id]
            if result.chunk_id in vector_ranks:
                result.matched_vector = True
                result.vector_rank = vector_ranks[result.chunk_id]
            result.fusion_score = (1 / (60 + (result.lexical_rank or 999))) + (
                1 / (60 + (result.vector_rank or 999))
            )
        ranked = sorted(by_id.values(), key=lambda item: item.fusion_score or 0, reverse=True)[
            : min(max(limit, 1), 50)
        ]
        for rank, result in enumerate(ranked, 1):
            result.hybrid_rank = rank
        return ranked
