import re
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.writing_assessment.service import canonical_checksum

MetadataField = Literal[
    "title",
    "publisher",
    "version_label",
    "publication_date",
    "effective_from",
    "effective_until",
    "rights_status",
]
Decision = Literal["METADATA_ONLY", "DENY"]
SHA256 = r"^[a-f0-9]{64}$"
METADATA_FIELDS: frozenset[MetadataField] = frozenset(
    (
        "title",
        "publisher",
        "version_label",
        "publication_date",
        "effective_from",
        "effective_until",
        "rights_status",
    )
)
DECISIONS: frozenset[Decision] = frozenset(("METADATA_ONLY", "DENY"))


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation_id: UUID


class ResourcePin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: UUID
    metadata_checksum: str = Field(pattern=SHA256)
    metadata_fields: frozenset[MetadataField] = Field(default=METADATA_FIELDS, min_length=1)
    allowed_decisions: frozenset[Decision] = Field(default=DECISIONS, min_length=1)

    def canonical(self) -> dict[str, object]:
        return {
            "version_id": str(self.version_id),
            "metadata_checksum": self.metadata_checksum,
            "metadata_fields": sorted(self.metadata_fields),
            "allowed_decisions": sorted(self.allowed_decisions),
        }


class CreateGrant(Operation):
    reviewer_user_id: UUID
    purpose: str = Field(min_length=1, max_length=240)
    business_approval_reference: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    approved_scope_checksum: str = Field(pattern=SHA256)
    starts_at: AwareDatetime
    expires_at: AwareDatetime
    resources: list[ResourcePin] = Field(min_length=1, max_length=25)

    @field_validator("starts_at", "expires_at", mode="before")
    @classmethod
    def require_explicit_offset(cls, value: object) -> object:
        if isinstance(value, str) and re.search(r"(?:Z|[+-][0-9]{2}:[0-9]{2})$", value):
            return value
        if isinstance(value, datetime) and value.utcoffset() is not None:
            return value
        raise ValueError("an explicit UTC offset is required")

    @model_validator(mode="after")
    def validate_scope(self) -> "CreateGrant":
        self.starts_at = self.starts_at.astimezone(timezone.utc)
        self.expires_at = self.expires_at.astimezone(timezone.utc)
        if self.starts_at >= self.expires_at:
            raise ValueError("grant expiration must follow activation")
        if len({pin.version_id for pin in self.resources}) != len(self.resources):
            raise ValueError("duplicate source version")
        return self


class ReviewDecision(Operation):
    expected_metadata_checksum: str = Field(pattern=SHA256)
    decision: Literal["METADATA_ONLY", "DENY", "ALLOW_SYNTHETIC_INGESTION", "WITHDRAW"]
    basis_reference: str = Field(min_length=1, max_length=240)
    rights_holder: str = Field(min_length=1, max_length=300)
    licence_type: str = Field(min_length=1, max_length=120)
    retention_policy: str = Field(min_length=1, max_length=240)


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def grant_scope_checksum(organisation_id: UUID, issuer_id: UUID, payload: CreateGrant) -> str:
    return canonical_checksum(
        {
            "schema_version": 1,
            "reviewer_user_id": str(payload.reviewer_user_id),
            "organisation_id": str(organisation_id),
            "authorized_by": str(issuer_id),
            "purpose": payload.purpose,
            "starts_at": timestamp(payload.starts_at),
            "expires_at": timestamp(payload.expires_at),
            "resources": [
                pin.canonical() for pin in sorted(payload.resources, key=lambda pin: pin.version_id)
            ],
        }
    )
