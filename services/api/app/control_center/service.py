from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError, TenantContext
from app.config import Settings, get_settings
from app.control_center.repository import OVERVIEW_MODELS, ControlCenterRepository
from app.models import AIModelProfile, Base
from app.models.enums import AIArtifactStatus
from app.result_release.service import ResultReleaseService
from app.speech_transcription.runtime import validate_profile_configuration

ENTRY_PERMISSION = "platform.control_center.read"
SECTION_PERMISSIONS = {
    "organisations": ("platform.organisation.read",),
    "standards": ("standard_pack.read", "standards.authority.read"),
    "source-registry": ("standards.source.read", "standards.document.read"),
    "question-bank": ("question_bank.read",),
    "exams": ("exam.read",),
    "audit": ("platform.audit.read",),
    "ai-operations": ("ai.read",),
    "system": ("platform.system.read",),
    "certification": ("certification.read",),
}
SAFE_EVENT_PREFIXES = frozenset(
    {
        "auth",
        "organisation",
        "organisation_site",
        "membership",
        "platform_role",
        "standards",
        "standard_pack",
        "rule",
        "rules",
        "ai",
        "memory",
        "question_bank",
        "exam",
        "exam_assignment",
        "exam_attempt",
        "candidate",
        "candidate_audio",
        "speech_transcription",
        "writing",
        "speaking",
        "result",
        "statement",
        "certificate",
        "certification",
        "appeal",
        "subjective",
        "subjective_assessment",
        "rubric",
        "scoring",
        "result_moderation",
    }
)
SAFE_ENTITY_TYPES = frozenset(
    mapper.class_.__name__ for mapper in Base.registry.mappers
) | frozenset(
    {
        "ai_orchestration",
        "user",
        "auth_session",
        "organisation_invitation",
        "EXAM",
        "EXAM_VERSION",
        "long_term_memory",
        "long_term_memory_version",
        "QUESTION_BANK_ITEM_VERSION",
        "question_bank_item",
        "question_bank_item_version",
        "SUBJECTIVE_ASSESSMENT_VERSION",
        "result_appeal",
        "result_correction",
        "result_moderation_case",
        "rule",
        "RESULT_VERSION",
        "knowledge_authority",
        "knowledge_document_version",
        "knowledge_ingestion_job",
        "knowledge_legal_metadata",
        "knowledge_source",
        "membership_role",
        "organisation",
        "organisation_membership",
        "organisation_site",
        "platform_role_assignment",
        "knowledge_index_job",
        "CANDIDATE_AUDIO",
        "CANDIDATE_ASSIGNMENT",
        "CANDIDATE_ATTEMPT",
        "SPEECH_TRANSCRIPTION",
        "SPEECH_TRANSCRIPTION_JOB",
        "standard_pack",
        "ResultRelease",
        "StatementOfResults",
        "CertificationAuthorization",
        "CertificateRecord",
    }
)


def safe_reference(value: Any) -> str | None:
    """Correlation fields are untrusted strings; emit UUID references only."""
    try:
        return str(uuid.UUID(str(value))) if value is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _audit_references(row: dict[str, Any]) -> list[str]:
    """Only explicit UUIDs and SHA256 digests; never serialize a domain payload."""
    references = []
    for key, label in (
        ("_audio_asset", "Audio asset"),
        ("_response_revision", "Response revision"),
        ("_result_version", "Result version"),
        ("_source_result", "Source result version"),
        ("_source_component", "Source assessment version"),
        ("_source_run", "Source scoring run"),
        ("_new_component", "Successor assessment version"),
        ("_new_run", "Successor scoring run"),
        ("_new_result", "Successor result version"),
    ):
        value = safe_reference(row.pop(key, None))
        if value is not None:
            references.append(f"{label}: {value}")
    for key, label in (
        ("_state_checksum", "Evidence checksum"),
        ("_artifact_checksum", "Artifact checksum"),
        ("_audio_sha256", "Audio SHA256"),
    ):
        value = row.pop(key, None)
        if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value):
            references.append(f"{label}: {value}")
    return references


def _safe_transcription_operation(row: dict[str, Any]) -> None:
    """Whitelisted metadata; even corrupted operational rows cannot expose payloads."""
    for key, values in (
        ("status", {"QUEUED", "RUNNING", "RETRYABLE", "READY", "FAILED", "CANCELLED"}),
        ("provider", {"OPENAI", "DETERMINISTIC_TEST"}),
        ("model_name", {"gpt-transcribe", "fixture-transcriber"}),
        ("adapter_version", {"1"}),
        (
            "failure_category",
            {
                None,
                "PROVIDER_TIMEOUT",
                "PROVIDER_UNAVAILABLE",
                "PROVIDER_UNCONFIGURED",
                "INVALID_PROVIDER_OUTPUT",
                "EMPTY_RESULT",
                "STORAGE_UNAVAILABLE",
                "STORAGE_READ_FAILED",
                "DATABASE_UNAVAILABLE",
                "PERSISTENCE_FAILED",
                "STALE_AUDIO_EVIDENCE",
                "REQUESTER_UNAUTHORIZED",
                "LEASE_EXPIRED",
                "RETRY_EXHAUSTED",
                "CANCELLED",
                "EXECUTION_FAILED",
                "OBJECT_STORAGE_UNAVAILABLE",
                "WORKER_LEASE_EXPIRED",
                "REQUEST_AUTHORITY_REVOKED",
                "PROFILE_APPROVAL_INVALID",
                "UNCONFIGURED",
            },
        ),
    ):
        if row.get(key) not in values:
            row[key] = "REDACTED"
    value = row.get("profile_version")
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        row["profile_version"] = None
    row["test_only"] = row.get("provider") == "DETERMINISTIC_TEST"
    started, completed = row.get("started_at"), row.get("completed_at")
    row["duration_ms"] = (
        max(0, int((completed - started).total_seconds() * 1000))
        if isinstance(started, datetime) and isinstance(completed, datetime)
        else None
    )


def _transcription_executor(session: Session, settings: Settings) -> dict[str, Any]:
    # Availability describes configuration, never a completed provider call or
    # worker liveness. Human assessment and generic AI execution remain separate.
    profiles = session.scalars(
        select(AIModelProfile).where(
            AIModelProfile.purpose == "SPEECH_TRANSCRIPTION",
            AIModelProfile.status == AIArtifactStatus.ACTIVE,
        )
    ).all()
    for profile in profiles:
        if profile.approved_at is None or profile.approved_by is None:
            continue
        from app.speech_transcription.execution import snapshot_profile

        languages = (profile.configuration or {}).get("languages", [])
        if not languages or not isinstance(languages, list):
            continue
        try:
            snapshot = snapshot_profile(profile, languages[0], {})
            from app.speech_transcription.profiles import approved_profile_record

            if approved_profile_record(session, profile, snapshot) is None:
                continue
            availability = validate_profile_configuration(snapshot, settings)["availability"]
        except (ValueError, TypeError, KeyError):
            continue
        if availability in {"CONFIGURED", "TEST_ONLY"}:
            return {
                "status": availability,
                "description": (
                    "TEST ONLY technical worker configuration; no speech recognition is verified."
                    if availability == "TEST_ONLY"
                    else "Approved transcription configuration is available to the worker. "
                    "Worker health and real provider acceptance are not asserted."
                ),
                "provider": snapshot["provider"],
                "model_name": snapshot["model_name"],
                "adapter_version": snapshot["adapter_version"],
                "test_only": availability == "TEST_ONLY",
            }
    return {
        "status": "UNCONFIGURED",
        "description": "No approved deployment transcription configuration is available. "
        "Original audio and human-only assessment remain available.",
        "test_only": False,
    }


class ControlCenterService:
    @staticmethod
    def require(context: TenantContext, permission: str = ENTRY_PERMISSION) -> None:
        # Do not use the shared tenant-capability fallback for platform oversight.
        if ENTRY_PERMISSION not in context.platform_permissions:
            raise AuthorizationError("platform Control Centre authority required")
        if permission not in context.platform_permissions:
            raise AuthorizationError(f"platform permission required: {permission}")

    @staticmethod
    def _page(offset: int, limit: int) -> None:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("pagination requires offset >= 0 and limit between 1 and 100")

    @staticmethod
    def overview(session: Session, context: TenantContext, settings: Settings) -> dict[str, Any]:
        ControlCenterService.require(context)
        permissions = context.platform_permissions
        metrics: dict[str, int | None] = {}
        for key, permission in (
            ("organisations", "platform.organisation.read"),
            ("users", "platform.user.read"),
            ("standard_packs", "standard_pack.read"),
        ):
            metrics[key] = (
                ControlCenterRepository.count(session, OVERVIEW_MODELS[key])
                if permission in permissions
                else None
            )
        metrics["recent_audit_events"] = (
            ControlCenterRepository.recent_audit_count(session)
            if "platform.audit.read" in permissions
            else None
        )
        metrics["question_bank_items"] = (
            ControlCenterRepository.shared_question_count(session)
            if "question_bank.read" in permissions
            else None
        )
        metrics["exam_versions"] = (
            ControlCenterRepository.shared_exam_count(session)
            if "exam.read" in permissions
            else None
        )
        metrics["open_governance_cases"] = (
            ControlCenterRepository.open_governance_count(session)
            if "result.read" in permissions
            else None
        )
        return {
            "environment": settings.environment,
            "metrics": metrics,
            "metric_scopes": {
                "question_bank_items": "PLATFORM_SHARED",
                "exam_versions": "PLATFORM_SHARED",
                "recent_audit_events": "LAST_24_HOURS",
            },
            "available_sections": [
                section
                for section, required in SECTION_PERMISSIONS.items()
                if any(permission in permissions for permission in required)
            ],
            "recent_audit": (
                ControlCenterService.audit(session, context, 0, 5, None, None)["items"]
                if "platform.audit.read" in permissions
                else None
            ),
            "service_health": (
                ControlCenterService.system(session, context, settings)
                if "platform.system.read" in permissions
                else None
            ),
        }

    @staticmethod
    def organisations(
        session: Session,
        context: TenantContext,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        ControlCenterService.require(context, "platform.organisation.read")
        ControlCenterService._page(offset, limit)
        return ControlCenterRepository.organisations(
            session, offset, limit, "platform.membership.read" in context.platform_permissions
        )

    @staticmethod
    def standards(
        session: Session,
        context: TenantContext,
        kind: str,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        if kind not in {"versions", "packs", "authorities", "bindings"}:
            raise ValueError("unsupported standards metadata view")
        ControlCenterService.require(
            context, "standards.authority.read" if kind == "authorities" else "standard_pack.read"
        )
        ControlCenterService._page(offset, limit)
        page = ControlCenterRepository.standards(session, kind, offset, limit)
        if kind == "versions":
            for row in page["items"]:
                row["supported_levels"] = (
                    []
                    if row["assessment_family"] == "INTEGRATION_KNOWLEDGE"
                    else [
                        level
                        for level in row["supported_levels"] or []
                        if level in {"A1", "A2", "B1", "B2", "C1", "C2"}
                    ]
                )
        return page

    @staticmethod
    def sources(
        session: Session,
        context: TenantContext,
        kind: str,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        if kind not in {"sources", "documents", "ingestions"}:
            raise ValueError("unsupported source metadata view")
        ControlCenterService.require(
            context, "standards.source.read" if kind == "sources" else "standards.document.read"
        )
        ControlCenterService._page(offset, limit)
        page = ControlCenterRepository.sources(session, kind, offset, limit)
        if kind == "sources":
            for row in page["items"]:
                if row["assessment_family"] == "INTEGRATION_KNOWLEDGE":
                    row["cefr_level"] = None
        return page

    @staticmethod
    def scoped_metadata(
        session: Session,
        context: TenantContext,
        domain: str,
        offset: int,
        limit: int,
        organisation_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        permission = "question_bank.read" if domain == "question-bank" else "exam.read"
        ControlCenterService.require(context, permission)
        ControlCenterService._page(offset, limit)
        if organisation_id is not None:
            ControlCenterService.require(context, "platform.organisation.read")
        query = (
            ControlCenterRepository.question_bank
            if domain == "question-bank"
            else ControlCenterRepository.exams
        )
        return query(session, offset, limit, organisation_id)

    @staticmethod
    def audit(
        session: Session,
        context: TenantContext,
        offset: int,
        limit: int,
        organisation_id: uuid.UUID | None,
        event_type: str | None,
    ) -> dict[str, Any]:
        ControlCenterService.require(context, "platform.audit.read")
        ControlCenterService._page(offset, limit)
        if event_type is not None and not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_.:-]{0,159}", event_type
        ):
            raise ValueError("event_type must be a bounded event key")
        page = ControlCenterRepository.audit(session, offset, limit, organisation_id, event_type)
        for row in page["items"]:
            evidence_references = _audit_references(row)
            source_version_id = safe_reference(row.pop("source_version_id", None))
            if source_version_id is not None:
                row["source_version_id"] = source_version_id
            for key in ("entity_id", "operation_id", "request_id", "workflow_id"):
                row[key] = safe_reference(row[key])
            event = row["event_type"]
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,159}", event) or (
                event.split(".", 1)[0] not in SAFE_EVENT_PREFIXES
            ):
                row["event_type"] = "REDACTED"
            if row["entity_type"] not in SAFE_ENTITY_TYPES:
                row["entity_type"] = "REDACTED"
            if (
                evidence_references
                and row["event_type"] != "REDACTED"
                and row["entity_type"] != "REDACTED"
            ):
                row["evidence_references"] = evidence_references
        return page

    @staticmethod
    def ai_operations(
        session: Session,
        context: TenantContext,
        kind: str,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        ControlCenterService.require(context, "ai.read")
        ControlCenterService._page(offset, limit)
        if kind not in {"models", "workflows", "runs", "reviews", "checkpoints", "transcriptions"}:
            raise ValueError("unsupported AI metadata view")
        page = ControlCenterRepository.ai_operations(session, kind, offset, limit)
        for row in page["items"]:
            if kind == "transcriptions":
                _safe_transcription_operation(row)
            elif kind == "runs":
                row["request_id"] = safe_reference(row["request_id"])
            elif kind == "reviews":
                if row["entity_type"] not in SAFE_ENTITY_TYPES:
                    row["entity_type"] = "REDACTED"
                if row["review_type"] not in {"AI_PROPOSAL", "AI_RUN", "AI_WORKFLOW"}:
                    row["review_type"] = "REDACTED"
        return page

    @staticmethod
    def certification(
        session: Session,
        context: TenantContext,
        kind: str,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        ControlCenterService.require(context, "certification.read")
        ControlCenterService._page(offset, limit)
        if kind == "authorizations":
            page = ControlCenterRepository.authorizations(session, offset, limit)
            for row in page["items"]:
                row["authorization_kind"] = (
                    "SYNTHETIC_TEST" if row.pop("synthetic") else "RECORDED_NONOFFICIAL"
                )
                row["official_entitlement"] = False
                now = datetime.now(timezone.utc)
                start = (
                    row["effective_from"].replace(tzinfo=timezone.utc)
                    if (row["effective_from"].tzinfo is None)
                    else row["effective_from"]
                )
                end = row["effective_until"]
                if end is not None and end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                if row["status"] == "REVOKED":
                    effective = "REVOKED"
                elif row["classification"] == "OFFICIAL_CERTIFICATION":
                    effective = "BLOCKED"
                elif not get_settings().certification_issuance_enabled:
                    effective = "DISABLED"
                elif end is not None and end <= now:
                    effective = "EXPIRED"
                elif start > now:
                    effective = "INACTIVE"
                else:
                    effective = "ACTIVE"
                row["effective_status"] = effective
                row["currently_valid"] = effective == "ACTIVE"
            return page
        if kind != "certificates":
            raise ValueError("unsupported certification metadata view")
        records, total = ControlCenterRepository.certificates(session, offset, limit)
        items = []
        for record, release in records:
            effective = (
                ResultReleaseService.artifact_status(session, release, record)
                if release is not None
                else "BLOCKED"
            )
            items.append(
                {
                    "id": record.id,
                    "organisation_id": record.organisation_id,
                    "release_id": record.release_id,
                    "result_version_id": record.result_version_id,
                    "authorization_id": record.authorization_id,
                    "authorization_version": record.authorization_version,
                    "status": record.status.value,
                    "effective_status": effective,
                    "currently_valid": effective == "ISSUED",
                    "issued_at": record.issued_at,
                    "official_entitlement": False,
                }
            )
        return {"items": items, "total": total, "offset": offset, "limit": limit}

    @staticmethod
    def system(session: Session, context: TenantContext, settings: Settings) -> dict[str, Any]:
        ControlCenterService.require(context, "platform.system.read")
        try:
            reachable = ControlCenterRepository.database_reachable(session)
        except SQLAlchemyError:
            session.rollback()
            reachable = False
        return {
            "environment": settings.environment,
            "api": {"status": "responding"},
            "database": {"status": "reachable" if reachable else "unavailable"},
            "object_storage": {
                "backend": settings.object_storage_backend,
                "health": "NOT_VERIFIED",
            },
            "redis": {"health": "NOT_VERIFIED"},
            "ai_executor": {
                "status": "UNCONFIGURED",
                "description": "Workflow records and checkpoints are implemented. No live executor "
                "is connected to this API.",
            },
            "speech_transcription_executor": _transcription_executor(session, settings),
            "certification": {
                "official_issuance": "DISABLED",
                "synthetic_issuance_enabled": settings.certification_issuance_enabled,
            },
        }
