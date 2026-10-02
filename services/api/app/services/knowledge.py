from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.auth.security import utcnow
from app.authorization import AuthorizationService, TenantContext
from app.knowledge.parsers import ParserRegistry
from app.knowledge.storage import (
    InMemoryObjectStorage,
    ObjectStorage,
    build_storage_key,
    detect_mime_type,
)
from app.models import (
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

KNOWN_AUTHORITY_CODES = {
    "COUNCIL_OF_EUROPE",
    "BAMF",
    "GAST",
    "GOETHE",
    "TELC",
    "PRUFAI",
}


def _valid_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must use http or https")
    return value


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


class KnowledgeLifecycleService:
    transitions = {
        KnowledgeVersionStatus.DRAFT: {
            KnowledgeVersionStatus.INGESTING,
            KnowledgeVersionStatus.REJECTED,
        },
        KnowledgeVersionStatus.INGESTING: {
            KnowledgeVersionStatus.INGESTED,
            KnowledgeVersionStatus.REJECTED,
        },
        KnowledgeVersionStatus.INGESTED: {
            KnowledgeVersionStatus.REVIEW_REQUIRED,
            KnowledgeVersionStatus.REJECTED,
        },
        KnowledgeVersionStatus.REVIEW_REQUIRED: {
            KnowledgeVersionStatus.APPROVED,
            KnowledgeVersionStatus.REJECTED,
            KnowledgeVersionStatus.INGESTING,
        },
        KnowledgeVersionStatus.APPROVED: {
            KnowledgeVersionStatus.SUPERSEDED,
            KnowledgeVersionStatus.RETIRED,
        },
        KnowledgeVersionStatus.SUPERSEDED: {KnowledgeVersionStatus.RETIRED},
        KnowledgeVersionStatus.REJECTED: {KnowledgeVersionStatus.INGESTING},
        KnowledgeVersionStatus.RETIRED: set(),
    }

    @classmethod
    def transition(cls, version: KnowledgeDocumentVersion, target: KnowledgeVersionStatus) -> None:
        if target not in cls.transitions.get(version.status, set()):
            raise ValueError(f"invalid knowledge version transition: {version.status} -> {target}")
        version.status = target


class KnowledgeRegistryService:
    def __init__(
        self,
        *,
        storage: Optional[ObjectStorage] = None,
        parsers: Optional[ParserRegistry] = None,
    ) -> None:
        self.storage = storage or InMemoryObjectStorage()
        self.parsers = parsers or ParserRegistry()

    def create_authority(
        self,
        session: Session,
        context: TenantContext,
        *,
        code: str,
        name: str,
        authority_type: KnowledgeAuthorityType,
        jurisdiction: Optional[str] = None,
        website_url: Optional[str] = None,
    ) -> KnowledgeAuthority:
        AuthorizationService.require_permission(context, "standards.authority.manage")
        code = code.strip().upper()
        if not code or len(code) > 80:
            raise ValueError("authority code is required")
        if session.scalar(select(KnowledgeAuthority).where(KnowledgeAuthority.code == code)):
            raise ValueError("authority code already exists")
        authority = KnowledgeAuthority(
            code=code,
            name=name.strip(),
            authority_type=authority_type,
            jurisdiction=jurisdiction,
            website_url=_valid_url(website_url),
        )
        session.add(authority)
        session.flush()
        AuditService.record_security(
            session,
            event_type="standards.authority.created",
            entity_type="knowledge_authority",
            entity_id=str(authority.id),
            actor_id=context.user_id,
            metadata={"code": code},
        )
        session.commit()
        return authority

    def create_source(
        self,
        session: Session,
        context: TenantContext,
        *,
        authority_id: uuid.UUID,
        source_code: str,
        title: str,
        document_type: KnowledgeDocumentType,
        assessment_family: AssessmentFamily,
        scope: KnowledgeScope = KnowledgeScope.PLATFORM_SHARED,
        language: Optional[str] = None,
        provider_code: Optional[str] = None,
        exam_family: Optional[str] = None,
        cefr_level: Optional[str] = None,
        skill: Optional[str] = None,
        canonical_url: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> KnowledgeSource:
        AuthorizationService.require_permission(context, "standards.source.manage")
        if scope is not KnowledgeScope.PLATFORM_SHARED:
            raise ValueError("tenant-private standards sources are not enabled in this phase")
        authority = session.get(KnowledgeAuthority, authority_id)
        if authority is None:
            raise ValueError("authority does not exist")
        if session.scalar(
            select(KnowledgeSource).where(KnowledgeSource.source_code == source_code)
        ):
            raise ValueError("source code already exists")
        if assessment_family is AssessmentFamily.INTEGRATION_KNOWLEDGE and cefr_level is not None:
            raise ValueError("integration knowledge must not use a CEFR level")
        if assessment_family is AssessmentFamily.LANGUAGE and cefr_level is not None:
            if cefr_level not in {"A1", "A2", "B1", "B2", "C1", "C2"}:
                raise ValueError("unsupported CEFR level")
        source = KnowledgeSource(
            authority_id=authority_id,
            source_code=source_code.strip().upper(),
            title=title.strip(),
            document_type=document_type,
            scope=scope,
            language=language,
            status="ACTIVE",
            canonical_url=_valid_url(canonical_url),
            notes=notes,
            assessment_family=assessment_family,
            provider_code=provider_code,
            exam_family=exam_family,
            cefr_level=cefr_level,
            skill=skill,
        )
        session.add(source)
        session.flush()
        AuditService.record_security(
            session,
            event_type="standards.source.created",
            entity_type="knowledge_source",
            entity_id=str(source.id),
            actor_id=context.user_id,
            metadata={
                "source_code": source.source_code,
                "assessment_family": assessment_family.value,
            },
        )
        session.commit()
        return source

    def create_version(
        self,
        session: Session,
        context: TenantContext,
        *,
        source_id: uuid.UUID,
        version_label: str,
        publication_date: Optional[datetime] = None,
        effective_from: Optional[datetime] = None,
        effective_until: Optional[datetime] = None,
        source_url: Optional[str] = None,
        supersedes_document_version_id: Optional[uuid.UUID] = None,
        notes: Optional[str] = None,
    ) -> KnowledgeDocumentVersion:
        AuthorizationService.require_permission(context, "standards.document.manage")
        if session.get(KnowledgeSource, source_id) is None:
            raise ValueError("source does not exist")
        effective_from = _aware(effective_from)
        effective_until = _aware(effective_until)
        if effective_from and effective_until and effective_until <= effective_from:
            raise ValueError("effective_until must be after effective_from")
        version = KnowledgeDocumentVersion(
            source_id=source_id,
            version_label=version_label.strip(),
            publication_date=_aware(publication_date),
            effective_from=effective_from,
            effective_until=effective_until,
            status=KnowledgeVersionStatus.DRAFT,
            source_url=_valid_url(source_url),
            supersedes_document_version_id=supersedes_document_version_id,
            notes=notes,
            created_at=utcnow(),
            created_by_user_id=context.user_id,
        )
        session.add(version)
        session.flush()
        session.add(KnowledgeLegalMetadata(document_version_id=version.id))
        if (
            supersedes_document_version_id
            and session.get(KnowledgeDocumentVersion, supersedes_document_version_id) is None
        ):
            raise ValueError("superseded version does not exist")
        AuditService.record_security(
            session,
            event_type="standards.document_version.created",
            entity_type="knowledge_document_version",
            entity_id=str(version.id),
            actor_id=context.user_id,
            metadata={"source_id": str(source_id), "version_label": version.version_label},
        )
        session.commit()
        return version

    def register_file(
        self,
        session: Session,
        context: TenantContext,
        *,
        version_id: uuid.UUID,
        filename: str,
        content: bytes,
        declared_mime_type: Optional[str] = None,
    ) -> KnowledgeDocumentVersion:
        AuthorizationService.require_permission(context, "standards.document.manage")
        version = session.get(KnowledgeDocumentVersion, version_id)
        if version is None:
            raise ValueError("document version does not exist")
        if version.status is KnowledgeVersionStatus.APPROVED:
            raise ValueError("approved document versions are immutable")
        if version.status not in {KnowledgeVersionStatus.DRAFT, KnowledgeVersionStatus.REJECTED}:
            raise ValueError("only draft or rejected document versions can receive files")
        mime_type = detect_mime_type(filename, declared_mime_type, content)
        checksum = hashlib.sha256(content).hexdigest()
        duplicate = session.scalar(
            select(KnowledgeDocumentVersion).where(
                KnowledgeDocumentVersion.source_id == version.source_id,
                KnowledgeDocumentVersion.sha256 == checksum,
                KnowledgeDocumentVersion.id != version.id,
            )
        )
        if duplicate is not None:
            raise ValueError("identical file is already registered for this source")
        storage_key = build_storage_key(version.source_id, version.id, filename)
        self.storage.put(storage_key, content, mime_type)
        version.original_filename = filename.replace("\\", "/").split("/")[-1]
        version.mime_type = mime_type
        version.storage_key = storage_key
        version.file_size = len(content)
        version.sha256 = checksum
        AuditService.record_security(
            session,
            event_type="standards.document.upload_registered",
            entity_type="knowledge_document_version",
            entity_id=str(version.id),
            actor_id=context.user_id,
            metadata={"sha256": checksum, "mime_type": mime_type},
        )
        session.commit()
        return version

    def ingest(
        self,
        session: Session,
        context: TenantContext,
        *,
        version_id: uuid.UUID,
    ) -> KnowledgeIngestionJob:
        AuthorizationService.require_permission(context, "standards.document.manage")
        version = session.get(KnowledgeDocumentVersion, version_id)
        if version is None or not version.storage_key or not version.mime_type:
            raise ValueError("document version has no registered file")
        if version.status is KnowledgeVersionStatus.APPROVED:
            raise ValueError("approved document versions are immutable")
        existing_success = session.scalar(
            select(KnowledgeIngestionJob).where(
                KnowledgeIngestionJob.document_version_id == version.id,
                KnowledgeIngestionJob.status == KnowledgeIngestionStatus.SUCCEEDED,
            )
        )
        if (
            existing_success is not None
            and version.status is KnowledgeVersionStatus.REVIEW_REQUIRED
        ):
            return existing_success
        job = KnowledgeIngestionJob(
            document_version_id=version.id,
            status=KnowledgeIngestionStatus.RUNNING,
            started_at=utcnow(),
            created_by_user_id=context.user_id,
            created_at=utcnow(),
        )
        session.add(job)
        session.flush()
        job_id = job.id
        version_id = version.id
        version.status = KnowledgeVersionStatus.INGESTING
        AuditService.record_security(
            session,
            event_type="standards.ingestion.started",
            entity_type="knowledge_ingestion_job",
            entity_id=str(job.id),
            actor_id=context.user_id,
            metadata={"document_version_id": str(version.id)},
        )
        session.commit()
        try:
            parser = self.parsers.for_mime(version.mime_type)
            content = self.storage.get(version.storage_key)
            parsed_pages = parser.parse(content)
            session.execute(
                delete(KnowledgeDocumentPage).where(
                    KnowledgeDocumentPage.document_version_id == version.id
                )
            )
            for parsed in parsed_pages:
                session.add(
                    KnowledgeDocumentPage(
                        document_version_id=version.id,
                        ingestion_job_id=job.id,
                        page_number=parsed.page_number,
                        text_content=parsed.text_content,
                        normalized_text=parsed.normalized_text,
                        content_hash=parsed.content_hash,
                        character_count=parsed.character_count,
                        extraction_status=ExtractionStatus(parsed.extraction_status),
                        heading=parsed.heading,
                        parser_name=parser.name,
                        parser_version=parser.version,
                        created_at=utcnow(),
                    )
                )
            job.status = KnowledgeIngestionStatus.SUCCEEDED
            job.finished_at = utcnow()
            job.parser_name = parser.name
            job.parser_version = parser.version
            job.pages_detected = len(parsed_pages)
            version.page_count = len(parsed_pages)
            version.status = KnowledgeVersionStatus.REVIEW_REQUIRED
            AuditService.record_security(
                session,
                event_type="standards.ingestion.succeeded",
                entity_type="knowledge_ingestion_job",
                entity_id=str(job.id),
                actor_id=context.user_id,
                metadata={"pages_detected": len(parsed_pages), "parser": parser.name},
            )
            session.commit()
        except Exception as exc:
            session.rollback()
            failed_job = session.get(KnowledgeIngestionJob, job_id)
            version = session.get(KnowledgeDocumentVersion, version_id)
            if failed_job is None or version is None:
                raise
            job = failed_job
            job.status = KnowledgeIngestionStatus.FAILED
            job.finished_at = utcnow()
            job.error_code = type(exc).__name__[:80]
            job.error_message_safe = "document extraction failed"
            version.status = KnowledgeVersionStatus.REJECTED
            AuditService.record_security(
                session,
                event_type="standards.ingestion.failed",
                entity_type="knowledge_ingestion_job",
                entity_id=str(job.id),
                actor_id=context.user_id,
                metadata={"error_code": job.error_code},
            )
            session.commit()
        return job

    def _review_context(self, context: TenantContext) -> None:
        AuthorizationService.require_permission(context, "standards.document.review")

    def approve(
        self, session: Session, context: TenantContext, *, version_id: uuid.UUID
    ) -> KnowledgeDocumentVersion:
        self._review_context(context)
        version = session.get(KnowledgeDocumentVersion, version_id)
        if version is None:
            raise ValueError("document version does not exist")
        if version.status not in {
            KnowledgeVersionStatus.INGESTED,
            KnowledgeVersionStatus.REVIEW_REQUIRED,
        }:
            raise ValueError("only successfully ingested versions can be approved")
        if not version.pages:
            raise ValueError("document version has no extracted pages")
        version.status = KnowledgeVersionStatus.APPROVED
        version.reviewed_at = utcnow()
        version.reviewed_by_user_id = context.user_id
        version.approved_at = utcnow()
        version.approved_by_user_id = context.user_id
        AuditService.record_security(
            session,
            event_type="standards.document.approved",
            entity_type="knowledge_document_version",
            entity_id=str(version.id),
            actor_id=context.user_id,
            metadata={"source_id": str(version.source_id)},
        )
        session.commit()
        return version

    def reject(
        self, session: Session, context: TenantContext, *, version_id: uuid.UUID, reason: str
    ) -> KnowledgeDocumentVersion:
        self._review_context(context)
        version = session.get(KnowledgeDocumentVersion, version_id)
        if version is None:
            raise ValueError("document version does not exist")
        if version.status in {KnowledgeVersionStatus.APPROVED, KnowledgeVersionStatus.SUPERSEDED}:
            raise ValueError("approved versions require a new revision")
        version.status = KnowledgeVersionStatus.REJECTED
        version.reviewed_at = utcnow()
        version.reviewed_by_user_id = context.user_id
        version.rejection_reason = reason[:500]
        AuditService.record_security(
            session,
            event_type="standards.document.rejected",
            entity_type="knowledge_document_version",
            entity_id=str(version.id),
            actor_id=context.user_id,
            metadata={"reason_provided": bool(reason)},
        )
        session.commit()
        return version

    def supersede(
        self,
        session: Session,
        context: TenantContext,
        *,
        old_version_id: uuid.UUID,
        new_version_id: uuid.UUID,
    ) -> None:
        self._review_context(context)
        old = session.get(KnowledgeDocumentVersion, old_version_id)
        new = session.get(KnowledgeDocumentVersion, new_version_id)
        if old is None or new is None or old.source_id != new.source_id:
            raise ValueError("versions must exist and belong to the same source")
        if (
            old.status is not KnowledgeVersionStatus.APPROVED
            or new.status is not KnowledgeVersionStatus.APPROVED
        ):
            raise ValueError("only approved versions can be superseded")
        old.status = KnowledgeVersionStatus.SUPERSEDED
        new.supersedes_document_version_id = old.id
        AuditService.record_security(
            session,
            event_type="standards.document.superseded",
            entity_type="knowledge_document_version",
            entity_id=str(new.id),
            actor_id=context.user_id,
            metadata={"superseded_version_id": str(old.id)},
        )
        session.commit()

    def set_legal_metadata(
        self,
        session: Session,
        context: TenantContext,
        *,
        version_id: uuid.UUID,
        **fields: object,
    ) -> KnowledgeLegalMetadata:
        AuthorizationService.require_permission(context, "standards.document.manage")
        version = session.get(KnowledgeDocumentVersion, version_id)
        if version is None or version.status in {
            KnowledgeVersionStatus.APPROVED,
            KnowledgeVersionStatus.SUPERSEDED,
        }:
            raise ValueError("approved document legal metadata is immutable")
        legal = version.legal_metadata or KnowledgeLegalMetadata(document_version_id=version.id)
        for key, value in fields.items():
            if not hasattr(legal, key):
                raise ValueError(f"unsupported legal metadata field: {key}")
            setattr(legal, key, value)
        session.add(legal)
        session.flush()
        AuditService.record_security(
            session,
            event_type="standards.legal_review.updated",
            entity_type="knowledge_legal_metadata",
            entity_id=str(legal.id),
            actor_id=context.user_id,
            metadata={"version_id": str(version.id)},
        )
        session.commit()
        return legal

    def approved_at(
        self,
        session: Session,
        *,
        source_id: uuid.UUID,
        at: datetime,
    ) -> list[KnowledgeDocumentVersion]:
        point = _aware(at) or utcnow()
        versions = list(
            session.scalars(
                select(KnowledgeDocumentVersion).where(
                    KnowledgeDocumentVersion.source_id == source_id,
                    KnowledgeDocumentVersion.status.in_(
                        [KnowledgeVersionStatus.APPROVED, KnowledgeVersionStatus.SUPERSEDED]
                    ),
                )
            )
        )
        return [
            version
            for version in versions
            if (
                version.effective_from is None or (_aware(version.effective_from) or point) <= point
            )
            and (
                version.effective_until is None
                or (_aware(version.effective_until) or point) > point
            )
        ]

    def provenance(self, session: Session, *, page_id: uuid.UUID) -> dict[str, object]:
        page = session.get(KnowledgeDocumentPage, page_id)
        if page is None:
            raise ValueError("page does not exist")
        version = session.get(KnowledgeDocumentVersion, page.document_version_id)
        if version is None:
            raise ValueError("document version does not exist")
        source = session.get(KnowledgeSource, version.source_id)
        authority = session.get(KnowledgeAuthority, source.authority_id) if source else None
        return {
            "authority": authority.code if authority else None,
            "source_code": source.source_code if source else None,
            "source_title": source.title if source else None,
            "document_version": version.version_label,
            "status": version.status.value,
            "publication_date": version.publication_date,
            "effective_from": version.effective_from,
            "effective_until": version.effective_until,
            "page": page.page_number,
            "checksum": version.sha256,
            "source_url": version.source_url,
            "legal_metadata": version.legal_metadata,
            "parser": page.parser_name,
            "parser_version": page.parser_version,
            "ingestion_job_id": str(page.ingestion_job_id),
        }


def seed_knowledge_authorities(session: Session) -> None:
    """Seed registry identities only; no provider content or legal claims."""
    definitions = {
        "COUNCIL_OF_EUROPE": (
            "Council of Europe",
            KnowledgeAuthorityType.SUPRANATIONAL_STANDARD_BODY,
        ),
        "BAMF": (
            "Bundesamt für Migration und Flüchtlinge",
            KnowledgeAuthorityType.FEDERAL_AUTHORITY,
        ),
        "GAST": ("g.a.s.t.", KnowledgeAuthorityType.EXAM_PROVIDER),
        "GOETHE": ("Goethe-Institut", KnowledgeAuthorityType.EXAM_PROVIDER),
        "TELC": ("telc", KnowledgeAuthorityType.EXAM_PROVIDER),
        "PRUFAI": ("PrüfAI", KnowledgeAuthorityType.INTERNAL),
    }
    for code, (name, authority_type) in definitions.items():
        if (
            session.scalar(select(KnowledgeAuthority).where(KnowledgeAuthority.code == code))
            is None
        ):
            session.add(KnowledgeAuthority(code=code, name=name, authority_type=authority_type))
    session.commit()
