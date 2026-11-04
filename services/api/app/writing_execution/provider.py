"""Provider contract and strict output shape, independent of model transport."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class WritingProviderError(RuntimeError):
    category = "PROVIDER_UNAVAILABLE"


class ProviderTimeout(WritingProviderError):
    category = "PROVIDER_TIMEOUT"


class ProviderUnavailable(WritingProviderError):
    category = "PROVIDER_UNAVAILABLE"


class InvalidProviderOutput(WritingProviderError):
    category = "INVALID_PROVIDER_OUTPUT"


class ProviderConfigurationError(WritingProviderError):
    category = "PROVIDER_UNCONFIGURED"


class ProviderPolicyBlock(WritingProviderError):
    category = "PROVIDER_POLICY_BLOCK"


@dataclass(frozen=True)
class WritingProposalResult:
    proposal: dict[str, Any]
    provider: str
    model_name: str
    adapter_version: str
    provider_request_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


class WritingProposalProvider(Protocol):
    provider: str
    model_name: str
    adapter_version: str

    def generate(
        self, input: dict[str, Any], *, configuration: dict[str, Any], limits: dict[str, int]
    ) -> WritingProposalResult: ...


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def build_output_schema(input: dict[str, Any] | None = None) -> dict[str, Any]:
    """Schema conformance is followed by exact domain/Decimal/evidence validation."""
    data = input or {}
    source = data.get("source", {})
    rubric = data.get("rubric", {})
    codes = [str(c["code"]) for c in rubric.get("criteria", [])]
    levels = [str(level["code"]) for level in rubric.get("performance_levels", [])]
    length = len(str(data.get("candidate_text", ""))) if input else 100_000

    def identity(key: str, value: Any) -> dict[str, Any]:
        return {
            "type": "string",
            **({"enum": [str(value)]} if value is not None else {"maxLength": 64}),
        }

    evidence = _object(
        {
            "start": {"type": "integer", "minimum": 0, "maximum": length},
            "end": {"type": "integer", "minimum": 0, "maximum": length},
            "quote": {"type": "string", "minLength": 1, "maxLength": 2000},
        }
    )
    criterion = _object(
        {
            "criterion_code": {
                "type": "string",
                **({"enum": codes} if codes else {"maxLength": 120}),
            },
            "proposed_points": {"type": "string", "pattern": r"^\d{1,6}(\.\d{1,4})?$"},
            "proposed_level": {
                "type": "string",
                **({"enum": levels} if levels else {"maxLength": 120}),
            },
            # Qualitative model self-report, never a calibrated probability.
            "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
            "evidence": {"type": "array", "items": evidence, "minItems": 1, "maxItems": 10},
            "rationale": {"type": "string", "minLength": 1, "maxLength": 2000},
            "uncertainties": {
                "type": "array",
                "items": {"type": "string", "maxLength": 240},
                "maxItems": 10,
            },
            "disagreement": _object({}),
        }
    )
    properties: dict[str, Any] = {
        "schema_version": {"type": "string", "enum": ["1"]},
        "execution_id": identity("execution_id", data.get("execution_id")),
        "ai_run_id": identity("ai_run_id", data.get("ai_run_id")),
    }
    for key in (
        "response_revision_id",
        "response_checksum",
        "rubric_version_id",
        "rubric_checksum",
    ):
        properties[key] = identity(key, source.get(key))
    properties["criteria"] = {
        "type": "array",
        "items": criterion,
        "minItems": len(codes) if codes else 1,
        "maxItems": len(codes) if codes else 30,
    }
    properties["consistency"] = _object(
        {
            "status": {"type": "string", "enum": ["PASS", "WARNING"]},
            "criterion_codes": {
                "type": "array",
                "items": {"type": "string", **({"enum": codes} if codes else {})},
                "maxItems": 30,
            },
        }
    )
    return _object(properties)
