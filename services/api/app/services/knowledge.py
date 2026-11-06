from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qsl, urlparse

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.auth.security import utcnow
from app.authorization import AuthorizationError, TenantContext
from app.config import get_settings
from app.knowledge.parsers import ParserRegistry
from app.knowledge.storage import (
    MAX_SOURCE_FILE_SIZE,
    ObjectStorage,
    S3CompatibleObjectStorage,
    StorageError,
    StorageIntegrityError,
    build_object_storage,
    build_storage_key,
    detect_mime_type,
    safe_filename,
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
    LegalReviewStatus,
)
from app.writing_assessment.service import canonical_checksum

KNOWN_AUTHORITY_CODES = {
    "COUNCIL_OF_EUROPE",
    "BAMF",
    "GAST",
    "GOETHE",
    "TELC",
    "PRUFAI",
}
CONTENT_MODES = {"METADATA_ONLY", "ORIGINAL_SYNTHETIC"}
RIGHTS_DECISIONS = {"METADATA_ONLY", "ALLOW_SYNTHETIC_INGESTION", "DENY", "WITHDRAW"}
_SHA256 = re.compile(r"[a-f0-9]{64}")


class SourceConflict(ValueError):
    """A stale request cannot replace a sealed source or decision."""


def _valid_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("URL must use http or https")
    if any(
        marker in key.lower().replace("-", "_")
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
        for marker in ("password", "secret", "token", "api_key", "signature", "x_amz_")
    ):
        raise ValueError("source URL must not include credentials")
    return value


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _timestamp(value: datetime | None) -> str | None:
    instant = _aware(value)
    return instant.astimezone(timezone.utc).isoformat() if instant else None


def source_metadata_checksum(version: KnowledgeDocumentVersion) -> str:
    return canonical_checksum(
        {
            "id": str(version.id),
            "source_id": str(version.source_id),
            "version_label": version.version_label,
            "content_mode": version.content_mode,
            "publication_date": _timestamp(version.publication_date),
            "retrieved_at": _timestamp(version.retrieved_at),
            "effective_from": _timestamp(version.effective_from),
            "effective_until": _timestamp(version.effective_until),
            "source_url": version.source_url,
            "supersedes": str(version.supersedes_document_version_id)
            if version.supersedes_document_version_id
            else None,
        }
    )


def source_version_reference_checksum(version: KnowledgeDocumentVersion) -> str | None:
    return version.sha256 or version.metadata_checksum


def _decision_checksum(version: KnowledgeDocumentVersion, legal: KnowledgeLegalMetadata) -> str:
    return canonical_checksum(
        {
            "metadata_checksum": version.metadata_checksum,
            "version_id": str(version.id),
            "reviewer": str(legal.legal_reviewed_by_user_id),
            "reviewed_at": _timestamp(legal.legal_reviewed_at),
            "status": legal.legal_review_status.value,
            **{
                key: getattr(legal, key)
                for key in (
                    "copyright_status",
                    "licence_type",
                    "rights_holder",
                    "usage_rights",
                    "ingestion_allowed",
                    "approved_sha256",
                    "basis_reference",
                    "retention_policy",
                    "redistribution_allowed",
                    "derivative_use_allowed",
                    "ai_processing_allowed",
                    "citation_required",
                    "commercial_use_allowed",
                )
            },
        }
    )


def source_legal_decision_valid(version: KnowledgeDocumentVersion) -> bool:
    legal = version.legal_metadata
    return bool(
        version.metadata_checksum is not None
        and version.metadata_checksum == source_metadata_checksum(version)
        and legal is not None
        and legal.legal_review_status == LegalReviewStatus.APPROVED
        and legal.legal_reviewed_by_user_id is not None
        and legal.legal_reviewed_by_user_id != version.created_by_user_id
        and legal.legal_reviewed_at is not None
        and legal.decision_checksum is not None
        and legal.decision_checksum == _decision_checksum(version, legal)
    )


def _source_available(version: KnowledgeDocumentVersion) -> bool:
    return (
        version.source.scope == KnowledgeScope.PLATFORM_SHARED
        and version.source.status == "ACTIVE"
        and version.source.authority.status == "ACTIVE"
        and version.status != KnowledgeVersionStatus.RETIRED
    )


def _ingestion_rights(version: KnowledgeDocumentVersion) -> KnowledgeLegalMetadata | None:
    legal = version.legal_metadata
    if (
        legal is None
        or version.content_mode != "ORIGINAL_SYNTHETIC"
        or version.source.authority.authority_type != KnowledgeAuthorityType.INTERNAL
        or not _source_available(version)
        or not source_legal_decision_valid(version)
        or legal.ingestion_allowed is not True
        or _SHA256.fullmatch(legal.approved_sha256 or "") is None
    ):
        return None
    return legal


def content_rights_permit(version: KnowledgeDocumentVersion) -> bool:
    legal = _ingestion_rights(version)
    return bool(
        legal is not None
        and version.storage_key
        and version.file_size is not None
        and 0 < version.file_size <= MAX_SOURCE_FILE_SIZE
        and version.sha256 == legal.approved_sha256
    )


def _platform(context: TenantContext, permission: str) -> None:
    if context.organisation_id is not None or permission not in context.platform_permissions:
        raise AuthorizationError("platform standards authority required")


def _version(
    session: Session, version_id: uuid.UUID, *, lock: bool = False
) -> KnowledgeDocumentVersion:
    row = session.get(
        KnowledgeDocumentVersion, version_id, with_for_update=lock, populate_existing=lock
    )
    if row is None or row.source.scope != KnowledgeScope.PLATFORM_SHARED:
        raise SourceConflict("SOURCE_VERSION_UNAVAILABLE")
    return row


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
        self._storage = storage
        self.parsers = parsers or ParserRegistry()

    @property
    def storage(self) -> ObjectStorage:
        if self._storage is None:
            self._storage = build_object_storage(get_settings())
        return self._storage

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
        commit: bool = True,
    ) -> KnowledgeAuthority:
        _platform(context, "standards.authority.manage")
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
        if commit:
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
        commit: bool = True,
    ) -> KnowledgeSource:
        _platform(context, "standards.source.manage")
        if scope is not KnowledgeScope.PLATFORM_SHARED:
            raise ValueError("tenant-private standards sources are not enabled in this phase")
        authority = session.get(KnowledgeAuthority, authority_id)
        if authority is None:
            raise ValueError("authority does not exist")
        source_code = source_code.strip().upper()
        if not source_code or not title.strip():
            raise ValueError("source code and title are required")
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
            source_code=source_code,
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
        if commit:
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
        content_mode: str = "METADATA_ONLY",
        retrieved_at: Optional[datetime] = None,
        rights_holder: Optional[str] = None,
        licence_type: Optional[str] = None,
        commit: bool = True,
    ) -> KnowledgeDocumentVersion:
        _platform(context, "standards.document.manage")
        source = session.get(KnowledgeSource, source_id)
        if source is None or source.scope != KnowledgeScope.PLATFORM_SHARED:
            raise ValueError("source does not exist")
        if content_mode not in CONTENT_MODES or (
            content_mode == "ORIGINAL_SYNTHETIC"
            and source.authority.authority_type != KnowledgeAuthorityType.INTERNAL
        ):
            raise ValueError("SOURCE_CONTENT_MODE_UNAVAILABLE")
        version_label = version_label.strip()
        if not version_label:
            raise ValueError("source version label is required")
        if (
            session.scalar(
                select(KnowledgeDocumentVersion.id).where(
                    KnowledgeDocumentVersion.source_id == source_id,
                    KnowledgeDocumentVersion.version_label == version_label,
                )
            )
            is not None
        ):
            raise SourceConflict("SOURCE_VERSION_ALREADY_REGISTERED")
        if supersedes_document_version_id is not None:
            previous = _version(session, supersedes_document_version_id)
            if previous.source_id != source_id:
                raise SourceConflict("SOURCE_PREDECESSOR_MISMATCH")
        effective_from = _aware(effective_from)
        effective_until = _aware(effective_until)
        if effective_from and effective_until and effective_until <= effective_from:
            raise ValueError("effective_until must be after effective_from")
        version = KnowledgeDocumentVersion(
            id=uuid.uuid4(),
            source_id=source_id,
            version_label=version_label,
            content_mode=content_mode,
            publication_date=_aware(publication_date),
            retrieved_at=_aware(retrieved_at),
            effective_from=effective_from,
            effective_until=effective_until,
            status=KnowledgeVersionStatus.DRAFT,
            source_url=_valid_url(source_url),
            supersedes_document_version_id=supersedes_document_version_id,
            notes=notes,
            created_at=utcnow(),
            created_by_user_id=context.user_id,
        )
        version.metadata_checksum = source_metadata_checksum(version)
        session.add(version)
        session.flush()
        session.add(
            KnowledgeLegalMetadata(
                document_version_id=version.id,
                rights_holder=rights_holder,
                licence_type=licence_type,
            )
        )
        AuditService.record_security(
            session,
            event_type="standards.document_version.created",
            entity_type="knowledge_document_version",
            entity_id=str(version.id),
            actor_id=context.user_id,
            metadata={"source_id": str(source_id), "version_label": version.version_label},
        )
        if commit:
            session.commit()
        return version

    def review_rights(
        self,
        session: Session,
        context: TenantContext,
        *,
        version_id: uuid.UUID,
        expected_metadata_checksum: str,
        decision: str,
        basis_reference: str,
        rights_holder: str,
        licence_type: str,
        retention_policy: str,
        expected_sha256: str | None = None,
    ) -> KnowledgeLegalMetadata:
        self._review_context(context)
        version = _version(session, version_id, lock=True)
        legal = self._record_rights_decision(
            session,
            version,
            actor_id=context.user_id,
            expected_metadata_checksum=expected_metadata_checksum,
            decision=decision,
            basis_reference=basis_reference,
            rights_holder=rights_holder,
            licence_type=licence_type,
            retention_policy=retention_policy,
            expected_sha256=expected_sha256,
        )
        session.commit()
        return legal

    @staticmethod
    def _record_rights_decision(
        session: Session,
        version: KnowledgeDocumentVersion,
        *,
        actor_id: uuid.UUID,
        expected_metadata_checksum: str,
        decision: str,
        basis_reference: str,
        rights_holder: str,
        licence_type: str,
        retention_policy: str,
        expected_sha256: str | None = None,
        scoped_grant_id: uuid.UUID | None = None,
    ) -> KnowledgeLegalMetadata:
        """Write the canonical decision after caller authorization and version locking."""
        legal = version.legal_metadata
        if (
            legal is None
            or version.metadata_checksum != expected_metadata_checksum
            or source_metadata_checksum(version) != expected_metadata_checksum
        ):
            raise SourceConflict("SOURCE_METADATA_CHECKSUM_MISMATCH")
        if actor_id == version.created_by_user_id:
            raise AuthorizationError("SOURCE_INDEPENDENT_RIGHTS_REVIEW_REQUIRED")
        values = {
            "basis_reference": basis_reference.strip(),
            "rights_holder": rights_holder.strip(),
            "licence_type": licence_type.strip(),
            "retention_policy": retention_policy.strip(),
        }
        if decision not in RIGHTS_DECISIONS or any(
            not value or len(value) > bound
            for value, bound in zip(values.values(), (240, 300, 120, 240), strict=True)
        ):
            raise ValueError("SOURCE_RIGHTS_DECISION_INVALID")
        if decision == "WITHDRAW":
            if expected_sha256 is not None:
                raise ValueError("SOURCE_WITHDRAWAL_USES_METADATA_PIN")
            if not source_legal_decision_valid(version):
                raise SourceConflict("SOURCE_RIGHTS_APPROVAL_REQUIRED")
            if version.status != KnowledgeVersionStatus.RETIRED:
                version.status = KnowledgeVersionStatus.RETIRED
                AuditService.record_security(
                    session,
                    event_type="standards.source_rights.withdrawn",
                    entity_type="knowledge_document_version",
                    entity_id=str(version.id),
                    actor_id=actor_id,
                    metadata={
                        "metadata_checksum": version.metadata_checksum,
                        "prior_decision_checksum": legal.decision_checksum,
                        "withdrawal_basis_checksum": canonical_checksum(values),
                    },
                )
            return legal
        if not _source_available(version):
            raise SourceConflict("SOURCE_VERSION_UNAVAILABLE")
        allow = decision == "ALLOW_SYNTHETIC_INGESTION"
        if (
            (
                allow
                and (
                    version.content_mode != "ORIGINAL_SYNTHETIC"
                    or version.source.authority.authority_type != KnowledgeAuthorityType.INTERNAL
                    or _SHA256.fullmatch(expected_sha256 or "") is None
                )
            )
            or (not allow and expected_sha256 is not None)
            or (decision == "METADATA_ONLY" and version.content_mode != "METADATA_ONLY")
        ):
            raise ValueError("SOURCE_RIGHTS_CONTENT_MODE_MISMATCH")
        review_status = (
            LegalReviewStatus.REJECTED if decision == "DENY" else LegalReviewStatus.APPROVED
        )
        if legal.decision_checksum is not None:
            if (
                legal.legal_reviewed_by_user_id == actor_id
                and legal.legal_review_status == review_status
                and legal.ingestion_allowed is allow
                and legal.approved_sha256 == expected_sha256
                and all(getattr(legal, key) == value for key, value in values.items())
                and legal.decision_checksum == _decision_checksum(version, legal)
            ):
                return legal
            raise SourceConflict("SOURCE_RIGHTS_DECISION_IMMUTABLE")
        if version.status != KnowledgeVersionStatus.DRAFT or version.storage_key is not None:
            raise SourceConflict("SOURCE_RIGHTS_DRAFT_REQUIRED")
        legal.basis_reference = values["basis_reference"]
        legal.rights_holder = values["rights_holder"]
        legal.licence_type = values["licence_type"]
        legal.retention_policy = values["retention_policy"]
        legal.copyright_status = "PRUFAI_ORIGINAL" if allow else "UNKNOWN"
        legal.usage_rights = {
            "ALLOW_SYNTHETIC_INGESTION": "ORIGINAL_SYNTHETIC_INTERNAL_TEST_ONLY",
            "METADATA_ONLY": "BIBLIOGRAPHIC_REFERENCE_ONLY",
            "DENY": "CONTENT_USE_DENIED",
        }[decision]
        legal.ingestion_allowed = allow
        legal.approved_sha256 = expected_sha256
        legal.derivative_use_allowed = allow
        legal.ai_processing_allowed = allow
        legal.redistribution_allowed = False
        legal.commercial_use_allowed = False
        legal.citation_required = True
        legal.legal_review_status = review_status
        legal.legal_reviewed_by_user_id = actor_id
        legal.legal_reviewed_at = utcnow()
        legal.decision_checksum = _decision_checksum(version, legal)
        AuditService.record_security(
            session,
            event_type="standards.source_rights.reviewed",
            entity_type="knowledge_document_version",
            entity_id=str(version.id),
            actor_id=actor_id,
            metadata={
                "decision": decision,
                "decision_checksum": legal.decision_checksum,
                "metadata_checksum": version.metadata_checksum,
                "approved_sha256": expected_sha256,
                **({"scoped_grant_id": str(scoped_grant_id)} if scoped_grant_id else {}),
            },
        )
        return legal

    def _verified_file(self, version: KnowledgeDocumentVersion) -> bytes:
        if not content_rights_permit(version):
            raise SourceConflict("SOURCE_CONTENT_RIGHTS_REQUIRED")
        assert version.storage_key is not None and version.file_size is not None
        storage = self.storage
        bucket = storage.bucket if isinstance(storage, S3CompatibleObjectStorage) else None
        if bucket != version.storage_bucket:
            raise StorageIntegrityError("SOURCE_STORAGE_IDENTITY_MISMATCH")
        if isinstance(storage, S3CompatibleObjectStorage):
            content = storage.get_bounded(
                version.storage_key, max_bytes=MAX_SOURCE_FILE_SIZE, expected_size=version.file_size
            )
        else:
            if (
                not 0 < version.file_size <= MAX_SOURCE_FILE_SIZE
                or storage.size(version.storage_key) != version.file_size
            ):
                raise StorageIntegrityError("SOURCE_SIZE_MISMATCH")
            content = storage.get(version.storage_key)
        if (
            len(content) != version.file_size
            or hashlib.sha256(content).hexdigest() != version.sha256
        ):
            raise StorageIntegrityError("SOURCE_CHECKSUM_MISMATCH")
        return content

    def verify_file(
        self, session: Session, context: TenantContext, *, version_id: uuid.UUID
    ) -> KnowledgeDocumentVersion:
        _platform(context, "standards.document.read")
        version = _version(session, version_id, lock=True)
        self._verified_file(version)
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
        _platform(context, "standards.document.manage")
        version = _version(session, version_id, lock=True)
        legal = _ingestion_rights(version)
        if legal is None:
            raise SourceConflict("SOURCE_CONTENT_RIGHTS_REQUIRED")
        mime_type = detect_mime_type(filename, declared_mime_type, content)
        normalized_filename = safe_filename(filename)
        checksum = hashlib.sha256(content).hexdigest()
        if version.storage_key is not None:
            if (version.sha256, version.original_filename, version.mime_type) != (
                checksum,
                normalized_filename,
                mime_type,
            ):
                raise SourceConflict("SOURCE_OBJECT_IDENTITY_IMMUTABLE")
            self._verified_file(version)
            return version
        if version.status != KnowledgeVersionStatus.DRAFT:
            raise SourceConflict("SOURCE_DRAFT_REQUIRED")
        if checksum != legal.approved_sha256:
            raise SourceConflict("SOURCE_CHECKSUM_MISMATCH")
        storage = self.storage
        storage_key = build_storage_key(version.source_id, version.id, filename, sha256=checksum)
        storage.put(storage_key, content, mime_type)
        version.original_filename = normalized_filename
        version.mime_type = mime_type
        version.storage_key = storage_key
        version.storage_bucket = (
            storage.bucket if isinstance(storage, S3CompatibleObjectStorage) else None
        )
        version.file_size = len(content)
        version.sha256 = checksum
        self._verified_file(version)
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
        self, session: Session, context: TenantContext, *, version_id: uuid.UUID
    ) -> KnowledgeIngestionJob:
        _platform(context, "standards.document.manage")
        version = _version(session, version_id, lock=True)
        if not content_rights_permit(version):
            raise SourceConflict("SOURCE_CONTENT_RIGHTS_REQUIRED")
        existing_success = session.scalar(
            select(KnowledgeIngestionJob)
            .where(
                KnowledgeIngestionJob.document_version_id == version.id,
                KnowledgeIngestionJob.status == KnowledgeIngestionStatus.SUCCEEDED,
            )
            .order_by(KnowledgeIngestionJob.created_at.desc())
            .limit(1)
        )
        if existing_success is not None:
            self._verified_file(version)
            return existing_success
        if (
            version.status not in {KnowledgeVersionStatus.DRAFT, KnowledgeVersionStatus.REJECTED}
            or version.rejection_reason
        ):
            raise SourceConflict("SOURCE_INGESTION_STATE_CONFLICT")
        if version.pages:
            raise SourceConflict("SOURCE_PAGE_HISTORY_ALREADY_PRESENT")
        job = KnowledgeIngestionJob(
            document_version_id=version.id,
            status=KnowledgeIngestionStatus.RUNNING,
            started_at=utcnow(),
            created_by_user_id=context.user_id,
            created_at=utcnow(),
        )
        session.add(job)
        session.flush()
        version.status = KnowledgeVersionStatus.INGESTING
        AuditService.record_security(
            session,
            event_type="standards.ingestion.started",
            entity_type="knowledge_ingestion_job",
            entity_id=str(job.id),
            actor_id=context.user_id,
            metadata={"document_version_id": str(version.id), "sha256": version.sha256},
        )
        try:
            assert version.mime_type is not None
            parser = self.parsers.for_mime(version.mime_type)
            job.parser_name, job.parser_version = parser.name, parser.version
            with session.begin_nested():
                parsed_pages = parser.parse(self._verified_file(version))
                if not any(page.extraction_status == "EXTRACTED" for page in parsed_pages):
                    raise ValueError("SOURCE_HAS_NO_EXTRACTED_TEXT")
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
                session.flush()
            job.status = KnowledgeIngestionStatus.SUCCEEDED
            job.pages_detected = version.page_count = len(parsed_pages)
            version.status = KnowledgeVersionStatus.REVIEW_REQUIRED
            event_type = "standards.ingestion.succeeded"
            audit_metadata = {"pages_detected": len(parsed_pages), "parser": parser.name}
        except SQLAlchemyError:
            raise
        except Exception as exc:
            job.status = KnowledgeIngestionStatus.FAILED
            job.error_code = (
                "SOURCE_OBJECT_INTEGRITY_FAILED"
                if isinstance(exc, StorageIntegrityError)
                else "SOURCE_STORAGE_UNAVAILABLE"
                if isinstance(exc, StorageError)
                else "SOURCE_EXTRACTION_FAILED"
            )
            job.error_message_safe = "document extraction failed"
            version.status = KnowledgeVersionStatus.REJECTED
            event_type = "standards.ingestion.failed"
            audit_metadata = {"error_code": job.error_code}
        job.finished_at = utcnow()
        AuditService.record_security(
            session,
            event_type=event_type,
            entity_type="knowledge_ingestion_job",
            entity_id=str(job.id),
            actor_id=context.user_id,
            metadata=audit_metadata,
        )
        session.commit()
        return job

    def _review_context(self, context: TenantContext) -> None:
        _platform(context, "standards.document.review")

    def approve(
        self,
        session: Session,
        context: TenantContext,
        *,
        version_id: uuid.UUID,
        expected_sha256: str,
    ) -> KnowledgeDocumentVersion:
        self._review_context(context)
        version = _version(session, version_id, lock=True)
        if context.user_id == version.created_by_user_id:
            raise AuthorizationError("SOURCE_INDEPENDENT_CONTENT_REVIEW_REQUIRED")
        if expected_sha256 != version.sha256 or not content_rights_permit(version):
            raise SourceConflict("SOURCE_CONTENT_CHECKSUM_OR_RIGHTS_MISMATCH")
        if (
            version.status == KnowledgeVersionStatus.APPROVED
            and version.approved_by_user_id == context.user_id
        ):
            return version
        completed = [
            job
            for job in version.ingestion_jobs
            if job.status == KnowledgeIngestionStatus.SUCCEEDED
        ]
        if version.status != KnowledgeVersionStatus.REVIEW_REQUIRED or len(completed) != 1:
            raise ValueError("only successfully ingested versions can be approved")
        job = completed[0]
        if (
            not version.pages
            or len(version.pages) != version.page_count
            or len(version.pages) != job.pages_detected
            or any(
                page.ingestion_job_id != job.id
                or page.parser_name != job.parser_name
                or page.parser_version != job.parser_version
                for page in version.pages
            )
        ):
            raise SourceConflict("SOURCE_INGESTION_EVIDENCE_MISMATCH")
        self._verified_file(version)
        version.status = KnowledgeVersionStatus.APPROVED
        version.reviewed_at = version.approved_at = utcnow()
        version.reviewed_by_user_id = version.approved_by_user_id = context.user_id
        AuditService.record_security(
            session,
            event_type="standards.document.approved",
            entity_type="knowledge_document_version",
            entity_id=str(version.id),
            actor_id=context.user_id,
            metadata={"source_id": str(version.source_id), "sha256": expected_sha256},
        )
        session.commit()
        return version

    def reject(
        self, session: Session, context: TenantContext, *, version_id: uuid.UUID, reason: str
    ) -> KnowledgeDocumentVersion:
        self._review_context(context)
        version = _version(session, version_id, lock=True)
        if context.user_id == version.created_by_user_id:
            raise AuthorizationError("SOURCE_INDEPENDENT_CONTENT_REVIEW_REQUIRED")
        if version.status in {
            KnowledgeVersionStatus.APPROVED,
            KnowledgeVersionStatus.SUPERSEDED,
            KnowledgeVersionStatus.RETIRED,
        }:
            raise ValueError("approved versions require a new revision")
        if not reason.strip():
            raise ValueError("SOURCE_REJECTION_REASON_REQUIRED")
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
        first, second = sorted((old_version_id, new_version_id))
        versions = {
            identifier: _version(session, identifier, lock=True) for identifier in (first, second)
        }
        old, new = versions[old_version_id], versions[new_version_id]
        if (
            old.id == new.id
            or old.source_id != new.source_id
            or new.supersedes_document_version_id != old.id
            or old.status is not KnowledgeVersionStatus.APPROVED
            or new.status is not KnowledgeVersionStatus.APPROVED
        ):
            raise ValueError("only approved versions can be superseded")
        old.status = KnowledgeVersionStatus.SUPERSEDED
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
        _platform(context, "standards.document.manage")
        version = _version(session, version_id, lock=True)
        if version.status != KnowledgeVersionStatus.DRAFT:
            raise ValueError("approved document legal metadata is immutable")
        legal = version.legal_metadata or KnowledgeLegalMetadata(document_version_id=version.id)
        if legal.decision_checksum is not None:
            raise SourceConflict("SOURCE_RIGHTS_DECISION_IMMUTABLE")
        for key, value in fields.items():
            if key not in {"rights_holder", "licence_type", "legal_notes"}:
                raise ValueError("SOURCE_RIGHTS_REVIEW_REQUIRED")
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

    def get_approved_versions_as_of(
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

    def get_current_approved_versions(
        self,
        session: Session,
        *,
        source_id: uuid.UUID,
        at: Optional[datetime] = None,
    ) -> list[KnowledgeDocumentVersion]:
        point = _aware(at) or utcnow()
        candidates = list(
            session.scalars(
                select(KnowledgeDocumentVersion).where(
                    KnowledgeDocumentVersion.source_id == source_id,
                    KnowledgeDocumentVersion.status == KnowledgeVersionStatus.APPROVED,
                )
            )
        )
        effective = [
            version
            for version in candidates
            if (
                version.effective_from is None or (_aware(version.effective_from) or point) <= point
            )
            and (
                version.effective_until is None
                or (_aware(version.effective_until) or point) > point
            )
        ]
        superseded_ids = {
            version.supersedes_document_version_id
            for version in effective
            if version.supersedes_document_version_id is not None
        }
        return [version for version in effective if version.id not in superseded_ids]

    def approved_at(
        self,
        session: Session,
        *,
        source_id: uuid.UUID,
        at: datetime,
    ) -> list[KnowledgeDocumentVersion]:
        """Backward-compatible alias for historical as-of resolution."""
        return self.get_approved_versions_as_of(session, source_id=source_id, at=at)

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
