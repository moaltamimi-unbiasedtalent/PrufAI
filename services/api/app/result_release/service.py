from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.config import get_settings
from app.models import (
    AssessmentResult,
    AuditEvent,
    CandidateExamAttempt,
    CertificateRecord,
    CertificationAuthorization,
    ExamVersion,
    ResultAppeal,
    ResultAppealVersion,
    ResultCorrection,
    ResultModerationCase,
    ResultRelease,
    ResultVersion,
    ScoringRun,
    StandardPackVersion,
    StatementOfResults,
)
from app.models.enums import (
    AppealDecision,
    CandidateAttemptStatus,
    CertificateStatus,
    CertificationAuthorizationStatus,
    CorrectionStatus,
    ModerationStatus,
    ReleaseStatus,
    ResultVersionStatus,
    ScoringRunStatus,
    StatementStatus,
)
from app.scoring.service import ScoringService


class ResultReleaseError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _checksum(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _operation(value: str) -> None:
    if not value.strip() or len(value) > 120:
        raise ResultReleaseError("a bounded operation_id is required")


class ResultReleaseService:
    """Lock the AssessmentResult aggregate before every consequential action.

    Replays authenticate and scope before lookup. Artifact provenance stays
    immutable; validity is derived from release, current result and authorization.
    """

    @staticmethod
    def _version(
        session: Session, context: TenantContext, version_id: uuid.UUID
    ) -> tuple[ResultVersion, AssessmentResult, ScoringRun, CandidateExamAttempt, ExamVersion]:
        version = session.get(ResultVersion, version_id)
        result = (
            session.scalar(
                select(AssessmentResult)
                .where(
                    AssessmentResult.id == version.result_id,
                    AssessmentResult.organisation_id == context.organisation_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if version
            else None
        )
        if result is None or result.organisation_id != context.organisation_id:
            raise ResultReleaseError("result is not available in this organisation")
        version = session.scalar(
            select(ResultVersion)
            .where(ResultVersion.id == version_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        run = session.get(ScoringRun, version.scoring_run_id) if version else None
        attempt = session.get(CandidateExamAttempt, result.attempt_id)
        exam = session.get(ExamVersion, result.exam_version_id)
        if version is None or run is None or attempt is None or exam is None:
            raise ResultReleaseError("result release provenance is incomplete")
        if (
            run.organisation_id != result.organisation_id
            or run.candidate_user_id != result.candidate_user_id
            or run.attempt_id != result.attempt_id
            or run.exam_version_id != result.exam_version_id
            or attempt.organisation_id != result.organisation_id
            or attempt.candidate_user_id != result.candidate_user_id
            or attempt.exam_version_id != exam.id
            or run.standard_pack_version_id != exam.standard_pack_version_id
            or str(attempt.runtime_plan.get("standard_pack_version_id"))
            != str(run.standard_pack_version_id)
            or version.scoring_run_checksum != run.checksum
            or version.response_set_checksum != run.response_set_checksum
        ):
            raise ResultReleaseError("result release provenance does not match")
        return version, result, run, attempt, exam

    @staticmethod
    def _latest(session: Session, version: ResultVersion) -> bool:
        latest = session.scalar(
            select(ResultVersion.id)
            .where(ResultVersion.result_id == version.result_id)
            .order_by(ResultVersion.version_number.desc())
            .limit(1)
        )
        return latest == version.id

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
        _operation(operation_id)
        version, result, run, attempt, exam = ResultReleaseService._version(
            session, context, version_id
        )
        existing = session.scalar(
            select(ResultRelease).where(ResultRelease.result_version_id == version.id)
        )
        if existing is not None:
            if (
                existing.operation_id != operation_id
                or existing.supersedes_release_id != supersedes_release_id
            ):
                raise ResultReleaseError("result version already has an authoritative release")
            ResultReleaseService._check_release(existing, version, result, run, exam)
            ResultReleaseService._active(session, existing)
            return existing
        if not ResultReleaseService._latest(session, version):
            raise ResultReleaseError("stale result version")
        if ResultReleaseService._moderation_blocked(session, version.id):
            raise ResultReleaseError("result is blocked by moderation")
        if (
            run.status is not ScoringRunStatus.COMPLETED
            or attempt.status is not CandidateAttemptStatus.SUBMITTED
        ):
            raise ResultReleaseError("result release provenance is not complete")
        # Provider alignment and CEFR labels never authorize official delivery.
        # This remains denied even when synthetic/nonofficial issuance is enabled.
        if exam.classification.value == "OFFICIAL_CERTIFICATION":
            raise ResultReleaseError("CERTIFICATION.NOT_AUTHORIZED")
        prior = list(
            session.scalars(
                select(ResultRelease).where(ResultRelease.assessment_result_id == result.id)
            )
        )
        previous = None
        if supersedes_release_id is not None:
            previous = ResultReleaseService._owned_release(session, context, supersedes_release_id)
            ResultReleaseService._successor(session, previous, version, result, run, exam)
        elif prior:
            raise ResultReleaseError("successor release requires exact predecessor lineage")
        if version.status is ResultVersionStatus.APPROVED:
            ScoringService.release(session, context, version.id)
        elif version.status is not ResultVersionStatus.RELEASED:
            raise ResultReleaseError("result version requires approved human review")
        if any(
            row.get("status") in {"FAIL", "ERROR"}
            for row in version.rule_provenance.get("evaluations", [])
        ):
            raise ResultReleaseError("result is blocked by governed RulesEngine evaluation")
        payload = {
            "result_version_id": str(version.id),
            "result_checksum": version.checksum,
            "scoring_run_id": str(run.id),
            "scoring_run_checksum": run.checksum,
            "standard_pack_version_id": str(exam.standard_pack_version_id),
            "classification": exam.classification.value,
            "supersedes_release_id": str(supersedes_release_id) if supersedes_release_id else None,
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
            classification=exam.classification.value,
            status=ReleaseStatus.RELEASED,
            operation_id=operation_id,
            checksum=_checksum(payload),
            supersedes_release_id=supersedes_release_id,
            metadata_json=payload,
        )
        session.add(release)
        session.flush()
        if previous is not None:
            previous.status = ReleaseStatus.SUPERSEDED
            ResultReleaseService._audit(session, context, "result.release_superseded", previous)
        ResultReleaseService._audit(session, context, "result.release_created", release)
        return release

    @staticmethod
    def _successor(
        session: Session,
        previous: ResultRelease,
        version: ResultVersion,
        result: AssessmentResult,
        run: ScoringRun,
        exam: ExamVersion,
    ) -> None:
        if (
            previous.status is not ReleaseStatus.RELEASED
            or previous.assessment_result_id != result.id
            or previous.candidate_user_id != result.candidate_user_id
            or previous.attempt_id != result.attempt_id
            or previous.standard_pack_version_id != exam.standard_pack_version_id
            or version.supersedes_version_id != previous.result_version_id
        ):
            raise ResultReleaseError("successor release lineage does not match")
        correction = session.scalar(
            select(ResultCorrection).where(
                ResultCorrection.organisation_id == result.organisation_id,
                ResultCorrection.source_result_version_id == previous.result_version_id,
                ResultCorrection.new_result_version_id == version.id,
                ResultCorrection.new_scoring_run_id == run.id,
                ResultCorrection.status == CorrectionStatus.RESCORED,
            )
        )
        appeal = session.get(ResultAppeal, correction.appeal_id) if correction else None
        decision = (
            session.get(ResultAppealVersion, correction.appeal_version_id) if correction else None
        )
        if (
            correction is None
            or appeal is None
            or decision is None
            or correction.source_result_checksum != previous.metadata_json.get("result_checksum")
            or appeal.organisation_id != result.organisation_id
            or appeal.result_id != result.id
            or appeal.candidate_user_id != result.candidate_user_id
            or appeal.attempt_id != result.attempt_id
            or appeal.challenged_result_version_id != previous.result_version_id
            or appeal.scoring_run_id != previous.scoring_run_id
            or appeal.standard_pack_version_id != exam.standard_pack_version_id
            or decision.appeal_id != appeal.id
            or decision.checksum != appeal.current_checksum
            or decision.version_number != appeal.current_version_number
            or decision.decision not in {AppealDecision.UPHOLD, AppealDecision.PARTIALLY_UPHOLD}
        ):
            raise ResultReleaseError("successor requires the exact governed correction and appeal")

    @staticmethod
    def _check_release(
        release: ResultRelease,
        version: ResultVersion,
        result: AssessmentResult,
        run: ScoringRun,
        exam: ExamVersion,
    ) -> None:
        if (
            release.organisation_id != result.organisation_id
            or release.candidate_user_id != result.candidate_user_id
            or release.attempt_id != result.attempt_id
            or release.assessment_result_id != result.id
            or release.result_version_id != version.id
            or release.scoring_run_id != run.id
            or release.standard_pack_version_id != exam.standard_pack_version_id
            or release.classification != exam.classification.value
            or release.metadata_json.get("result_checksum") != version.checksum
            or release.metadata_json.get("scoring_run_checksum") != run.checksum
        ):
            raise ResultReleaseError("release provenance does not match")

    @staticmethod
    def _owned_release(
        session: Session, context: TenantContext, release_id: uuid.UUID
    ) -> ResultRelease:
        release = session.get(ResultRelease, release_id)
        if (
            release is None
            or context.organisation_id is None
            or release.organisation_id != context.organisation_id
        ):
            raise ResultReleaseError("release is not available in this organisation")
        version, result, run, _, exam = ResultReleaseService._version(
            session, context, release.result_version_id
        )
        session.refresh(release)
        ResultReleaseService._check_release(release, version, result, run, exam)
        return release

    @staticmethod
    def effective_status(session: Session, release: ResultRelease) -> str:
        if release.status is not ReleaseStatus.RELEASED:
            return str(release.status.value)
        version = session.get(ResultVersion, release.result_version_id)
        if (
            version is None
            or version.status is not ResultVersionStatus.RELEASED
            or not ResultReleaseService._latest(session, version)
        ):
            return "BLOCKED"
        return (
            "BLOCKED"
            if ResultReleaseService._moderation_blocked(session, version.id)
            else "RELEASED"
        )

    @staticmethod
    def _moderation_blocked(session: Session, version_id: uuid.UUID) -> bool:
        blocked = session.scalar(
            select(ResultModerationCase.id)
            .where(
                ResultModerationCase.result_version_id == version_id,
                ResultModerationCase.status.in_(
                    [
                        ModerationStatus.OPEN,
                        ModerationStatus.IN_REVIEW,
                        ModerationStatus.CORRECTION_REQUIRED,
                        ModerationStatus.ESCALATED,
                    ]
                ),
            )
            .limit(1)
        )
        return blocked is not None

    @staticmethod
    def _active(session: Session, release: ResultRelease) -> None:
        if ResultReleaseService.effective_status(session, release) != "RELEASED":
            raise ResultReleaseError("release is not active or is stale")

    @staticmethod
    def statement(
        session: Session, context: TenantContext, release_id: uuid.UUID, *, operation_id: str
    ) -> StatementOfResults:
        AuthorizationService.require_permission(context, "result.release", context.organisation_id)
        _operation(operation_id)
        release = ResultReleaseService._owned_release(session, context, release_id)
        ResultReleaseService._active(session, release)
        existing = session.scalar(
            select(StatementOfResults).where(StatementOfResults.release_id == release.id)
        )
        if existing is not None:
            if existing.operation_id != operation_id:
                raise ResultReleaseError("statement already issued with a different operation")
            ResultReleaseService._artifact_scope(existing, release)
            return existing
        ResultReleaseService._active(session, release)
        version = session.get(ResultVersion, release.result_version_id)
        if version is None:
            raise ResultReleaseError("statement result version is unavailable")
        payload = {
            "statement_type": "Statement of Results",
            "issued_by_user_id": str(context.user_id),
            "raw_awarded_points": str(version.raw_awarded_points)
            if version.raw_awarded_points is not None
            else None,
            "raw_maximum_points": str(version.raw_maximum_points)
            if version.raw_maximum_points is not None
            else None,
            "outcome": version.outcome.value if version.outcome else None,
            "result_checksum": version.checksum,
            "classification": release.classification,
            "result_release_id": str(release.id),
            "result_version_id": str(release.result_version_id),
            "scoring_run_id": str(release.scoring_run_id),
            "standard_pack_version_id": str(release.standard_pack_version_id),
            "candidate_user_id": str(release.candidate_user_id),
            "organisation_id": str(release.organisation_id),
            "attempt_id": str(release.attempt_id),
            "release_checksum": release.checksum,
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
        standard_pack_version_id: uuid.UUID,
        operation_id: str,
        evidence_reference: str,
        effective_from: datetime,
        effective_until: datetime | None = None,
    ) -> CertificationAuthorization:
        if (
            "certification.authorize" not in context.platform_permissions
            or context.organisation_id is None
        ):
            raise AuthorizationError(
                "explicit platform certification authority and organisation context required"
            )
        if not get_settings().certification_issuance_enabled:
            raise ResultReleaseError("CERTIFICATION.NOT_AUTHORIZED")
        if assessment_scope == "OFFICIAL_CERTIFICATION":
            raise ResultReleaseError("CERTIFICATION.NOT_AUTHORIZED")
        _operation(operation_id)
        pack = session.get(StandardPackVersion, standard_pack_version_id)
        if (
            pack is None
            or provider_scope != pack.standard_pack.code
            or assessment_scope not in (pack.allowed_classifications or [])
        ):
            raise ResultReleaseError("authorization scope does not match the exact Standard Pack")
        if not evidence_reference.strip() or len(evidence_reference) > 240:
            raise ResultReleaseError("authorization evidence is required")
        if effective_until is not None and _utc(effective_until) <= _utc(effective_from):
            raise ResultReleaseError("authorization effective dates are invalid")
        payload = {
            "scope": assessment_scope,
            "provider": provider_scope,
            "pack": str(pack.id),
            "evidence": evidence_reference,
            "effective_from": _utc(effective_from).isoformat(),
            "effective_until": _utc(effective_until).isoformat() if effective_until else None,
        }
        existing = session.scalar(
            select(CertificationAuthorization)
            .where(
                CertificationAuthorization.organisation_id == context.organisation_id,
                CertificationAuthorization.operation_id == operation_id,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.checksum != _checksum(payload):
                raise ResultReleaseError("authorization operation has different scope")
            return existing
        authorization = CertificationAuthorization(
            organisation_id=context.organisation_id,
            assessment_scope=assessment_scope,
            provider_scope=provider_scope,
            standard_pack_version_id=pack.id,
            operation_id=operation_id,
            classification=assessment_scope,
            effective_from=_utc(effective_from),
            effective_until=_utc(effective_until) if effective_until else None,
            status=CertificationAuthorizationStatus.ACTIVE,
            evidence_reference=evidence_reference,
            approved_by_user_id=context.user_id,
            checksum=_checksum(payload),
            version=1,
        )
        session.add(authorization)
        session.flush()
        ResultReleaseService._audit(
            session, context, "certification.authorization_created", authorization
        )
        return authorization

    @staticmethod
    def _authorization_valid(
        session: Session, release: ResultRelease, auth: CertificationAuthorization | None
    ) -> bool:
        now = _now()
        pack = session.get(StandardPackVersion, release.standard_pack_version_id)
        return bool(
            get_settings().certification_issuance_enabled
            and release.classification != "OFFICIAL_CERTIFICATION"
            and auth is not None
            and pack is not None
            and auth.organisation_id == release.organisation_id
            and auth.standard_pack_version_id == release.standard_pack_version_id
            and auth.provider_scope == pack.standard_pack.code
            and auth.classification == release.classification
            and auth.assessment_scope == release.classification
            and auth.status is CertificationAuthorizationStatus.ACTIVE
            and _utc(auth.effective_from) <= now
            and (auth.effective_until is None or _utc(auth.effective_until) > now)
        )

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
        _operation(operation_id)
        release = ResultReleaseService._owned_release(session, context, release_id)
        auth = session.scalar(
            select(CertificationAuthorization)
            .where(CertificationAuthorization.id == authorization_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if auth is None or auth.organisation_id != context.organisation_id:
            raise ResultReleaseError("CERTIFICATION.NOT_AUTHORIZED")
        ResultReleaseService._active(session, release)
        if not ResultReleaseService._authorization_valid(session, release, auth):
            raise ResultReleaseError("CERTIFICATION.NOT_AUTHORIZED")
        existing = session.scalar(
            select(CertificateRecord).where(CertificateRecord.release_id == release.id)
        )
        if existing is not None:
            if (
                existing.operation_id != operation_id
                or existing.authorization_id != authorization_id
                or existing.authorization_version != auth.version
            ):
                raise ResultReleaseError("certificate operation or authorization does not match")
            ResultReleaseService._artifact_scope(existing, release)
            return existing
        ResultReleaseService._active(session, release)
        if not ResultReleaseService._authorization_valid(session, release, auth):
            raise ResultReleaseError("CERTIFICATION.NOT_AUTHORIZED")
        artifact = CertificateRecord(
            release_id=release.id,
            result_version_id=release.result_version_id,
            authorization_id=auth.id,
            authorization_version=auth.version,
            organisation_id=release.organisation_id,
            candidate_user_id=release.candidate_user_id,
            issued_at=_now(),
            serial=f"PRUFAI-{uuid.uuid4().hex.upper()}",
            checksum=_checksum(
                {
                    "release": str(release.id),
                    "release_checksum": release.checksum,
                    "authorization": str(auth.id),
                    "authorization_version": auth.version,
                    "authorization_checksum": auth.checksum,
                }
            ),
            status=CertificateStatus.ISSUED,
            operation_id=operation_id,
        )
        session.add(artifact)
        session.flush()
        ResultReleaseService._audit(session, context, "certificate.issued", artifact)
        return artifact

    @staticmethod
    def _artifact_scope(artifact: Any, release: ResultRelease) -> None:
        if (
            artifact.organisation_id != release.organisation_id
            or artifact.candidate_user_id != release.candidate_user_id
            or artifact.result_version_id != release.result_version_id
            or artifact.release_id != release.id
        ):
            raise ResultReleaseError("artifact provenance does not match")
        if isinstance(artifact, StatementOfResults) and (
            artifact.attempt_id != release.attempt_id
            or artifact.scoring_run_id != release.scoring_run_id
            or artifact.standard_pack_version_id != release.standard_pack_version_id
            or artifact.classification != release.classification
        ):
            raise ResultReleaseError("statement provenance does not match")

    @staticmethod
    def artifact_status(
        session: Session, release: ResultRelease, artifact: StatementOfResults | CertificateRecord
    ) -> str:
        ResultReleaseService._artifact_scope(artifact, release)
        parent = ResultReleaseService.effective_status(session, release)
        if parent != "RELEASED":
            return parent
        if isinstance(artifact, CertificateRecord):
            auth = session.get(CertificationAuthorization, artifact.authorization_id)
            if (
                auth is None
                or artifact.authorization_version != auth.version
                or not ResultReleaseService._authorization_valid(session, release, auth)
            ):
                return "REVOKED"
        return str(artifact.status.value)

    @staticmethod
    def _revoke_replay(
        session: Session,
        context: TenantContext,
        entity: Any,
        reason: str,
        event: str,
        operation_id: str | None = None,
    ) -> None:
        if not reason.strip() or len(reason) > 240:
            raise ResultReleaseError("a bounded revocation reason is required")
        replay_id = operation_id or _checksum({"entity": str(entity.id), "reason": reason})
        _operation(replay_id)
        if entity.status.value == "REVOKED":
            original = session.scalar(
                select(AuditEvent)
                .where(AuditEvent.entity_id == str(entity.id), AuditEvent.event_type == event)
                .order_by(AuditEvent.timestamp.desc())
                .limit(1)
            )
            if (
                original is None
                or original.reason != reason
                or (original.metadata_json or {}).get("operation_id") != replay_id
            ):
                raise ResultReleaseError("revocation replay reason does not match")
            return
        if entity.status.value not in {"ACTIVE", "RELEASED"}:
            raise ResultReleaseError("release or authorization is not active")
        entity.status = (
            ReleaseStatus.REVOKED
            if isinstance(entity, ResultRelease)
            else CertificationAuthorizationStatus.REVOKED
        )
        ResultReleaseService._audit(
            session, context, event, entity, reason=reason, operation_id=replay_id
        )

    @staticmethod
    def revoke_release(
        session: Session,
        context: TenantContext,
        release_id: uuid.UUID,
        reason: str,
        *,
        operation_id: str | None = None,
    ) -> ResultRelease:
        AuthorizationService.require_permission(context, "result.manage", context.organisation_id)
        release = ResultReleaseService._owned_release(session, context, release_id)
        ResultReleaseService._revoke_replay(
            session, context, release, reason, "result.release_revoked", operation_id
        )
        session.flush()
        return release

    @staticmethod
    def revoke_authorization(
        session: Session,
        context: TenantContext,
        authorization_id: uuid.UUID,
        reason: str,
        *,
        operation_id: str | None = None,
    ) -> CertificationAuthorization:
        if (
            "certification.authorize" not in context.platform_permissions
            or context.organisation_id is None
        ):
            raise AuthorizationError(
                "explicit platform certification authority and organisation context required"
            )
        auth = session.scalar(
            select(CertificationAuthorization)
            .where(CertificationAuthorization.id == authorization_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if auth is None or auth.organisation_id != context.organisation_id:
            raise ResultReleaseError("authorization is not available")
        ResultReleaseService._revoke_replay(
            session, context, auth, reason, "certification.authorization_revoked", operation_id
        )
        session.flush()
        return auth

    @staticmethod
    def candidate_releases(session: Session, context: TenantContext) -> list[ResultRelease]:
        if context.organisation_id is None:
            raise AuthorizationError("organisation context required")
        return list(
            session.scalars(
                select(ResultRelease)
                .where(
                    ResultRelease.organisation_id == context.organisation_id,
                    ResultRelease.candidate_user_id == context.user_id,
                )
                .order_by(ResultRelease.released_at.desc())
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
    def _audit(
        session: Session,
        context: TenantContext,
        event: str,
        entity: Any,
        *,
        reason: str | None = None,
        operation_id: str | None = None,
    ) -> None:
        AuditService.record(
            session,
            context,
            event_type=event,
            entity_type=type(entity).__name__,
            entity_id=str(entity.id),
            organisation_id=context.organisation_id,
            reason=reason,
            metadata={
                "status": entity.status.value,
                "checksum": entity.checksum,
                **({"operation_id": operation_id} if operation_id else {}),
            },
            require_audit_permission=False,
        )
