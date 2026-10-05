import uuid
from types import SimpleNamespace

from app.result_governance.transcription_factory import TranscriptionCorrectionFactory
from app.speech_transcription import (
    DeterministicTranscriptionProvider,
    SpeechTranscriptionService,
    TranscriptionResult,
    TranscriptionSegment,
)
from tests.test_speech_transcription import _asset


def test_transcription_correction_factory_creates_a_new_version_from_same_audio(session):
    asset, profile, storage = _asset(session, b"correction audio")
    first = SpeechTranscriptionService.transcribe(
        session,
        asset_id=asset.id,
        profile_id=profile.id,
        storage=storage,
        provider=DeterministicTranscriptionProvider(
            result=TranscriptionResult(
                transcript="erste Fassung",
                segments=(TranscriptionSegment("s1", 0, 500, "erste Fassung"),),
            )
        ),
        language="de",
    )
    factory = TranscriptionCorrectionFactory(
        storage=storage,
        provider=DeterministicTranscriptionProvider(
            result=TranscriptionResult(
                transcript="zweite Fassung",
                segments=(TranscriptionSegment("s1", 0, 500, "zweite Fassung"),),
            )
        ),
        profile_id=profile.id,
        language="de",
        historical_version_id=first.id,
    )
    second = factory(session, None, SimpleNamespace(id=uuid.uuid4(), attempt_id=asset.attempt_id))
    assert second.id != first.id
    assert second.transcription_id == first.transcription_id
    assert second.candidate_media_asset_id == asset.id
    assert second.audio_sha256 == asset.sha256
    assert second.supersedes_version_id == first.id
