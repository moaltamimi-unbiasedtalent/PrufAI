from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, List, Literal, Optional, Union, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MultipleChoiceOption(StrictSchema):
    option_id: str = Field(min_length=1, max_length=80)
    display_text: str = Field(min_length=1, max_length=2000)
    order: int = Field(ge=0, le=100)


class MultipleChoiceContent(StrictSchema):
    kind: Literal["MULTIPLE_CHOICE"]
    prompt: str = Field(min_length=1, max_length=8000)
    options: List[MultipleChoiceOption] = Field(min_length=2, max_length=10)
    correct_option_ids: List[str] = Field(min_length=1, max_length=10)

    @field_validator("options")
    @classmethod
    def unique_option_ids(cls, value: List[MultipleChoiceOption]) -> List[MultipleChoiceOption]:
        ids = [item.option_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("option IDs must be unique")
        return value


class ExtendedWritingContent(StrictSchema):
    kind: Literal["EXTENDED_WRITING"]
    prompt: str = Field(min_length=1, max_length=8000)
    scenario: Optional[str] = Field(default=None, max_length=4000)
    response_guidance: Optional[str] = Field(default=None, max_length=4000)


class OralResponseContent(StrictSchema):
    kind: Literal["ORAL_RESPONSE"]
    prompt: str = Field(min_length=1, max_length=8000)
    preparation_seconds: Optional[int] = Field(default=None, ge=0, le=3600)
    interaction_mode: Optional[str] = Field(default=None, max_length=80)
    audio_policy: Optional[AudioRecordingPolicy] = None


class AudioRecordingPolicy(StrictSchema):
    max_recording_seconds: int = Field(ge=1, le=3600, strict=True)
    max_recording_attempts: int = Field(ge=1, le=20, strict=True)
    max_upload_bytes: int = Field(ge=1, le=25 * 1024 * 1024, strict=True)
    supported_mime_types: List[
        Literal[
            "audio/webm;codecs=opus",
            "audio/ogg;codecs=opus",
            "audio/mp4;codecs=mp4a.40.2",
            "audio/wav",
        ]
    ] = Field(min_length=1, max_length=4)
    upload_grace_seconds: int = Field(ge=0, le=300, strict=True)
    submission_behavior: Literal["REQUIRE_ATTACHED"]


OralResponseContent.model_rebuild()


ItemContent = Annotated[
    Union[MultipleChoiceContent, ExtendedWritingContent, OralResponseContent],
    Field(discriminator="kind"),
]
ITEM_CONTENT_ADAPTER: TypeAdapter[ItemContent] = TypeAdapter(ItemContent)


class ScoringMetadata(StrictSchema):
    policy: str
    max_points: Optional[Decimal] = Field(default=None, ge=0)
    partial_credit: Optional[bool] = None
    rubric_required: bool = False
    rubric_reference: Optional[str] = Field(default=None, max_length=240)


def parse_item_content(content: dict[str, Any]) -> StrictSchema:
    return cast(StrictSchema, ITEM_CONTENT_ADAPTER.validate_python(content))
