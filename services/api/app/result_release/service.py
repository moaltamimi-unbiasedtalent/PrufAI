from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationService, TenantContext
from app.models import (
    AssessmentResult,
    CandidateExamAttempt,
    CertificateRecord,
    CertificationAuthorization,
    ExamVersion,
    ResultRelease,
    ResultVersion,
    ScoringRun,
    StatementOfResults,
)
from app.models.enums import (
    AssessmentClassification,
    CandidateAttemptStatus,
    CertificateStatus,
    CertificationAuthorizationStatus,
    ReleaseStatus,
    ResultVersionStatus,
    ScoringRunStatus,
    StatementStatus,
)


class ResultReleaseError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _checksum(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


class ResultReleaseService:
    @staticmethod
    def release(
        session: Session,
        context: TenantContext,
        version_id: uuid.UUID,
        *,
        operation_id: str,
        supersedes_release_id: uuid.UUID | None = None,
    ) -> ResultRelease:
        AuthorizationService.require_permission(context, "result.release", context.organisation_id)
        existing = session.scalar(
            select(ResultRelease).where(
                ResultRelease.result_version_id == version_id,
                ResultRelease.operation_id == operation_id,
            )
        )
        if existing is not None:
            return existing
        version = session.scalar(
            select(ResultVersion).where(ResultVersion.id == version_id).with_for_update()
        )
        result = session.get(AssessmentResult, version.result_id) if version else None
        run = session.get(ScoringRun, version.scoring_run_id) if version else None
        attempt = session.get(CandidateExamAttempt, result.attempt_id) if result else None
        exam = session.get(ExamVersion, result.exam_version_id) if result else None
        if version is None or result is None or run is None or attempt is None or exam is None:
            raise ResultReleaseError("result release provenance is incomplete")
        if result.organisation_id != context.organisation_id:
            raise ResultReleaseError("result is not available in this organisation")
        if version.status is not ResultVersionStatus.RELEASED:
            raise ResultReleaseError("result version must be released by an approved human action")
        active_release = session.scalar(
            select(ResultRelease).where(
                ResultRelease.result_version_id == version.id,
                ResultRelease.status == ReleaseStatus.RELEASED,
            )
        )
        if active_release is not None:
            raise ResultReleaseError("result version already has an authoritative release")
        if run.status is not ScoringRunStatus.COMPLETED:
            raise ResultReleaseError("scoring run is not complete")
        if attempt.status is not CandidateAttemptStatus.SUBMITTED:
            raise ResultReleaseError("candidate attempt is not complete")
        classification = exam.classification.value
        if classification == AssessmentClassification.OFFICIAL_CERTIFICATION.value:
            now = _now()
            authorized = session.scalar(
                select(CertificationAuthorization).where(
                    CertificationAuthorization.organisation_id == result.organisation_id,
                    CertificationAuthorization.assessment_scope == classification,
                    CertificationAuthorization.status == CertificationAuthorizationStatus.ACTIVE,
                    CertificationAuthorization.effective_from <= now,
                    (CertificationAuthorization.effective_until.is_(None))
                    | (CertificationAuthorization.effective_until > now),
                )
            )
            if authorized is None:
                raise ResultReleaseError("CERTIFICATION.NOT_AUTHORIZED")
        payload = {
            "result_version_id": str(version.id),
            "result_checksum": version.checksum,
            "scoring_run_id": str(run.id),
            "scoring_run_checksum": run.checksum,
            "standard_pack_version_id": str(exam.standard_pack_version_id),
            "classification": classification,
        }
        release = ResultRelease(
            organisation_id=result.organisation_id,
            candidate_user_id=result.candidate_user_id,
            attempt_id=result.attempt_id,
            assessment_result_id=result.id,
            result_version_id=version.id,
            scoring_run_id=run.id,
            standard_pack_version_id=exam.standard_pack_version_id,
            released_by_user_id=context.user_id,
            released_at=_now(),
            classification=classification,
            status=ReleaseStatus.RELEASED,
            operation_id=operation_id,
            checksum=_checksum(payload),
            supersedes_release_id=supersedes_release_id,
            metadata_json=payload,
        )
        if supersedes_release_id is not None:
            previous = session.get(ResultRelease, supersedes_release_id)
            if previous is None or previous.organisation_id != result.organisation_id:
                raise ResultReleaseError("superseded release is not available")
            previous.status = ReleaseStatus.SUPERSEDED
        session.add(release)
        session.flush()
        ResultReleaseService._audit(session, context, "result.release_created", release)
        return release

    @staticmethod
    def statement(
        session: Session, context: TenantContext, release_id: uuid.UUID, *, operation_id: str
    ) -> StatementOfResults:
        release = ResultReleaseService._owned_release(session, context, release_id)
        existing = session.scalar(
            select(StatementOfResults).where(
                StatementOfResults.release_id == release.id,
                StatementOfResults.operation_id == operation_id,
            )
        )
        if existing is not None:
            return existing
        payload = {
            "statement_type": "Statement of Results",
            "classification": release.classification,
            "result_release_id": str(release.id),
            "result_version_id": str(release.result_version_id),
            "candidate_user_id": str(release.candidate_user_id),
            "organisation_id": str(release.organisation_id),
        }
        artifact = StatementOfResults(
            release_id=release.id,
            result_version_id=release.result_version_id,
            scoring_run_id=release.scoring_run_id,
            standard_pack_version_id=release.standard_pack_version_id,
            organisation_id=release.organisation_id,
            candidate_user_id=release.candidate_user_id,
            attempt_id=release.attempt_id,
            classification=release.classification,
            statement_version=1,
            issued_at=_now(),
            checksum=_checksum(payload),
            status=StatementStatus.ISSUED,
            operation_id=operation_id,
            payload=payload,
        )
        session.add(artifact)
        session.flush()
        ResultReleaseService._audit(session, context, "statement.created", artifact)
        return artifact

    @staticmethod
    def authorize(
        session: Session,
        context: TenantContext,
        *,
        assessment_scope: str,
        provider_scope: str,
        evidence_reference: str,
        effective_from: datetime,
    ) -> CertificationAuthorization:
        AuthorizationService.require_permission(context, "result.manage", context.organisation_id)
        if not evidence_reference.strip():
            raise ResultReleaseError("authorization evidence is required")
        authorization = CertificationAuthorization(
            organisation_id=context.organisation_id,
            assessment_scope=assessment_scope,
            provider_scope=provider_scope,
            classification=AssessmentClassification.OFFICIAL_CERTIFICATION.value,
            effective_from=effective_from,
            status=CertificationAuthorizationStatus.ACTIVE,
            evidence_reference=evidence_reference,
            approved_by_user_id=context.user_id,
            checksum=_checksum(
                {
                    "scope": assessment_scope,
                    "provider": provider_scope,
                    "evidence": evidence_reference,
                }
            ),
            version=1,
        )
        session.add(authorization)
        session.flush()
        ResultReleaseService._audit(
            session, context, "certification.authorization_created", authorization
        )
        return authorization

    @staticmethod
    def certificate(
        session: Session,
        context: TenantContext,
        release_id: uuid.UUID,
        authorization_id: uuid.UUID,
        *,
        operation_id: str,
    ) -> CertificateRecord:
        AuthorizationService.require_permission(context, "result.manage", context.organisation_id)
        release = ResultReleaseService._owned_release(session, context, release_id)
        existing = session.scalar(
            select(CertificateRecord).where(
                CertificateRecord.release_id == release.id,
                CertificateRecord.operation_id == operation_id,
            )
        )
        if existing is not None:
            return existing
        auth = session.get(CertificationAuthorization, authorization_id)
        now = _now()
        if (
            auth is None
            or auth.organisation_id != release.organisation_id
            or auth.status is not CertificationAuthorizationStatus.ACTIVE
            or auth.effective_from > now
            or (auth.effective_until is not None and auth.effective_until <= now)
            or auth.assessment_scope != release.classification
        ):
            raise ResultReleaseError("CERTIFICATION.NOT_AUTHORIZED")
        certificate = CertificateRecord(
            release_id=release.id,
            result_version_id=release.result_version_id,
            authorization_id=auth.id,
            organisation_id=release.organisation_id,
            candidate_user_id=release.candidate_user_id,
            issued_at=now,
            serial=f"PRUFAI-{uuid.uuid4().hex.upper()}",
            checksum=_checksum(
                {"release": str(release.id), "authorization": str(auth.id), "version": auth.version}
            ),
            status=CertificateStatus.ISSUED,
            operation_id=operation_id,
        )
        session.add(certificate)
        session.flush()
        ResultReleaseService._audit(session, context, "certificate.issued", certificate)
        return certificate

    @staticmethod
    def candidate_releases(session: Session, context: TenantContext) -> list[ResultRelease]:
        return list(
            session.scalars(
                select(ResultRelease).where(
                    ResultRelease.organisation_id == context.organisation_id,
                    ResultRelease.candidate_user_id == context.user_id,
                    ResultRelease.status == ReleaseStatus.RELEASED,
                )
            )
        )

    @staticmethod
    def organisation_releases(session: Session, context: TenantContext) -> list[ResultRelease]:
        AuthorizationService.require_permission(context, "result.read", context.organisation_id)
        return list(
            session.scalars(
                select(ResultRelease)
                .where(ResultRelease.organisation_id == context.organisation_id)
                .order_by(ResultRelease.released_at.desc())
            )
        )

    @staticmethod
    def _owned_release(
        session: Session, context: TenantContext, release_id: uuid.UUID
    ) -> ResultRelease:
        release = session.get(ResultRelease, release_id)
        if release is None or release.organisation_id != context.organisation_id:
            raise ResultReleaseError("release is not available in this organisation")
        return release

    @staticmethod
    def _audit(session: Session, context: TenantContext, event: str, entity: Any) -> None:
        if context.organisation_id and AuthorizationService.has_permission(
            context, "audit.read", context.organisation_id
        ):
            AuditService.record(
                session,
                context,
                event_type=event,
                entity_type=type(entity).__name__,
                entity_id=str(entity.id),
                organisation_id=context.organisation_id,
                require_audit_permission=False,
            )
