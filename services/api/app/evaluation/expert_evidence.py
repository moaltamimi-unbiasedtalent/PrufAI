"""Exact reference evidence validation, without provider or assessment writes."""

from __future__ import annotations

import copy
import unicodedata
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.authorization.membership import require_active_tenant_permission
from app.config import get_settings
from app.evaluation.expert_schemas import CriterionJudgmentInput, SourceInput
from app.knowledge.storage import ObjectStorage, build_object_storage
from app.models import (
    CandidateExamAttempt,
    CandidateResponseRevision,
    ExamVersion,
    ExamVersionItemBinding,
    KnowledgeDocumentVersion,
    Organisation,
    QuestionBankItemCefrLevel,
    QuestionBankItemVersion,
    QuestionBankStandardPackBinding,
    RubricVersion,
    StandardPackVersion,
)
from app.models.enums import CandidateAttemptStatus, KnowledgeVersionStatus, LegalReviewStatus
from app.question_bank.service import validate_item_payload
from app.speaking_audio.evidence import resolve_submitted_audio
from app.speaking_audio.service import SpeakingAudioService
from app.standard_packs.service import StandardPackService
from app.writing_assessment.service import RubricService, canonical_checksum


def _pins(session: Session, payload: SourceInput) -> RubricVersion | None:
    pack = session.get(StandardPackVersion, payload.standard_pack_version_id)
    if (
        pack is None
        or pack.configuration_checksum != payload.standard_pack_checksum
        or StandardPackService.checksum(pack) != payload.standard_pack_checksum
        or pack.status.value not in {"PUBLISHED", "ACTIVE", "RETIRED", "SUPERSEDED"}
        or pack.standard_pack.assessment_family.value != payload.assessment_family
        or pack.standard_pack.language != payload.language
        or payload.cefr_level is not None
        and payload.cefr_level not in pack.supported_levels
    ):
        raise ValueError("REFERENCE_STANDARD_PACK_PIN_MISMATCH")
    rubric = (
        session.get(RubricVersion, payload.rubric_version_id) if payload.rubric_version_id else None
    )
    if payload.rubric_version_id and (
        rubric is None
        or rubric.status.value not in {"PUBLISHED", "ACTIVE"}
        or rubric.checksum != payload.rubric_checksum
        or canonical_checksum(RubricService.checksum_payload(rubric)) != rubric.checksum
        or rubric.standard_pack_version_id != pack.id
        or rubric.rubric.organisation_id not in {None, payload.organisation_id}
        or rubric.assessment_family.value != payload.assessment_family
        or rubric.skill.value != payload.kind
        or rubric.language != payload.language
        or payload.cefr_level is not None
        and payload.cefr_level not in rubric.cefr_levels
    ):
        raise ValueError("REFERENCE_RUBRIC_PIN_MISMATCH")
    if payload.question_bank_item_version_id:
        item = session.get(QuestionBankItemVersion, payload.question_bank_item_version_id)
        if (
            item is None
            or item.checksum != payload.item_checksum
            or item.status.value not in {"APPROVED", "PUBLISHED", "ACTIVE", "RETIRED", "SUPERSEDED"}
            or item.assessment_family.value != payload.assessment_family
            or item.language != payload.language
            or payload.kind in {"WRITING", "SPEAKING"}
            and (item.skill is None or item.skill.value != payload.kind)
            or item.item.scope_type == "ORGANISATION_PRIVATE"
            and item.item.organisation_id != payload.organisation_id
        ):
            raise ValueError("REFERENCE_ITEM_PIN_MISMATCH")
        levels = list(
            session.scalars(
                select(QuestionBankItemCefrLevel.cefr_level).where(
                    QuestionBankItemCefrLevel.item_version_id == item.id
                )
            )
        )
        if payload.cefr_level is not None and payload.cefr_level not in levels:
            raise ValueError("REFERENCE_ITEM_LEVEL_MISMATCH")
        points = (
            [None]
            if item.max_points is None
            else [
                item.max_points.quantize(Decimal(10) ** -scale)
                for scale in range(5)
                if item.max_points.quantize(Decimal(10) ** -scale) == item.max_points
            ]
        )
        checksums = {
            validate_item_payload(
                assessment_family=item.assessment_family,
                exam_family=item.exam_family,
                language=item.language,
                cefr_levels=levels,
                skill=item.skill,
                task_type=item.task_type,
                response_mode=item.response_mode,
                media_requirement=item.media_requirement,
                content=item.content,
                scoring_policy=item.scoring_policy,
                max_points=value,
                scoring_metadata=item.scoring_metadata,
                rights_status=item.rights_status,
                licence_reference=item.licence_reference,
                rubric_reference=item.rubric_reference,
            ).checksum
            for value in points
        }
        if item.checksum not in checksums or not session.scalar(
            select(QuestionBankStandardPackBinding.id).where(
                QuestionBankStandardPackBinding.item_version_id == item.id,
                QuestionBankStandardPackBinding.standard_pack_version_id == pack.id,
            )
        ):
            raise ValueError("REFERENCE_ITEM_CONTENT_OR_PACK_MISMATCH")
        if rubric is not None and item.rubric_reference != str(rubric.id):
            raise ValueError("REFERENCE_ITEM_RUBRIC_MISMATCH")
    return rubric


def _document(session: Session, payload: SourceInput) -> None:
    if payload.knowledge_document_version_id is None:
        return
    document = session.get(KnowledgeDocumentVersion, payload.knowledge_document_version_id)
    legal = document.legal_metadata if document is not None else None
    if (
        document is None
        or document.sha256 != payload.knowledge_document_checksum
        or document.status is not KnowledgeVersionStatus.APPROVED
        or document.source.scope.value != "PLATFORM_SHARED"
        or document.source.language not in {None, payload.language}
        or document.source.assessment_family.value != payload.assessment_family
        or legal is None
        or legal.legal_review_status is not LegalReviewStatus.APPROVED
        or legal.legal_reviewed_by_user_id is None
        or legal.legal_reviewed_at is None
        or not legal.usage_rights
        or legal.derivative_use_allowed is not True
    ):
        raise ValueError("REFERENCE_APPROVED_DOCUMENT_RIGHTS_REQUIRED")
    if payload.kind == "WRITING" and (
        payload.input.text is None
        or payload.input.text
        not in "\n".join(
            page.text_content for page in sorted(document.pages, key=lambda page: page.page_number)
        )
    ):
        raise ValueError("REFERENCE_DOCUMENT_TEXT_NOT_FOUND")


def _candidate(
    session: Session, context: TenantContext, payload: SourceInput, storage: ObjectStorage | None
) -> dict[str, str]:
    identity = payload.input.evidence_identity
    raw_revision = identity.get("candidate_response_revision_id") or identity.get(
        "response_revision_id"
    )
    if raw_revision is None:
        raise ValueError("REFERENCE_EXACT_RESPONSE_REQUIRED")
    revision = session.get(CandidateResponseRevision, uuid.UUID(raw_revision))
    attempt = session.get(CandidateExamAttempt, revision.attempt_id) if revision else None
    if attempt is None or attempt.organisation_id != payload.organisation_id:
        raise ValueError("REFERENCE_RESPONSE_UNAVAILABLE")
    trusted = build_tenant_context(session, context.user_id, attempt.organisation_id)
    permission = (
        "speech_transcription.read" if payload.kind == "TRANSCRIPTION" else "subjective.review"
    )
    require_active_tenant_permission(session, trusted, permission, attempt.organisation_id)
    if attempt.candidate_user_id == context.user_id:
        raise AuthorizationError("REFERENCE_CANDIDATE_CANNOT_REVIEW_OWN_EVIDENCE")
    assert revision is not None
    response = revision.response
    binding = session.get(ExamVersionItemBinding, response.exam_version_item_binding_id)
    exam = session.get(ExamVersion, attempt.exam_version_id)
    if (
        attempt.status is not CandidateAttemptStatus.SUBMITTED
        or attempt.submitted_at is None
        or response.current_revision != revision.revision_number
        or response.current_payload != revision.payload
        or response.attempt_id != attempt.id
        or revision.payload_checksum != canonical_checksum(revision.payload)
        or identity.get("response_checksum") != revision.payload_checksum
        or binding is None
        or exam is None
        or binding.task.section.module.exam_version_id != attempt.exam_version_id
        or str(binding.question_bank_item_version_id) != str(payload.question_bank_item_version_id)
        or exam.standard_pack_version_id != payload.standard_pack_version_id
        or attempt.runtime_plan.get("standard_pack_version_id")
        != str(payload.standard_pack_version_id)
        or canonical_checksum(attempt.runtime_plan) != attempt.runtime_plan_checksum
    ):
        raise ValueError("REFERENCE_SUBMITTED_RESPONSE_PIN_MISMATCH")
    rubric = (
        session.get(RubricVersion, payload.rubric_version_id) if payload.rubric_version_id else None
    )
    plan_item = next(
        (
            item
            for module in attempt.runtime_plan.get("modules", [])
            for section in module.get("sections", [])
            for task in section.get("tasks", [])
            for item in task.get("items", [])
            if item.get("binding_id") == str(binding.id)
        ),
        None,
    )
    if (
        plan_item is None
        or plan_item.get("item_version_id") != str(payload.question_bank_item_version_id)
        or plan_item.get("item_version_checksum") != payload.item_checksum
        or rubric is not None
        and rubric.task_spec_id != binding.task.standard_pack_task_spec_id
    ):
        raise ValueError("REFERENCE_EXACT_RUNTIME_ITEM_MISMATCH")
    actual = {
        "organisation_id": str(attempt.organisation_id),
        "candidate_user_id": str(attempt.candidate_user_id),
        "attempt_id": str(attempt.id),
        "exam_version_id": str(attempt.exam_version_id),
        "exam_version_item_binding_id": str(binding.id),
        "candidate_response_revision_id": str(revision.id),
        "response_revision_id": str(revision.id),
        "candidate_response_id": str(response.id),
        "response_checksum": revision.payload_checksum,
    }
    if payload.kind == "WRITING":
        if revision.payload != {"text": payload.input.text}:
            raise ValueError("REFERENCE_WRITING_TEXT_MISMATCH")
    else:
        raw_asset = identity.get("candidate_media_asset_id")
        if raw_asset is None:
            raise ValueError("REFERENCE_ORIGINAL_AUDIO_REQUIRED")
        asset, _, _, audio = resolve_submitted_audio(
            session,
            uuid.UUID(raw_asset),
            response_revision_id=revision.id,
            audio_sha256=payload.input.audio_sha256,
        )
        if asset.duration_ms != payload.input.duration_ms:
            raise ValueError("REFERENCE_AUDIO_DURATION_MISMATCH")
        backend = SpeakingAudioService.durable_storage(
            storage or build_object_storage(get_settings())
        )
        content = backend.get_bounded(
            asset.storage_key, max_bytes=25_000_000, expected_size=asset.byte_size
        )
        if SpeakingAudioService.checksum(content) != asset.sha256:
            raise ValueError("REFERENCE_ORIGINAL_AUDIO_CHECKSUM_MISMATCH")
        actual.update(
            {key: str(value) for key, value in audio.items() if not isinstance(value, dict)}
        )
    return actual


def validate_reference_source(
    session: Session,
    context: TenantContext,
    payload: SourceInput,
    *,
    storage: ObjectStorage | None = None,
) -> dict[str, Any]:
    """Revalidate exact private evidence before immutable source creation/approval."""
    if (
        payload.organisation_id is not None
        and session.get(Organisation, payload.organisation_id) is None
    ):
        raise ValueError("REFERENCE_ORGANISATION_UNAVAILABLE")
    if payload.organisation_id is not None:
        trusted = build_tenant_context(session, context.user_id, payload.organisation_id)
        permission = (
            "speech_transcription.read" if payload.kind == "TRANSCRIPTION" else "subjective.review"
        )
        require_active_tenant_permission(session, trusted, permission, payload.organisation_id)
    _pins(session, payload)
    _document(session, payload)
    actual = {
        key: str(value)
        for key, value in payload.model_dump(mode="json").items()
        if key
        in {
            "standard_pack_version_id",
            "standard_pack_checksum",
            "rubric_version_id",
            "rubric_checksum",
            "question_bank_item_version_id",
            "item_checksum",
        }
        and value is not None
    }
    if payload.input.audio_sha256 is not None:
        actual["audio_sha256"] = payload.input.audio_sha256
    identity = payload.input.evidence_identity
    if (
        payload.origin == "APPROVED_CANDIDATE"
        or identity.get("candidate_response_revision_id")
        or identity.get("response_revision_id")
    ):
        actual.update(_candidate(session, context, payload, storage))
    aliases = {
        "item_version_id": "question_bank_item_version_id",
        "item_version_checksum": "item_checksum",
    }
    if any(actual.get(aliases.get(key, key)) != value for key, value in identity.items()):
        raise ValueError("REFERENCE_EXACT_EVIDENCE_IDENTITY_MISMATCH")
    body = payload.model_dump(mode="json", exclude={"operation_id", "code"})
    if payload.kind in {"SPEAKING", "TRANSCRIPTION"}:
        content = payload.input.audio_sha256
    elif payload.input.text is not None:
        content = " ".join(unicodedata.normalize("NFC", payload.input.text).split())
    else:
        content = payload.knowledge_document_checksum
    if not content:
        raise ValueError("REFERENCE_CONTENT_REQUIRED")
    return {
        "body": body,
        "content_checksum": canonical_checksum(content),
        "lineage_checksum": canonical_checksum(payload.known_lineage_reference),
        "evidence_class": "SYNTHETIC_TEST"
        if payload.origin == "ORIGINAL_SYNTHETIC"
        else "APPROVED_REFERENCE",
    }


def validate_judgment_evidence(
    source_body: dict[str, Any],
    rubric: RubricVersion | None,
    criteria: list[dict[str, Any]],
    transcript: str | None,
    relevance: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate only the declared reference task, preserving exact source evidence."""
    kind = source_body["kind"]
    if kind == "TRANSCRIPTION":
        if (
            criteria
            or relevance
            or not isinstance(transcript, str)
            or not transcript.strip()
            or len(transcript) > 4000
        ):
            raise ValueError("REFERENCE_TRANSCRIPT_REQUIRED")
        if (
            source_body["origin"] != "ORIGINAL_SYNTHETIC"
            and source_body["input"]["audio_kind"] != "SPEECH"
        ):
            raise ValueError("TECHNICAL_TONE_IS_NOT_LINGUISTIC_REFERENCE")
        return []
    if kind == "RETRIEVAL":
        if criteria or transcript is not None or not relevance:
            raise ValueError("REFERENCE_RELEVANCE_JUDGMENT_REQUIRED")
        expected = str(source_body.get("knowledge_document_version_id"))
        seen: set[str] = set()
        for row in relevance:
            if (
                set(row) != {"document_version_id", "checksum", "relevant"}
                or row["document_version_id"] != expected
                or row["checksum"] != source_body.get("knowledge_document_checksum")
                or type(row["relevant"]) is not bool
                or expected in seen
            ):
                raise ValueError("REFERENCE_RELEVANCE_IDENTITY_MISMATCH")
            seen.add(expected)
        return []
    if rubric is None or transcript is not None or relevance:
        raise ValueError("REFERENCE_RUBRIC_JUDGMENT_REQUIRED")
    entries = [
        CriterionJudgmentInput.model_validate(row).model_dump(mode="json") for row in criteria
    ]
    expected_criteria = {str(row["code"]): row for row in rubric.criteria}
    if len(entries) != len(expected_criteria) or {row["criterion_code"] for row in entries} != set(
        expected_criteria
    ):
        raise ValueError("REFERENCE_COMPLETE_CRITERION_COVERAGE_REQUIRED")
    levels = {str(row["code"]) for row in rubric.performance_levels}
    normalized: list[dict[str, Any]] = []
    for row in entries:
        points = Decimal(str(row["points"]))
        if (
            not 0 <= points <= Decimal(str(expected_criteria[row["criterion_code"]]["max_points"]))
            or points != points.quantize(Decimal("0.0001"))
            or row["level"] not in levels
            or not row["rationale"].strip()
        ):
            raise ValueError("REFERENCE_CRITERION_OUT_OF_BOUNDS")
        minimum = max(1, int(rubric.evidence_policy.get("minimum_references", 1)))
        if len(row["evidence"]) < minimum:
            raise ValueError("REFERENCE_CRITERION_EVIDENCE_REQUIRED")
        for evidence in row["evidence"]:
            if kind == "WRITING":
                text = source_body["input"].get("text")
                if set(evidence) != {"quote", "start", "end"} or not isinstance(text, str):
                    raise ValueError("REFERENCE_EXACT_TEXT_EVIDENCE_REQUIRED")
                start, end, quote = evidence["start"], evidence["end"], evidence["quote"]
                if (
                    type(start) is not int
                    or type(end) is not int
                    or not isinstance(quote, str)
                    or not 0 <= start < end <= len(text)
                    or len(quote) > 2000
                    or text[start:end] != quote
                ):
                    raise ValueError("REFERENCE_TEXT_SPAN_MISMATCH")
            else:
                if set(evidence) != {
                    "asset_id",
                    "audio_sha256",
                    "candidate_response_revision_id",
                    "start_ms",
                    "end_ms",
                    "time_unit",
                }:
                    raise ValueError("REFERENCE_EXACT_AUDIO_EVIDENCE_REQUIRED")
                identity = source_body["input"]["evidence_identity"]
                revision = identity.get("candidate_response_revision_id") or identity.get(
                    "response_revision_id"
                )
                start, end = evidence["start_ms"], evidence["end_ms"]
                if (
                    evidence["asset_id"] != identity.get("candidate_media_asset_id")
                    or evidence["audio_sha256"] != source_body["input"]["audio_sha256"]
                    or evidence["candidate_response_revision_id"] != revision
                    or evidence["time_unit"] != "milliseconds"
                    or type(start) is not int
                    or type(end) is not int
                    or not 0 <= start < end <= source_body["input"]["duration_ms"]
                ):
                    raise ValueError("REFERENCE_AUDIO_SPAN_OR_IDENTITY_MISMATCH")
        normalized.append(
            {
                **copy.deepcopy(row),
                "points": format(points, ".4f"),
                "rationale": row["rationale"].strip(),
            }
        )
    return sorted(normalized, key=lambda row: row["criterion_code"])
