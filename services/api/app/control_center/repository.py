from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import func, literal, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import Executable, FromClause

from app.models import (
    AIModelProfile,
    AIRun,
    AIWorkflowCheckpoint,
    AIWorkflowDefinition,
    AIWorkflowVersion,
    AuditEvent,
    CertificateRecord,
    CertificationAuthorization,
    Exam,
    ExamVersion,
    HumanReviewRequest,
    KnowledgeAuthority,
    KnowledgeDocumentVersion,
    KnowledgeIngestionJob,
    KnowledgeLegalMetadata,
    KnowledgeSource,
    Organisation,
    OrganisationMembership,
    QuestionBankItem,
    QuestionBankItemVersion,
    ResultAppeal,
    ResultModerationCase,
    ResultRelease,
    SpeechTranscriptionJob,
    StandardPack,
    StandardPackSourceBinding,
    StandardPackVersion,
    User,
    WritingExecutionJob,
)
from app.models.enums import AppealStatus, ModerationStatus


class SubquerySource(Protocol):
    def subquery(self) -> FromClause: ...


class LimitableQuery(Protocol):
    def limit(self, limit: int, /) -> Executable: ...


class MetadataQuery(Protocol):
    """Pagination capabilities independent of SQLAlchemy's row-shape generics."""

    def order_by(self, clause: None, /) -> SubquerySource: ...

    def offset(self, offset: int, /) -> LimitableQuery: ...


class ControlCenterRepository:
    """Explicit metadata projections; private domain payloads are never serialized."""

    @staticmethod
    def page(session: Session, query: MetadataQuery, offset: int, limit: int) -> dict[str, Any]:
        total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        rows = session.execute(query.offset(offset).limit(limit)).mappings()
        return {
            "items": [dict(row) for row in rows],
            "total": int(total or 0),
            "offset": offset,
            "limit": limit,
        }

    @staticmethod
    def count(session: Session, model: type[Any]) -> int:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)

    @staticmethod
    def shared_question_count(session: Session) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(QuestionBankItem)
                .where(QuestionBankItem.scope_type == "PLATFORM_SHARED")
            )
            or 0
        )

    @staticmethod
    def shared_exam_count(session: Session) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(ExamVersion)
                .join(Exam)
                .where(Exam.scope_type == "PLATFORM_SHARED")
            )
            or 0
        )

    @staticmethod
    def open_governance_count(session: Session) -> int:
        moderation = session.scalar(
            select(func.count())
            .select_from(ResultModerationCase)
            .where(
                ResultModerationCase.status.in_(
                    [
                        ModerationStatus.OPEN,
                        ModerationStatus.IN_REVIEW,
                        ModerationStatus.CORRECTION_REQUIRED,
                        ModerationStatus.ESCALATED,
                    ]
                )
            )
        )
        appeals = session.scalar(
            select(func.count())
            .select_from(ResultAppeal)
            .where(
                ResultAppeal.status.in_(
                    [
                        AppealStatus.SUBMITTED,
                        AppealStatus.UNDER_REVIEW,
                        AppealStatus.INFORMATION_REQUIRED,
                    ]
                )
            )
        )
        return int(moderation or 0) + int(appeals or 0)

    @staticmethod
    def recent_audit_count(session: Session) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        return int(
            session.scalar(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.timestamp >= cutoff)
            )
            or 0
        )

    @staticmethod
    def organisations(
        session: Session, offset: int, limit: int, memberships: bool
    ) -> dict[str, Any]:
        membership_count = (
            select(func.count())
            .select_from(OrganisationMembership)
            .where(OrganisationMembership.organisation_id == Organisation.id)
            .correlate(Organisation)
            .scalar_subquery()
            if memberships
            else literal(None)
        )
        query = select(
            Organisation.id,
            Organisation.name,
            Organisation.slug,
            Organisation.organisation_type,
            Organisation.status,
            Organisation.created_at,
            membership_count.label("membership_count"),
        ).order_by(Organisation.created_at, Organisation.id)
        return ControlCenterRepository.page(session, query, offset, limit)

    @staticmethod
    def standards(session: Session, kind: str, offset: int, limit: int) -> dict[str, Any]:
        query: MetadataQuery
        if kind == "authorities":
            query = select(
                KnowledgeAuthority.id,
                KnowledgeAuthority.code,
                KnowledgeAuthority.name,
                KnowledgeAuthority.authority_type,
                KnowledgeAuthority.status,
                KnowledgeAuthority.jurisdiction,
            ).order_by(KnowledgeAuthority.code, KnowledgeAuthority.id)
        elif kind == "packs":
            query = select(
                StandardPack.id,
                StandardPack.code,
                StandardPack.name,
                StandardPack.status,
                StandardPack.assessment_family,
                StandardPack.language,
                StandardPack.authority_id,
                StandardPack.created_at,
            ).order_by(StandardPack.code, StandardPack.id)
        elif kind == "bindings":
            query = select(
                StandardPackSourceBinding.id,
                StandardPackSourceBinding.standard_pack_version_id,
                StandardPackSourceBinding.knowledge_source_id,
                StandardPackSourceBinding.document_version_id,
                StandardPackSourceBinding.document_checksum,
                StandardPackSourceBinding.binding_type,
                StandardPackSourceBinding.required,
                StandardPackSourceBinding.created_at,
            ).order_by(StandardPackSourceBinding.created_at, StandardPackSourceBinding.id)
        else:
            query = (
                select(
                    StandardPackVersion.id,
                    StandardPackVersion.standard_pack_id,
                    StandardPack.code,
                    StandardPack.name,
                    StandardPackVersion.version_label,
                    StandardPackVersion.status,
                    StandardPack.assessment_family,
                    StandardPack.language,
                    StandardPackVersion.supported_levels,
                    StandardPackVersion.allowed_classifications,
                    StandardPackVersion.effective_from,
                    StandardPackVersion.effective_until,
                    StandardPackVersion.configuration_checksum,
                )
                .join(StandardPack)
                .order_by(StandardPack.code, StandardPackVersion.created_at, StandardPackVersion.id)
            )
        return ControlCenterRepository.page(session, query, offset, limit)

    @staticmethod
    def sources(session: Session, kind: str, offset: int, limit: int) -> dict[str, Any]:
        query: MetadataQuery
        if kind == "documents":
            query = (
                select(
                    KnowledgeDocumentVersion.id,
                    KnowledgeDocumentVersion.source_id,
                    KnowledgeSource.title.label("source_title"),
                    KnowledgeDocumentVersion.version_label,
                    KnowledgeDocumentVersion.status,
                    KnowledgeDocumentVersion.effective_from,
                    KnowledgeDocumentVersion.effective_until,
                    KnowledgeDocumentVersion.sha256,
                    KnowledgeDocumentVersion.page_count,
                    KnowledgeDocumentVersion.created_at,
                    KnowledgeLegalMetadata.copyright_status,
                    KnowledgeLegalMetadata.licence_type,
                    KnowledgeLegalMetadata.legal_review_status,
                    KnowledgeLegalMetadata.ai_processing_allowed,
                    KnowledgeLegalMetadata.redistribution_allowed,
                )
                .join(KnowledgeSource)
                .outerjoin(
                    KnowledgeLegalMetadata,
                    KnowledgeLegalMetadata.document_version_id == KnowledgeDocumentVersion.id,
                )
                .order_by(KnowledgeDocumentVersion.created_at, KnowledgeDocumentVersion.id)
            )
        elif kind == "ingestions":
            query = select(
                KnowledgeIngestionJob.id,
                KnowledgeIngestionJob.document_version_id,
                KnowledgeIngestionJob.status,
                KnowledgeIngestionJob.created_at,
                KnowledgeIngestionJob.started_at,
                KnowledgeIngestionJob.finished_at,
                KnowledgeIngestionJob.pages_detected,
            ).order_by(KnowledgeIngestionJob.created_at, KnowledgeIngestionJob.id)
        else:
            query = select(
                KnowledgeSource.id,
                KnowledgeSource.source_code,
                KnowledgeSource.title,
                KnowledgeSource.authority_id,
                KnowledgeSource.assessment_family,
                KnowledgeSource.language,
                KnowledgeSource.cefr_level,
                KnowledgeSource.status,
                KnowledgeSource.document_type,
            ).order_by(KnowledgeSource.source_code, KnowledgeSource.id)
        return ControlCenterRepository.page(session, query, offset, limit)

    @staticmethod
    def question_bank(
        session: Session,
        offset: int,
        limit: int,
        organisation_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        query = (
            select(
                QuestionBankItemVersion.id,
                QuestionBankItemVersion.item_id,
                QuestionBankItem.code,
                QuestionBankItem.scope_type,
                QuestionBankItem.organisation_id,
                QuestionBankItemVersion.version_label,
                QuestionBankItemVersion.status,
                QuestionBankItemVersion.assessment_family,
                QuestionBankItemVersion.language,
                QuestionBankItemVersion.skill,
                QuestionBankItemVersion.task_type,
                QuestionBankItemVersion.rights_status,
                QuestionBankItemVersion.origin_type,
                QuestionBankItemVersion.checksum,
                QuestionBankItemVersion.created_at,
            )
            .join(QuestionBankItem)
            .where(
                QuestionBankItem.organisation_id == organisation_id
                if organisation_id
                else QuestionBankItem.scope_type == "PLATFORM_SHARED"
            )
            .order_by(
                QuestionBankItem.code,
                QuestionBankItemVersion.created_at,
                QuestionBankItemVersion.id,
            )
        )
        return ControlCenterRepository.page(session, query, offset, limit)

    @staticmethod
    def exams(
        session: Session,
        offset: int,
        limit: int,
        organisation_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        query = (
            select(
                ExamVersion.id,
                ExamVersion.exam_id,
                Exam.code,
                Exam.scope_type,
                Exam.organisation_id,
                ExamVersion.version_label,
                ExamVersion.status,
                ExamVersion.classification,
                ExamVersion.standard_pack_version_id,
                ExamVersion.origin_type,
                ExamVersion.checksum,
                ExamVersion.published_at,
                ExamVersion.created_at,
            )
            .join(Exam)
            .where(
                Exam.organisation_id == organisation_id
                if organisation_id
                else Exam.scope_type == "PLATFORM_SHARED"
            )
            .order_by(Exam.code, ExamVersion.created_at, ExamVersion.id)
        )
        return ControlCenterRepository.page(session, query, offset, limit)

    @staticmethod
    def audit(
        session: Session,
        offset: int,
        limit: int,
        organisation_id: uuid.UUID | None,
        event_type: str | None,
    ) -> dict[str, Any]:
        query = select(
            AuditEvent.id,
            AuditEvent.timestamp,
            AuditEvent.actor_type,
            AuditEvent.actor_id,
            AuditEvent.organisation_id,
            AuditEvent.event_type,
            AuditEvent.entity_type,
            AuditEvent.entity_id,
            AuditEvent.request_id,
            AuditEvent.workflow_id,
            AuditEvent.metadata_json["operation_id"].as_string().label("operation_id"),
            AuditEvent.metadata_json["source_version_id"].as_string().label("source_version_id"),
            AuditEvent.new_state_hash.label("_state_checksum"),
            AuditEvent.metadata_json["checksum"].as_string().label("_artifact_checksum"),
            AuditEvent.metadata_json["sha256"].as_string().label("_audio_sha256"),
            AuditEvent.metadata_json["candidate_media_asset_id"].as_string().label("_audio_asset"),
            AuditEvent.metadata_json["candidate_response_revision_id"]
            .as_string()
            .label("_response_revision"),
            AuditEvent.metadata_json["result_version_id"].as_string().label("_result_version"),
            AuditEvent.metadata_json["source_result_version_id"]
            .as_string()
            .label("_source_result"),
            AuditEvent.metadata_json["new_component_version_id"]
            .as_string()
            .label("_new_component"),
            AuditEvent.metadata_json["source_component_version_id"]
            .as_string()
            .label("_source_component"),
            AuditEvent.metadata_json["source_scoring_run_id"].as_string().label("_source_run"),
            AuditEvent.metadata_json["new_scoring_run_id"].as_string().label("_new_run"),
            AuditEvent.metadata_json["new_result_version_id"].as_string().label("_new_result"),
        )
        if organisation_id is not None:
            query = query.where(AuditEvent.organisation_id == organisation_id)
        if event_type is not None:
            query = query.where(AuditEvent.event_type == event_type)
        return ControlCenterRepository.page(
            session, query.order_by(AuditEvent.timestamp.desc(), AuditEvent.id), offset, limit
        )

    @staticmethod
    def ai_operations(
        session: Session,
        kind: str,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        query: MetadataQuery
        if kind == "writing-proposals":
            review_status = (
                select(HumanReviewRequest.status)
                .where(HumanReviewRequest.entity_id == WritingExecutionJob.assessment_version_id)
                .order_by(HumanReviewRequest.requested_at.desc(), HumanReviewRequest.id.desc())
                .limit(1)
                .scalar_subquery()
            )
            query = select(
                WritingExecutionJob.id,
                WritingExecutionJob.organisation_id,
                WritingExecutionJob.status,
                WritingExecutionJob.execution_profile_id,
                WritingExecutionJob.profile_version,
                WritingExecutionJob.execution_snapshot["model_version"]
                .as_string()
                .label("model_version"),
                WritingExecutionJob.execution_snapshot["provider"].as_string().label("provider"),
                WritingExecutionJob.execution_snapshot["model_name"]
                .as_string()
                .label("model_name"),
                WritingExecutionJob.execution_snapshot["adapter_version"]
                .as_string()
                .label("adapter_version"),
                WritingExecutionJob.execution_snapshot["workflow_version_id"]
                .as_string()
                .label("workflow_version_id"),
                WritingExecutionJob.ai_run_id,
                WritingExecutionJob.attempt_count,
                WritingExecutionJob.max_attempts,
                WritingExecutionJob.created_at,
                WritingExecutionJob.updated_at,
                WritingExecutionJob.started_at,
                WritingExecutionJob.completed_at,
                WritingExecutionJob.failure_category,
                review_status.label("human_review_status"),
            ).order_by(WritingExecutionJob.created_at.desc(), WritingExecutionJob.id)
        elif kind == "transcriptions":
            # Intentionally no audio identity, candidate, profile config, object
            # key, transcript, claim token or arbitrary provider payload projection.
            query = select(
                SpeechTranscriptionJob.id,
                SpeechTranscriptionJob.organisation_id,
                SpeechTranscriptionJob.status,
                SpeechTranscriptionJob.profile_id,
                SpeechTranscriptionJob.profile_version,
                SpeechTranscriptionJob.profile_snapshot["provider"].as_string().label("provider"),
                SpeechTranscriptionJob.profile_snapshot["model_name"]
                .as_string()
                .label("model_name"),
                SpeechTranscriptionJob.profile_snapshot["adapter_version"]
                .as_string()
                .label("adapter_version"),
                SpeechTranscriptionJob.attempt_count,
                SpeechTranscriptionJob.max_attempts,
                SpeechTranscriptionJob.created_at,
                SpeechTranscriptionJob.updated_at,
                SpeechTranscriptionJob.started_at,
                SpeechTranscriptionJob.completed_at,
                SpeechTranscriptionJob.failure_category,
            ).order_by(SpeechTranscriptionJob.created_at.desc(), SpeechTranscriptionJob.id)
        elif kind == "workflows":
            query = (
                select(
                    AIWorkflowVersion.id,
                    AIWorkflowVersion.workflow_definition_id,
                    AIWorkflowDefinition.code,
                    AIWorkflowDefinition.name,
                    AIWorkflowVersion.version_label,
                    AIWorkflowVersion.status,
                    AIWorkflowVersion.checksum,
                )
                .join(AIWorkflowDefinition)
                .order_by(AIWorkflowDefinition.code, AIWorkflowVersion.id)
            )
        elif kind == "runs":
            query = select(
                AIRun.id,
                AIRun.status,
                AIRun.workflow_version_id,
                AIRun.agent_version_id,
                AIRun.organisation_id,
                AIRun.standard_pack_version_id,
                AIRun.created_at,
                AIRun.started_at,
                AIRun.completed_at,
                AIRun.request_id,
            ).order_by(AIRun.created_at.desc(), AIRun.id)
        elif kind == "reviews":
            query = (
                select(
                    HumanReviewRequest.id,
                    HumanReviewRequest.review_type,
                    HumanReviewRequest.entity_type,
                    HumanReviewRequest.entity_id,
                    HumanReviewRequest.ai_run_id,
                    HumanReviewRequest.status,
                    HumanReviewRequest.requested_at,
                    HumanReviewRequest.decided_at,
                )
                .where(HumanReviewRequest.ai_run_id.is_not(None))
                .order_by(HumanReviewRequest.requested_at.desc(), HumanReviewRequest.id)
            )
        elif kind == "checkpoints":
            query = select(
                AIWorkflowCheckpoint.id,
                AIWorkflowCheckpoint.run_id,
                AIWorkflowCheckpoint.workflow_version_id,
                AIWorkflowCheckpoint.organisation_id,
                AIWorkflowCheckpoint.status,
                AIWorkflowCheckpoint.created_at,
                AIWorkflowCheckpoint.updated_at,
                AIWorkflowCheckpoint.expires_at,
            ).order_by(AIWorkflowCheckpoint.created_at.desc(), AIWorkflowCheckpoint.id)
        else:
            query = select(
                AIModelProfile.id,
                AIModelProfile.code,
                AIModelProfile.name,
                AIModelProfile.provider,
                AIModelProfile.model_name,
                AIModelProfile.status,
                AIModelProfile.supports_structured_output,
            ).order_by(AIModelProfile.code, AIModelProfile.id)
        return ControlCenterRepository.page(session, query, offset, limit)

    @staticmethod
    def authorizations(session: Session, offset: int, limit: int) -> dict[str, Any]:
        query = select(
            CertificationAuthorization.id,
            CertificationAuthorization.organisation_id,
            CertificationAuthorization.assessment_scope,
            CertificationAuthorization.provider_scope,
            CertificationAuthorization.standard_pack_version_id,
            CertificationAuthorization.classification,
            CertificationAuthorization.status,
            CertificationAuthorization.version,
            CertificationAuthorization.effective_from,
            CertificationAuthorization.effective_until,
            CertificationAuthorization.evidence_reference.ilike("%SYNTHETIC%").label("synthetic"),
        ).order_by(CertificationAuthorization.effective_from.desc(), CertificationAuthorization.id)
        return ControlCenterRepository.page(session, query, offset, limit)

    @staticmethod
    def certificates(
        session: Session, offset: int, limit: int
    ) -> tuple[list[tuple[CertificateRecord, ResultRelease | None]], int]:
        total = ControlCenterRepository.count(session, CertificateRecord)
        records = session.scalars(
            select(CertificateRecord)
            .order_by(CertificateRecord.issued_at.desc(), CertificateRecord.id)
            .offset(offset)
            .limit(limit)
        )
        return [
            (record, session.get(ResultRelease, record.release_id)) for record in records
        ], total

    @staticmethod
    def database_reachable(session: Session) -> bool:
        return bool(session.scalar(text("SELECT 1")) == 1)


OVERVIEW_MODELS = {
    "organisations": Organisation,
    "users": User,
    "standard_packs": StandardPack,
}
