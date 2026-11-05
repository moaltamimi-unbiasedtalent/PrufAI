"""Controlled expert-reference inputs; synthetic fixtures remain test evidence."""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.evaluation.contracts import CaseInput, Contract

OpaqueReference = str
REFERENCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$"
CHECKSUM_PATTERN = r"^[a-f0-9]{64}$"


class SourceInput(Contract):
    operation_id: uuid.UUID
    code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
    organisation_id: uuid.UUID | None = None
    kind: Literal["WRITING", "SPEAKING", "TRANSCRIPTION", "RETRIEVAL"]
    split: Literal["TRAIN", "CALIBRATION", "VALIDATION", "HELD_OUT"]
    origin: Literal["ORIGINAL_SYNTHETIC", "APPROVED_CANDIDATE", "APPROVED_DOCUMENT"]
    language: str = Field(pattern=r"^[a-z]{2}$")
    assessment_family: Literal["LANGUAGE", "INTEGRATION_KNOWLEDGE"] = "LANGUAGE"
    cefr_level: Literal["A1", "A2", "B1", "B2", "C1", "C2"] | None = None
    purpose: Literal["EVALUATION_ONLY"] = "EVALUATION_ONLY"
    retention_classification: Literal["SYNTHETIC_TEST", "APPROVED_RESTRICTED"]
    source_reference: OpaqueReference = Field(pattern=REFERENCE_PATTERN)
    rights_approval_reference: OpaqueReference = Field(pattern=REFERENCE_PATTERN)
    processing_approval_reference: OpaqueReference | None = Field(
        default=None, pattern=REFERENCE_PATTERN
    )
    language_content_review_reference: OpaqueReference | None = Field(
        default=None, pattern=REFERENCE_PATTERN
    )
    known_lineage_reference: OpaqueReference = Field(pattern=REFERENCE_PATTERN)
    standard_pack_version_id: uuid.UUID
    standard_pack_checksum: str = Field(pattern=CHECKSUM_PATTERN)
    rubric_version_id: uuid.UUID | None = None
    rubric_checksum: str | None = Field(default=None, pattern=CHECKSUM_PATTERN)
    question_bank_item_version_id: uuid.UUID | None = None
    item_checksum: str | None = Field(default=None, pattern=CHECKSUM_PATTERN)
    input: CaseInput
    knowledge_document_version_id: uuid.UUID | None = None
    knowledge_document_checksum: str | None = Field(default=None, pattern=CHECKSUM_PATTERN)
    normalization: Literal["NFC_WHITESPACE", "NFC_WHITESPACE_CASEFOLD"] = "NFC_WHITESPACE"

    @model_validator(mode="after")
    def controlled_provenance(self) -> SourceInput:
        if self.origin != "ORIGINAL_SYNTHETIC" and any(
            reference is not None
            and reference.casefold().startswith(("synthetic:", "demo:", "test:"))
            for reference in (
                self.source_reference,
                self.rights_approval_reference,
                self.processing_approval_reference,
                self.language_content_review_reference,
            )
        ):
            raise ValueError("genuine references cannot claim synthetic provenance approval")
        for identifier, checksum in (
            (self.rubric_version_id, self.rubric_checksum),
            (self.question_bank_item_version_id, self.item_checksum),
            (self.knowledge_document_version_id, self.knowledge_document_checksum),
        ):
            if bool(identifier) != bool(checksum):
                raise ValueError("reference identities require an exact version and checksum")
        if self.input.language != self.language:
            raise ValueError("reference language must match the exact input language")
        if self.assessment_family == "INTEGRATION_KNOWLEDGE" and self.cefr_level is not None:
            raise ValueError("integration knowledge references have no CEFR classification")
        if self.kind in {"WRITING", "SPEAKING"} and self.rubric_version_id is None:
            raise ValueError("subjective references require an exact published rubric")
        if self.origin == "ORIGINAL_SYNTHETIC":
            if self.retention_classification != "SYNTHETIC_TEST":
                raise ValueError("synthetic evidence must retain its SYNTHETIC_TEST classification")
        elif self.retention_classification != "APPROVED_RESTRICTED":
            raise ValueError("genuine evidence requires approved restricted retention")
        if self.origin == "APPROVED_CANDIDATE" and (
            self.organisation_id is None or self.processing_approval_reference is None
        ):
            raise ValueError("candidate evidence requires organisation and processing approval")
        identity = self.input.evidence_identity
        if (
            identity.get("candidate_response_revision_id") or identity.get("response_revision_id")
        ) and (self.organisation_id is None or self.processing_approval_reference is None):
            raise ValueError("bound candidate evidence requires processing approval in every class")
        if self.origin == "APPROVED_DOCUMENT" and self.knowledge_document_version_id is None:
            raise ValueError("approved document evidence requires the governed document version")
        if self.kind in {"SPEAKING", "TRANSCRIPTION"}:
            if (
                self.input.modality != "AUDIO"
                or self.input.audio_sha256 is None
                or self.input.duration_ms is None
            ):
                raise ValueError("audio references require original audio checksum and duration")
            if self.origin != "ORIGINAL_SYNTHETIC" and (
                self.input.audio_kind != "SPEECH"
                or self.language_content_review_reference is None
                or self.origin != "APPROVED_CANDIDATE"
            ):
                raise ValueError("genuine audio requires approved original language speech")
        elif self.kind == "WRITING" and (
            self.input.modality != "TEXT" or not self.input.text or not self.input.text.strip()
        ):
            raise ValueError("Writing references require exact nonempty submitted text")
        return self


class CriterionJudgmentInput(Contract):
    criterion_code: str = Field(min_length=1, max_length=120)
    points: Decimal
    level: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=2000)
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=20)

    @field_validator("points", mode="before")
    @classmethod
    def finite_points(cls, value: Any) -> Any:
        try:
            valid = not isinstance(value, bool) and Decimal(str(value)).is_finite()
        except (InvalidOperation, ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("examiner points must be finite numeric values")
        return value
