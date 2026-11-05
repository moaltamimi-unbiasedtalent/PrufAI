"""Private evaluation case contracts, not an authority to create expert labels."""

from __future__ import annotations

import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EvidenceClass = Literal[
    "TECHNICAL_TEST", "SYNTHETIC_REGRESSION", "EXPERT_LABELLED", "REAL_PROVIDER", "PILOT_CALIBRATED"
]
LabelKind = Literal["SYNTHETIC_EXPECTATION", "SINGLE_EXPERT", "ADJUDICATED_EXPERT"]
CaseKind = Literal["TRANSCRIPTION", "WRITING", "SPEAKING", "RETRIEVAL"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ExpertJudgment(Contract):
    reviewer_id: str = Field(min_length=1, max_length=120)
    points: Decimal

    @field_validator("points", mode="before")
    @classmethod
    def finite_points(cls, value: Any) -> Any:
        try:
            valid = not isinstance(value, bool) and Decimal(str(value)).is_finite()
        except (InvalidOperation, ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("a judgment requires finite numeric points")
        return value


class CriterionReference(Contract):
    code: str = Field(min_length=1, max_length=120)
    points: Decimal | None = None
    max_points: Decimal = Field(ge=0, le=1_000_000)
    judgments: list[ExpertJudgment] = Field(default_factory=list, max_length=20)

    @field_validator("points", "max_points", mode="before")
    @classmethod
    def finite_points(cls, value: Any) -> Any:
        try:
            valid = value is None or (
                not isinstance(value, bool) and Decimal(str(value)).is_finite()
            )
        except (InvalidOperation, ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("criterion reference points must be finite numbers")
        return value

    @model_validator(mode="after")
    def bounded_reference(self) -> CriterionReference:
        values = [j.points for j in self.judgments]
        if self.points is not None:
            values.append(self.points)
        if any(not 0 <= value <= self.max_points for value in values):
            raise ValueError("reference judgment exceeds its rubric bounds")
        actors = [j.reviewer_id for j in self.judgments]
        if len(actors) != len(set(actors)):
            raise ValueError("human judgments require distinct reviewer identities")
        return self


class SourceReference(Contract):
    id: str = Field(min_length=1, max_length=120)
    version_id: str = Field(min_length=1, max_length=120)
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    organisation_id: str | None = Field(default=None, max_length=120)
    authorized: bool
    text: str | None = Field(default=None, max_length=10_000)
    relevant: bool | None = None


class CitationSupportReference(Contract):
    source_id: str = Field(min_length=1, max_length=120)
    quote: str = Field(min_length=1, max_length=2000)
    supported: bool


class CaseReference(Contract):
    label_kind: LabelKind = "SYNTHETIC_EXPECTATION"
    text: str | None = Field(default=None, max_length=4000)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    criteria: list[CriterionReference] = Field(default_factory=list, max_length=50)
    sources: list[SourceReference] = Field(default_factory=list, max_length=100)
    citation_support: list[CitationSupportReference] = Field(default_factory=list, max_length=100)
    evidence_identity: dict[str, str] = Field(default_factory=dict, max_length=32)
    segments_supported: bool = False

    @model_validator(mode="after")
    def unique_references(self) -> CaseReference:
        for values in (
            [c.code for c in self.criteria],
            [s.id for s in self.sources],
        ):
            if len(values) != len(set(values)):
                raise ValueError("reference identities must be unique")
        if self.label_kind == "SYNTHETIC_EXPECTATION" and any(c.judgments for c in self.criteria):
            raise ValueError("synthetic expectations cannot declare human expert judgments")
        support_keys = [(label.source_id, label.quote) for label in self.citation_support]
        if len(support_keys) != len(set(support_keys)):
            raise ValueError("citation support labels require unique source and quote identities")
        source_ids = {source.id for source in self.sources}
        if any(label.source_id not in source_ids for label in self.citation_support):
            raise ValueError("citation support label refers to an undeclared source")
        return self


class CaseInput(Contract):
    text: str | None = Field(default=None, max_length=10_000)
    language: str = Field(default="de", pattern=r"^[a-z]{2}$")
    modality: Literal["TEXT", "AUDIO", "TRANSCRIPT"] | None = None
    audio_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    duration_ms: int | None = Field(default=None, gt=0, le=1_800_000, strict=True)
    audio_kind: Literal["SPEECH", "TECHNICAL_TONE", "NONE"] = "NONE"
    evidence_identity: dict[str, str] = Field(default_factory=dict, max_length=32)
    adversarial: bool = False


class EvaluationCase(Contract):
    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
    kind: CaseKind
    split: Literal["TRAIN", "CALIBRATION", "TEST"] = "TEST"
    input: CaseInput
    reference: CaseReference
    synthetic_observation: dict[str, Any] | None = None
    execution_job_id: str | None = Field(default=None, max_length=120)


class MetricConfiguration(Contract):
    schema_version: Literal["1"] = "1"
    normalization: Literal["NFC_WHITESPACE", "NFC_WHITESPACE_CASEFOLD"] = "NFC_WHITESPACE"
    retrieval_k: int = Field(default=5, ge=1, le=100, strict=True)


class SegmentObservation(Contract):
    start_ms: int = Field(strict=True)
    end_ms: int = Field(strict=True)
    text: str | None = Field(default=None, max_length=4000)


class CriterionObservation(Contract):
    criterion_code: str = Field(min_length=1, max_length=120)
    proposed_points: Any
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class RetrievedObservation(Contract):
    id: str = Field(min_length=1, max_length=120)
    version_id: str = Field(min_length=1, max_length=120)
    checksum: str = Field(min_length=1, max_length=120)
    organisation_id: str | None = Field(default=None, max_length=120)
    text: str | None = Field(default=None, max_length=10_000)


class CitationObservation(Contract):
    source_id: str = Field(min_length=1, max_length=120)
    version_id: str = Field(min_length=1, max_length=120)
    checksum: str = Field(min_length=1, max_length=120)
    quote: str | None = Field(default=None, max_length=2000)


class EvaluationObservation(Contract):
    evidence_identity: dict[str, str] = Field(default_factory=dict, max_length=32)
    actions: list[str] = Field(default_factory=list, max_length=30)
    tool_calls: list[str] = Field(default_factory=list, max_length=30)
    text: str | None = Field(default=None, max_length=4000)
    language: str | None = Field(default=None, max_length=20)
    audio_sha256: str | None = Field(default=None, max_length=120)
    segments: list[SegmentObservation] = Field(default_factory=list, max_length=100)
    criteria: list[CriterionObservation] = Field(default_factory=list, max_length=50)
    modality: Literal["TEXT", "AUDIO", "TRANSCRIPT"] | None = None
    retrieved: list[RetrievedObservation] = Field(default_factory=list, max_length=100)
    citations: list[CitationObservation] = Field(default_factory=list, max_length=100)


def validate_case_collection(cases: list[dict[str, Any]]) -> None:
    """Reject detectable exact leakage, without claiming semantic deduplication."""
    if not 1 <= len(cases) <= 100:
        raise ValueError("an evaluation dataset requires between one and 100 bounded cases")
    parsed = [EvaluationCase.model_validate(case) for case in cases]
    ids = [case.case_id for case in parsed]
    if len(ids) != len(set(ids)):
        raise ValueError("evaluation case identities must be unique")
    observed: dict[tuple[str, str], str] = {}
    for case in parsed:
        identities = []
        for text in (case.input.text, case.reference.text):
            if text is not None:
                normalized = " ".join(unicodedata.normalize("NFC", text).split())
                if normalized:
                    identities.append(("TEXT", normalized))
        if case.input.audio_sha256:
            identities.append(("AUDIO", case.input.audio_sha256))
        if case.execution_job_id:
            identities.append(("EXECUTION", case.execution_job_id))
        for identity in identities:
            previous_split = observed.get(identity)
            if previous_split is not None and previous_split != case.split:
                raise ValueError("dataset split leakage detected across exact evidence identities")
            observed[identity] = case.split
