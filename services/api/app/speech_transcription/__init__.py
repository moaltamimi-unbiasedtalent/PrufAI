"""Governed, versioned speech transcription and derived evidence."""

from app.speech_transcription.provider import (
    ConfiguredTranscriptionProvider,
    DeterministicTranscriptionProvider,
    InvalidProviderResult,
    ProviderError,
    ProviderTimeout,
    SpeechTranscriptionProvider,
    TranscriptionResult,
    TranscriptionSegment,
)
from app.speech_transcription.service import (
    AudioEvidenceReference,
    SpeechTranscriptionService,
    TranscriptEvidenceReference,
)

__all__ = [
    "AudioEvidenceReference",
    "ConfiguredTranscriptionProvider",
    "DeterministicTranscriptionProvider",
    "InvalidProviderResult",
    "ProviderError",
    "ProviderTimeout",
    "SpeechTranscriptionProvider",
    "SpeechTranscriptionService",
    "TranscriptEvidenceReference",
    "TranscriptionResult",
    "TranscriptionSegment",
]
