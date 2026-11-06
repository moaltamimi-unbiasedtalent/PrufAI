from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError
from app.config import Settings, get_settings
from app.db import get_db_session
from app.knowledge.citations import Citation, CitationBuilder
from app.knowledge.embeddings import EmbeddingError
from app.knowledge.reranking import RerankerError
from app.models.enums import AssessmentFamily
from app.services.evidence import EvidenceRetrievalService
from app.services.retrieval import (
    CitationReadyResult,
    KnowledgeIndexService,
    RetrievalScope,
    RetrievalService,
)

router = APIRouter(prefix="/api/v1/platform/standards/retrieval", tags=["standards-retrieval"])
logger = logging.getLogger(__name__)


class RetrievalMode(str, Enum):
    LEXICAL = "LEXICAL"
    VECTOR = "VECTOR"
    HYBRID = "HYBRID"
    RERANKED_HYBRID = "RERANKED_HYBRID"


class RetrievalScopeRequest(BaseModel):
    assessment_family: Optional[AssessmentFamily] = None
    provider_codes: list[str] = Field(default_factory=list, max_length=10)
    authority_codes: list[str] = Field(default_factory=list, max_length=10)
    source_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    document_version_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    expected_document_checksum: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    exam_family: Optional[str] = Field(default=None, max_length=120)
    language: Optional[str] = Field(default=None, max_length=20)
    cefr_level: Optional[str] = Field(default=None, max_length=2)
    skill: Optional[str] = Field(default=None, max_length=80)
    document_type: Optional[str] = Field(default=None, max_length=40)
    as_of_date: Optional[datetime] = None
    include_shared_authorities: list[str] = Field(default_factory=list, max_length=10)


class RetrievalSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    scope: RetrievalScopeRequest = Field(default_factory=RetrievalScopeRequest)
    mode: RetrievalMode = RetrievalMode.HYBRID
    limit: int = Field(default=10, ge=1, le=50)


class IndexRequest(BaseModel):
    document_version_id: uuid.UUID
    profile_id: Optional[uuid.UUID] = None
    expected_document_checksum: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def _scope(request: RetrievalScopeRequest) -> RetrievalScope:
    return RetrievalScope(
        assessment_family=request.assessment_family,
        provider_codes=tuple(request.provider_codes),
        authority_codes=tuple(request.authority_codes + request.include_shared_authorities),
        source_ids=tuple(request.source_ids),
        document_version_ids=tuple(request.document_version_ids),
        expected_document_checksum=request.expected_document_checksum,
        exam_family=request.exam_family,
        language=request.language,
        cefr_level=request.cefr_level,
        skill=request.skill,
        document_type=request.document_type,
        as_of_date=request.as_of_date,
        include_shared_authorities=tuple(request.include_shared_authorities),
    )


def _safe_evidence(result: CitationReadyResult, citation: Citation) -> dict[str, object]:
    projected = asdict(result)
    if not citation.text_display_allowed:
        projected.pop("text")
        projected.pop("section_title")
        projected.pop("source_url")
    return projected


@router.post("/search")
def search(
    payload: RetrievalSearchRequest = Body(...),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    context = principal.tenant_context
    if (
        context is None
        or context.organisation_id is not None
        or "standards.document.read" not in context.platform_permissions
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="platform authority required"
        )
    try:
        request_id = uuid.uuid4()
        logger.info(
            "standards.retrieval.request",
            extra={
                "request_id": str(request_id),
                "query_sha256": hashlib.sha256(payload.query.encode()).hexdigest(),
                "query_length": len(payload.query),
                "mode": payload.mode.value,
                "limit": payload.limit,
            },
        )
        service = RetrievalService(KnowledgeIndexService(settings))
        scope = _scope(payload.scope)
        if payload.mode is RetrievalMode.LEXICAL:
            results = service.search_lexical(session, payload.query, scope, payload.limit)
        elif payload.mode is RetrievalMode.VECTOR:
            results = service.search_vector(session, payload.query, scope, payload.limit)
        elif payload.mode is RetrievalMode.RERANKED_HYBRID:
            evidence = EvidenceRetrievalService(retrieval=service, settings=settings).search(
                session, payload.query, scope, payload.limit
            )
            return {
                "request_id": str(request_id),
                "mode": payload.mode.value,
                "execution_class": "TEST_ONLY",
                "results": [
                    {"evidence": _safe_evidence(result, citation), "citation": asdict(citation)}
                    for result, citation in evidence
                ],
            }
        else:
            results = service.search_hybrid(session, payload.query, scope, payload.limit)
    except AuthorizationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (EmbeddingError, RerankerError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="retrieval backend unavailable",
        ) from exc
    citations = CitationBuilder()
    return {
        "request_id": str(request_id),
        "mode": payload.mode.value,
        "execution_class": "TEST_ONLY",
        "results": [
            {**_safe_evidence(result, citation), "citation": asdict(citation)}
            for result in results
            for citation in (citations.build(result),)
        ],
    }


@router.post("/index")
def index_document(
    payload: IndexRequest = Body(...),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    context = principal.tenant_context
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="platform authority required"
        )
    try:
        job = KnowledgeIndexService(settings).index(
            session,
            context,
            version_id=payload.document_version_id,
            profile_id=payload.profile_id,
            expected_document_checksum=payload.expected_document_checksum,
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except EmbeddingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="embedding backend unavailable",
        ) from exc
    return {
        "id": str(job.id),
        "status": job.status.value,
        "index_status": job.index_status.value,
        "profile_id": str(job.profile_id),
        "chunks_created": job.chunks_created,
        "embeddings_created": job.embeddings_created,
        "execution_class": "TEST_ONLY",
    }
