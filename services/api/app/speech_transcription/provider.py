from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class ProviderError(RuntimeError):
    category = "PROVIDER_UNAVAILABLE"


class ProviderTimeout(ProviderError):
    category = "PROVIDER_TIMEOUT"


class ProviderUnavailable(ProviderError):
    category = "PROVIDER_UNAVAILABLE"


class InvalidProviderResult(ProviderError):
    category = "INVALID_PROVIDER_OUTPUT"


class EmptyProviderResult(ProviderError):
    category = "EMPTY_RESULT"


@dataclass(frozen=True)
class TranscriptionSegment:
    segment_id: str
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "segment_id": self.segment_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
        }
        if self.confidence is not None:
            value["confidence"] = self.confidence
        return value


@dataclass(frozen=True)
class TranscriptionResult:
    transcript: str
    segments: tuple[TranscriptionSegment, ...] = ()
    provider: str | None = None
    model_name: str | None = None
    adapter_version: str = "1"
    metadata: dict[str, Any] = field(default_factory=dict)


class SpeechTranscriptionProvider(Protocol):
    provider: str
    model_name: str
    adapter_version: str

    def transcribe(
        self,
        audio: bytes,
        *,
        language: str,
        configuration: dict[str, Any],
        limits: dict[str, int],
    ) -> TranscriptionResult: ...


class ConfiguredTranscriptionProvider:
    """Production adapter boundary supplied by an explicitly configured handler."""

    def __init__(
        self,
        handler: Callable[..., TranscriptionResult],
        *,
        provider: str,
        model_name: str,
        adapter_version: str,
    ) -> None:
        self.handler = handler
        self.provider = provider
        self.model_name = model_name
        self.adapter_version = adapter_version

    def transcribe(
        self,
        audio: bytes,
        *,
        language: str,
        configuration: dict[str, Any],
        limits: dict[str, int],
    ) -> TranscriptionResult:
        return self.handler(
            audio,
            language=language,
            configuration=configuration,
            limits=limits,
        )


class DeterministicTranscriptionProvider:
    """Synthetic provider for CI and unit tests; never a production fallback."""

    provider = "DETERMINISTIC_TEST"
    model_name = "fixture-transcriber"
    adapter_version = "1"

    def __init__(
        self,
        result: TranscriptionResult | None = None,
        *,
        scenario: str = "success",
        retry_success_after: int = 1,
    ) -> None:
        self.result = result or TranscriptionResult(
            transcript="Das ist eine synthetische Transkription.",
            segments=(
                TranscriptionSegment("s1", 0, 1000, "Das ist eine synthetische Transkription."),
            ),
        )
        self.scenario = scenario
        self.retry_success_after = retry_success_after
        self.calls = 0

    def transcribe(
        self,
        audio: bytes,
        *,
        language: str,
        configuration: dict[str, Any],
        limits: dict[str, int],
    ) -> TranscriptionResult:
        del audio, language, configuration, limits
        self.calls += 1
        if self.scenario == "timeout":
            raise ProviderTimeout("synthetic timeout")
        if self.scenario == "unavailable":
            raise ProviderUnavailable("synthetic unavailable")
        if self.scenario == "malformed":
            return TranscriptionResult(transcript="", segments=())
        if self.scenario == "retry-then-success" and self.calls < self.retry_success_after:
            raise ProviderTimeout("synthetic transient timeout")
        return self.result
