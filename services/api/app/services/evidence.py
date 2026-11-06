from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError
from app.config import Settings, get_settings
from app.knowledge.citations import Citation, CitationBuilder
from app.knowledge.reranking import Reranker, RerankingProfile, build_reranker
from app.models.retrieval import KnowledgeRerankingProfile
from app.services.retrieval import (
    CitationReadyResult,
    KnowledgeIndexService,
    RetrievalScope,
    RetrievalService,
)


class EvidenceRetrievalService:
    def __init__(
        self,
        retrieval: Optional[RetrievalService] = None,
        reranker: Optional[Reranker] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.retrieval = retrieval or RetrievalService(KnowledgeIndexService(self.settings))
        if (
            self.settings.environment == "production"
            or self.settings.knowledge_reranker_backend != "deterministic"
            or (reranker is not None and reranker.provider != "test-deterministic")
        ):
            raise AuthorizationError("STANDARDS_SYNTHETIC_TEST_RERANKER_REQUIRED")
        self.reranker = reranker or build_reranker(self.settings)
        self.citations = CitationBuilder()

    def reranking_profile(self, final_limit: int = 10) -> RerankingProfile:
        return RerankingProfile(
            profile_id="deterministic-reranker-v1"
            if self.reranker.provider == "test-deterministic"
            else "configured-reranker-v1",
            name="PrüfAI retrieval reranker",
            version="1.0",
            provider=self.reranker.provider,
            model=self.reranker.model,
            candidate_limit=50,
            final_limit=final_limit,
        )

    def search(
        self, session: Session, query: str, scope: RetrievalScope, limit: int = 10
    ) -> List[tuple[CitationReadyResult, Citation]]:
        candidates = self.retrieval.search_hybrid(session, query, scope, min(limit * 5, 50))
        if not candidates:
            return []
        profile = self.reranking_profile(limit)
        self._persist_profile(session, profile)
        reranked = self.reranker.rerank(query, list(candidates), profile)
        for candidate in reranked:
            candidate.reranking_profile = profile.profile_id
        return [(candidate, self.citations.build(candidate)) for candidate in reranked]

    @staticmethod
    def _persist_profile(session: Session, profile: RerankingProfile) -> None:
        existing = session.scalar(
            select(KnowledgeRerankingProfile).where(
                KnowledgeRerankingProfile.profile_id == profile.profile_id
            )
        )
        if existing is not None:
            return
        session.add(
            KnowledgeRerankingProfile(
                profile_id=profile.profile_id,
                name=profile.name,
                version=profile.version,
                provider=profile.provider,
                model=profile.model,
                candidate_limit=profile.candidate_limit,
                final_limit=profile.final_limit,
                configuration=json.dumps(profile.configuration or {}, sort_keys=True),
                status="ACTIVE",
                created_at=datetime.now(timezone.utc),
            )
        )
        session.flush()
