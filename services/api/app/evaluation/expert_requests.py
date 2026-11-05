"""Human reference decisions; authentication supplies every acting identity."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.evaluation.contracts import Contract
from app.evaluation.expert_schemas import CriterionJudgmentInput


class ApprovalInput(Contract):
    expected_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")


class QualificationInput(Contract):
    operation_id: uuid.UUID
    examiner_id: uuid.UUID
    organisation_id: uuid.UUID | None = None
    kind: Literal["WRITING", "SPEAKING", "TRANSCRIPTION", "RETRIEVAL"]
    language: str = Field(pattern=r"^[a-z]{2}$")
    rubric_version_id: uuid.UUID | None = None
    rubric_checksum: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    standard_pack_version_id: uuid.UUID
    standard_pack_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    origin: Literal["SYNTHETIC_TEST", "VERIFIED_CREDENTIAL"]
    credential_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")
    expires_at: datetime

    @model_validator(mode="after")
    def scope(self) -> QualificationInput:
        if bool(self.rubric_version_id) != bool(self.rubric_checksum):
            raise ValueError("EXAMINER_EXACT_RUBRIC_REQUIRED")
        if self.expires_at.tzinfo is None:
            raise ValueError("EXAMINER_EXPIRY_TIMEZONE_REQUIRED")
        return self


class JudgmentInput(Contract):
    operation_id: uuid.UUID
    expected_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    qualification_id: uuid.UUID
    qualification_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    criteria: list[CriterionJudgmentInput] = Field(default_factory=list, max_length=50)
    transcript: str | None = Field(default=None, min_length=1, max_length=4000)
    relevance: list[dict[str, str | bool]] = Field(default_factory=list, max_length=100)
    review_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")


class JudgmentPin(Contract):
    id: uuid.UUID
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class AdjudicationInput(JudgmentInput):
    judgment_pins: list[JudgmentPin] = Field(min_length=2, max_length=2)
    rationale: str = Field(min_length=1, max_length=4000)
    supersedes_id: uuid.UUID | None = None

    @field_validator("rationale")
    @classmethod
    def documented_rationale(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("EVALUATION_ADJUDICATION_RATIONALE_REQUIRED")
        return normalized


class ReferenceDatasetInput(Contract):
    operation_id: uuid.UUID
    expected_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    adjudication_id: uuid.UUID
    adjudication_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    execution_job_id: uuid.UUID | None = None


class CalibrationInput(Contract):
    operation_id: uuid.UUID
    dataset_version_id: uuid.UUID
    dataset_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    comparison_kind: Literal[
        "HUMAN_A_VS_HUMAN_B", "ADJUDICATED_VS_HUMAN", "AI_PROPOSAL_VS_REFERENCE", "VERSION_A_VS_B"
    ]
    left_id: uuid.UUID
    left_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    right_id: uuid.UUID
    right_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
