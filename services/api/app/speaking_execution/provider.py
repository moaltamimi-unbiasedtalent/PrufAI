"""Audio-capable provider boundary; native schema enforcement is not assumed."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class SpeakingProviderError(RuntimeError):
    category = "PROVIDER_UNAVAILABLE"


class ProviderTimeout(SpeakingProviderError):
    category = "PROVIDER_TIMEOUT"


class ProviderUnavailable(SpeakingProviderError):
    category = "PROVIDER_UNAVAILABLE"


class InvalidProviderOutput(SpeakingProviderError):
    category = "INVALID_PROVIDER_OUTPUT"


class ProviderConfigurationError(SpeakingProviderError):
    category = "PROVIDER_UNCONFIGURED"


class ProviderPolicyBlock(SpeakingProviderError):
    category = "PROVIDER_POLICY_BLOCK"


class AudioTransportError(SpeakingProviderError):
    category = "AUDIO_TRANSPORT_UNSUPPORTED"


@dataclass(frozen=True)
class AudioTransport:
    content: bytes
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SpeakingProposalResult:
    proposal: dict[str, Any]
    provider: str
    model_name: str
    adapter_version: str
    provider_request_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


class SpeakingProposalProvider(Protocol):
    provider: str
    model_name: str
    adapter_version: str

    def generate(
        self,
        input: dict[str, Any],
        *,
        audio: bytes,
        configuration: dict[str, Any],
        limits: dict[str, int],
    ) -> SpeakingProposalResult: ...


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def build_output_schema(input: dict[str, Any] | None = None) -> dict[str, Any]:
    data, source, rubric = (
        input or {},
        (input or {}).get("source", {}),
        (input or {}).get("rubric", {}),
    )
    codes = [criterion["code"] for criterion in rubric.get("criteria", [])]
    levels = [level["code"] for level in rubric.get("performance_levels", [])]

    def identity(value: Any) -> dict[str, Any]:
        return {
            "type": "string",
            **({"enum": [str(value)]} if value is not None else {"maxLength": 64}),
        }

    duration = source.get("duration_ms", 180_000)
    evidence = _object(
        {
            "type": {"type": "string", "enum": ["AUDIO"]},
            "candidate_media_asset_id": identity(source.get("candidate_media_asset_id")),
            "audio_sha256": identity(source.get("audio_sha256")),
            "start_ms": {"type": "integer", "minimum": 0, "maximum": duration},
            "end_ms": {"type": "integer", "minimum": 1, "maximum": duration},
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
    properties = {
        "schema_version": {"type": "string", "enum": ["1"]},
        "execution_id": identity(data.get("execution_id")),
        "ai_run_id": identity(data.get("ai_run_id")),
        "evidence_mode": {"type": "string", "enum": ["AUDIO"]},
        **{
            key: identity(source.get(key))
            for key in (
                "response_revision_id",
                "response_checksum",
                "rubric_version_id",
                "rubric_checksum",
                "candidate_media_asset_id",
                "audio_sha256",
            )
        },
        "duration_ms": {
            "type": "integer",
            "minimum": 1,
            "maximum": duration,
            **({"enum": [duration]} if input is not None else {}),
        },
        "criteria": {
            "type": "array",
            "items": criterion,
            "minItems": len(codes) if codes else 1,
            "maxItems": len(codes) if codes else 50,
        },
        "consistency": _object(
            {
                "status": {"type": "string", "enum": ["PASS", "WARNING"]},
                "criterion_codes": {
                    "type": "array",
                    "items": {"type": "string", **({"enum": codes} if codes else {})},
                    "maxItems": 50,
                },
            }
        ),
    }
    return _object(properties)
