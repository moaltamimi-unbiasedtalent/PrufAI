"""Input contracts for exact LiD editorial decisions."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Stage = Literal["FACTUAL", "ASSESSMENT", "RIGHTS"]
STAGES: tuple[Stage, ...] = ("FACTUAL", "ASSESSMENT", "RIGHTS")
REVIEW_TYPE = "LID_EDITORIAL_CONTENT"
SUBMIT_PERMISSION = "question_bank.manage"
REVIEW_PERMISSIONS = {
    "FACTUAL": "question_bank.review",
    "ASSESSMENT": "question_bank.review",
    "RIGHTS": "standards.document.review",
}
REVIEW_PROTOCOL = {
    "FACTUAL": (
        "Check the precise source and its date, factual accuracy, Berlin scope, and the key.",
        "Check whether each distractor is substantively incorrect.",
    ),
    "ASSESSMENT": (
        "Check clarity, ambiguity, accessibility, and suitability for the practice blueprint.",
        "Check all alternatives for hidden context, bias, and a second defensible answer.",
    ),
    "RIGHTS": (
        "Check originality, factual dependencies, and the evidence for permitted practice use.",
        "Record display, redistribution, and derivative permissions separately.",
    ),
}


class ReviewSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: uuid.UUID
    expected_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReviewRights(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    basis_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")
    rights_holder_status: Literal["CONFIRMED_RIGHTSHOLDER", "NO_EXCLUSIVE_RIGHTS_ASSERTED"]
    rights_holder: str | None = Field(default=None, min_length=1, max_length=240)
    practice_use_allowed: bool
    display_allowed: bool
    redistribution_allowed: bool
    derivative_use_allowed: bool

    @model_validator(mode="after")
    def check_permissions(self) -> ReviewRights:
        if not self.practice_use_allowed or not self.display_allowed:
            raise ValueError("practice use and display require permission")
        if self.rights_holder_status == "CONFIRMED_RIGHTSHOLDER":
            if not self.rights_holder or not self.rights_holder.strip():
                raise ValueError("an identified rights holder is required")
        elif self.rights_holder is not None:
            raise ValueError("an exclusive rights holder has not been established")
        return self


class ReviewDecision(ReviewSubmission):
    stage: Stage
    decision: Literal["APPROVE", "REJECT"]
    qualification_id: uuid.UUID
    qualification_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=4000)
    rights: ReviewRights | None = None

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("a review reason is required")
        return value

    @model_validator(mode="after")
    def check_rights_scope(self) -> ReviewDecision:
        if (self.stage == "RIGHTS" and self.decision == "APPROVE") != (self.rights is not None):
            raise ValueError("rights terms are required only for approval of rights")
        return self
