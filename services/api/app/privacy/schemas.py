from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class Category(StrEnum):
    ASSIGNMENTS = "ASSIGNMENTS"
    ATTEMPTS = "ATTEMPTS"
    OBJECTIVE_ANSWERS = "OBJECTIVE_ANSWERS"
    WRITING_REVISIONS = "WRITING_REVISIONS"
    ORIGINAL_AUDIO = "ORIGINAL_AUDIO"
    TRANSCRIPTIONS = "TRANSCRIPTIONS"
    AI_EXECUTION = "AI_EXECUTION"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    SCORING_RESULTS = "SCORING_RESULTS"
    RELEASE_STATEMENTS = "RELEASE_STATEMENTS"
    APPEALS_CORRECTIONS = "APPEALS_CORRECTIONS"
    IDENTITY = "IDENTITY"
    EVALUATION = "EVALUATION"
    FEEDBACK = "FEEDBACK"
    CERTIFICATES = "CERTIFICATES"
    AUDIT = "AUDIT"
    REDIS = "REDIS"
    BROWSER = "BROWSER"
    TEMPORARY_FILES = "TEMPORARY_FILES"
    LOGS_TRACES = "LOGS_TRACES"
    DATABASE_BACKUPS = "DATABASE_BACKUPS"
    OBJECT_BACKUPS = "OBJECT_BACKUPS"
    EXTERNAL_PROCESSORS = "EXTERNAL_PROCESSORS"


Purpose = Literal["ASSESSMENT_DELIVERY", "ASSESSMENT_DISPUTE"]
Checksum = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID


class CaseIntake(Operation):
    subject_id: UUID
    request_type: Literal["ERASURE_REQUEST", "RESTRICTION_REQUEST"]
    purpose: Purpose
    request_receipt: UUID
    categories: list[Category] = Field(min_length=1, max_length=len(Category))

    @model_validator(mode="after")
    def unique_categories(self) -> "CaseIntake":
        if len(self.categories) != len(set(self.categories)):
            raise ValueError("duplicate categories")
        return self


class CaseOperation(Operation):
    case_checksum: Checksum


class Verification(CaseOperation):
    verification_receipt: UUID
    controller_context: Literal["UNASSESSED", "CONTROLLER", "PROCESSOR", "JOINT_CONTROLLER"]
    context_receipt: UUID | None = None

    @model_validator(mode="after")
    def require_context_receipt(self) -> "Verification":
        if (self.controller_context == "UNASSESSED") != (self.context_receipt is None):
            raise ValueError("controller context requires exact assessment evidence")
        return self


class PolicyDraft(CaseOperation):
    category: Category
    previous_checksum: Checksum | None = None
    jurisdiction: Annotated[str, Field(pattern=r"^[A-Z]{2}(?:-[A-Z0-9]{1,3})?$")]
    controller_context: Literal["UNASSESSED", "CONTROLLER", "PROCESSOR", "JOINT_CONTROLLER"]
    legal_basis_reference: UUID | None = None
    start_event: Literal["UNASSESSED", "SUBMISSION", "RELEASE", "CASE_CLOSURE"]
    duration_days: Annotated[int, Field(strict=True, gt=0)] | None = None
    end_event: Literal["APPEAL_CLOSED", "APPROVED_REVIEW_COMPLETE"] | None = None
    hold_applicability: Literal["UNASSESSED", "REQUIRES_REVIEW"]
    backup_treatment: Literal["UNASSESSED", "RECONCILE_ON_RESTORE"]
    processor_treatment: Literal["UNASSESSED", "REQUIRES_PROCESSOR_REVIEW"]

    @model_validator(mode="after")
    def one_end_condition(self) -> "PolicyDraft":
        if self.duration_days is not None and self.end_event is not None:
            raise ValueError("choose a single end condition")
        return self


class HoldReview(CaseOperation):
    previous_checksum: Checksum | None = None
    status: Literal["REVIEW_REQUIRED", "REPORTED_HOLD", "NO_HOLD_REPORTED"]
    basis: Literal["UNASSESSED", "LEGAL_CLAIM", "LEGAL_OBLIGATION", "REGULATORY_REVIEW"]
    basis_reference: UUID | None = None
    decision_receipt: UUID
    starts_at: AwareDatetime
    review_due_at: AwareDatetime

    @model_validator(mode="after")
    def review_window(self) -> "HoldReview":
        if self.review_due_at <= self.starts_at:
            raise ValueError("hold review requires a positive window")
        if self.status == "REPORTED_HOLD" and (
            self.basis == "UNASSESSED" or self.basis_reference is None
        ):
            raise ValueError("reported hold requires a basis reference")
        return self


class PreviewRequest(CaseOperation):
    scope_checksum: Checksum
    policy_pins: dict[Category, Checksum] = Field(max_length=len(Category))
    hold_checksum: Checksum | None = None
