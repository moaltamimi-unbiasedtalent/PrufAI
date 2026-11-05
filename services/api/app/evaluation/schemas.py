"""Bounded, explicit evaluation requests. No caller can declare a genuine model run."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field, model_validator

from app.evaluation.contracts import Contract, EvaluationCase, MetricConfiguration

Checksum = str


class DatasetVersionInput(Contract):
    code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
    organisation_id: uuid.UUID | None = None
    assessment_family: Literal["LANGUAGE", "INTEGRATION_KNOWLEDGE"]
    language: str = Field(pattern=r"^[a-z]{2}$")
    cefr_level: Literal["A1", "A2", "B1", "B2", "C1", "C2"] | None = None
    exam_scope: str = Field(min_length=1, max_length=120)
    skill: Literal["TRANSCRIPTION", "WRITING", "SPEAKING", "RETRIEVAL"]
    purpose: str = Field(min_length=1, max_length=120)
    origin: Literal["ORIGINAL_SYNTHETIC"]
    source_legal_status: Literal["OWNED_ORIGINAL"]
    source_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")
    label_provenance: Literal["SYNTHETIC_EXPECTATION"]
    expert_review_status: Literal["NOT_EXPERT_REVIEWED"]
    retention_classification: Literal["SYNTHETIC_TEST"]
    cases: list[EvaluationCase] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def scope_and_labels(self) -> DatasetVersionInput:
        if self.assessment_family == "INTEGRATION_KNOWLEDGE" and self.cefr_level is not None:
            raise ValueError("INTEGRATION_KNOWLEDGE has no CEFR classification")
        if any(case.kind != self.skill for case in self.cases):
            raise ValueError("all cases must match the declared skill")
        if any(
            case.input.language != self.language
            or case.reference.language is not None
            and case.reference.language != self.language
            for case in self.cases
        ):
            raise ValueError("case and reference languages must match dataset language")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate evaluation case identity")
        if any(
            case.reference.label_kind != "SYNTHETIC_EXPECTATION"
            or any(criterion.judgments for criterion in case.reference.criteria)
            for case in self.cases
        ):
            raise ValueError("expert reference import requires separate provenance governance")
        return self


class ChecksumRequest(Contract):
    expected_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class DatasetApprovalRequest(ChecksumRequest):
    provenance_review_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")


class RunConfiguration(Contract):
    dataset_split: Literal["TRAIN", "CALIBRATION", "TEST", "VALIDATION", "HELD_OUT"] = "TEST"
    standard_pack_version_id: uuid.UUID
    standard_pack_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    rubric_version_id: uuid.UUID | None = None
    rubric_checksum: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    question_bank_item_version_id: uuid.UUID | None = None
    item_checksum: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    model_profile_id: uuid.UUID | None = None
    model_version: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    prompt_version_id: uuid.UUID | None = None
    agent_version_id: uuid.UUID | None = None
    workflow_version_id: uuid.UUID | None = None
    profile_version: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    execution_adapter: Literal["SYNTHETIC_OBSERVATION_REPLAY_V1", "PINNED_EXECUTION_REPLAY_V1"]
    evaluation_method: Literal["DETERMINISTIC_REFERENCE_COMPARISON"]
    evaluation_method_version: Literal["1"]
    metric_configuration: MetricConfiguration = Field(default_factory=MetricConfiguration)

    @model_validator(mode="after")
    def paired_pins(self) -> RunConfiguration:
        if bool(self.rubric_version_id) != bool(self.rubric_checksum) or bool(
            self.question_bank_item_version_id
        ) != bool(self.item_checksum):
            raise ValueError("exact version and checksum must be supplied together")
        if self.execution_adapter == "SYNTHETIC_OBSERVATION_REPLAY_V1" and any(
            (
                self.model_profile_id,
                self.model_version,
                self.prompt_version_id,
                self.agent_version_id,
                self.workflow_version_id,
                self.profile_version,
            )
        ):
            raise ValueError("synthetic observation replay executes no model or AI registry bundle")
        return self


class RunRequest(Contract):
    operation_id: uuid.UUID
    dataset_version_id: uuid.UUID
    dataset_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    configuration: RunConfiguration


class QualityReviewRequest(Contract):
    expected_run_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["ACCEPTED", "REJECTED", "CHANGES_REQUIRED"]
    review_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")
