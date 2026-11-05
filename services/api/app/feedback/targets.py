"""Exact existing artifact versions that can be named in a proposal."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_orchestration.execution_profiles import MODEL_FIELDS, _record
from app.ai_orchestration.service import _checksum as ai_checksum
from app.exams.service import ExamStudioService, exam_checksum
from app.feedback.common import FeedbackConflict, Pin
from app.models import (
    AIModelProfile,
    AIWorkflowVersion,
    ExamVersion,
    PromptVersion,
    QuestionBankItemCefrLevel,
    QuestionBankItemVersion,
    RubricVersion,
    StandardPackVersion,
)
from app.question_bank.service import validate_item_payload
from app.speech_transcription.execution import snapshot_profile
from app.speech_transcription.profiles import approved_profile_record
from app.standard_packs.service import StandardPackService
from app.writing_assessment.service import RubricService, canonical_checksum

TargetType = Literal[
    "STANDARD_PACK_VERSION",
    "RUBRIC_VERSION",
    "PROMPT_VERSION",
    "WORKFLOW_VERSION",
    "QUESTION_BANK_ITEM_VERSION",
    "EXAM_VERSION",
    "MODEL_PROFILE",
    "TRANSCRIPTION_PROFILE",
]
CURRENT = {"APPROVED", "PUBLISHED", "ACTIVE"}


class TargetPin(Pin):
    type: TargetType


def _item_checksums(session: Session, row: QuestionBankItemVersion) -> set[str]:
    levels = list(
        session.scalars(
            select(QuestionBankItemCefrLevel.cefr_level).where(
                QuestionBankItemCefrLevel.item_version_id == row.id
            )
        )
    )
    # Item checksums predate Numeric(12,4) persistence. Reconstruct equivalent scales.
    points = (
        [None]
        if row.max_points is None
        else [
            row.max_points.quantize(Decimal(10) ** -scale)
            for scale in range(5)
            if row.max_points.quantize(Decimal(10) ** -scale) == row.max_points
        ]
    )
    checksums = {
        validate_item_payload(
            assessment_family=row.assessment_family,
            exam_family=row.exam_family,
            language=row.language,
            cefr_levels=levels,
            skill=row.skill,
            task_type=row.task_type,
            response_mode=row.response_mode,
            media_requirement=row.media_requirement,
            content=row.content,
            scoring_policy=row.scoring_policy,
            max_points=value,
            scoring_metadata=row.scoring_metadata,
            rights_status=row.rights_status,
            licence_reference=row.licence_reference,
            rubric_reference=row.rubric_reference,
        ).checksum
        for value in points
    }
    return {checksum for checksum in checksums if checksum is not None}


def _projection(pin: TargetPin, label: str) -> dict[str, Any]:
    return {**pin.model_dump(mode="json"), "version_label": label}


def resolve_target(session: Session, pin: TargetPin) -> dict[str, Any]:
    """Revalidate the current, platform-shared record without changing it."""
    mismatch = FeedbackConflict("FEEDBACK_TARGET_VERSION_MISMATCH")
    if pin.type == "STANDARD_PACK_VERSION":
        pack = session.get(
            StandardPackVersion, pin.id, with_for_update={"read": True}, populate_existing=True
        )
        if (
            pack is None
            or pack.status.value not in {"PUBLISHED", "ACTIVE"}
            or pack.configuration_checksum != pin.checksum
            or StandardPackService.checksum(pack) != pin.checksum
        ):
            raise mismatch
        return _projection(pin, pack.version_label)
    if pin.type == "RUBRIC_VERSION":
        rubric = session.get(
            RubricVersion, pin.id, with_for_update={"read": True}, populate_existing=True
        )
        if (
            rubric is None
            or rubric.rubric.organisation_id is not None
            or rubric.status.value not in CURRENT
            or rubric.checksum != pin.checksum
            or canonical_checksum(RubricService.checksum_payload(rubric)) != pin.checksum
        ):
            raise mismatch
        return _projection(pin, rubric.version_label)
    if pin.type == "PROMPT_VERSION":
        prompt = session.get(
            PromptVersion, pin.id, with_for_update={"read": True}, populate_existing=True
        )
        if (
            prompt is None
            or prompt.status.value not in CURRENT
            or prompt.checksum != pin.checksum
            or ai_checksum(
                {
                    "system_template": prompt.system_template,
                    "instruction_template": prompt.instruction_template,
                    "configuration": prompt.configuration,
                    "input_schema_version": prompt.input_schema_version,
                    "output_schema_version": prompt.output_schema_version,
                }
            )
            != pin.checksum
        ):
            raise mismatch
        return _projection(pin, prompt.version_label)
    if pin.type == "WORKFLOW_VERSION":
        workflow = session.get(
            AIWorkflowVersion, pin.id, with_for_update={"read": True}, populate_existing=True
        )
        if (
            workflow is None
            or workflow.status.value not in CURRENT
            or workflow.checksum != pin.checksum
            or ai_checksum(
                {
                    "version_label": workflow.version_label,
                    "graph_schema_version": workflow.graph_schema_version,
                    "graph_definition": workflow.graph_definition,
                }
            )
            != pin.checksum
        ):
            raise mismatch
        return _projection(pin, workflow.version_label)
    if pin.type == "QUESTION_BANK_ITEM_VERSION":
        item = session.get(
            QuestionBankItemVersion, pin.id, with_for_update={"read": True}, populate_existing=True
        )
        if (
            item is None
            or item.item.organisation_id is not None
            or item.item.scope_type != "PLATFORM_SHARED"
            or item.status.value not in CURRENT
            or item.checksum != pin.checksum
            or pin.checksum not in _item_checksums(session, item)
        ):
            raise mismatch
        return _projection(pin, item.version_label)
    if pin.type == "EXAM_VERSION":
        exam = session.get(
            ExamVersion, pin.id, with_for_update={"read": True}, populate_existing=True
        )
        if (
            exam is None
            or exam.exam.organisation_id is not None
            or exam.exam.scope_type != "PLATFORM_SHARED"
            or exam.status.value not in CURRENT
            or exam.checksum != pin.checksum
            or exam_checksum(ExamStudioService._payload(session, exam)) != pin.checksum
        ):
            raise mismatch
        return _projection(pin, exam.version_label)
    if pin.type in {"MODEL_PROFILE", "TRANSCRIPTION_PROFILE"}:
        profile = session.get(
            AIModelProfile, pin.id, with_for_update={"read": True}, populate_existing=True
        )
        if profile is None or profile.status.value not in {"PUBLISHED", "ACTIVE"}:
            raise mismatch
        if pin.type == "TRANSCRIPTION_PROFILE":
            if profile.purpose != "SPEECH_TRANSCRIPTION":
                raise mismatch
            snapshot = snapshot_profile(profile, "", {})
            if (
                snapshot["profile_version"] != pin.checksum
                or approved_profile_record(session, profile, snapshot) is None
            ):
                raise mismatch
        elif (
            profile.purpose == "SPEECH_TRANSCRIPTION"
            or canonical_checksum(_record(profile, MODEL_FIELDS)) != pin.checksum
        ):
            raise mismatch
        return _projection(pin, pin.checksum[:12])
    raise mismatch
