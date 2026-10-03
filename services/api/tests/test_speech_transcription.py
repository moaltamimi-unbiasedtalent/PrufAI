import hashlib
import uuid
from datetime import datetime, timezone

import pytest

from app.knowledge.storage import InMemoryObjectStorage
from app.models import (
    AIModelProfile,
    CandidateMediaAsset,
    CandidateResponseMediaBinding,
    SpeechTranscription,
)
from app.models.enums import AIArtifactStatus, CandidateMediaStatus, TranscriptionStatus
from app.speech_transcription import (
    AudioEvidenceReference,
    DeterministicTranscriptionProvider,
    InvalidProviderResult,
    ProviderTimeout,
    SpeechTranscriptionService,
    TranscriptEvidenceReference,
    TranscriptionResult,
    TranscriptionSegment,
)


def _asset(
    session, content: bytes = b"synthetic audio"
) -> tuple[CandidateMediaAsset, AIModelProfile, InMemoryObjectStorage]:
    now = datetime.now(timezone.utc)
    asset = CandidateMediaAsset(
        organisation_id=uuid.uuid4(),
        candidate_user_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        exam_version_item_binding_id=uuid.uuid4(),
        recording_session_id=uuid.uuid4(),
        content_type="audio/ogg",
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        storage_key=f"audio/{uuid.uuid4()}",
        status=CandidateMediaStatus.READY,
        created_at=now,
    )
    session.add(asset)
    session.flush()
    session.add(
        CandidateResponseMediaBinding(
            candidate_response_revision_id=uuid.uuid4(),
            candidate_media_asset_id=asset.id,
            binding_role="PRIMARY_AUDIO",
            created_at=now,
        )
    )
    profile = AIModelProfile(
        code=f"speech.{uuid.uuid4()}",
        name="Synthetic speech profile",
        provider="TEST",
        model_name="fixture-v1",
        purpose="SPEECH_TRANSCRIPTION",
        status=AIArtifactStatus.ACTIVE,
        configuration={"response_format": "segments"},
    )
    session.add(profile)
    session.flush()
    storage = InMemoryObjectStorage()
    storage.put(asset.storage_key, content, asset.content_type)
    return asset, profile, storage


def test_transcription_pins_audio_and_is_versioned(session):
    asset, profile, storage = _asset(session)
    provider = DeterministicTranscriptionProvider(
        result=TranscriptionResult(
            transcript=" Ich  wohne  in Berlin. ",
            segments=(TranscriptionSegment("s1", 0, 1000, "Ich wohne in Berlin."),),
        )
    )
    version = SpeechTranscriptionService.transcribe(
        session,
        asset_id=asset.id,
        profile_id=profile.id,
        storage=storage,
        provider=provider,
        language="de",
    )
    assert version.status is TranscriptionStatus.READY
    assert version.candidate_media_asset_id == asset.id
    assert version.audio_sha256 == asset.sha256
    assert version.transcript == "Ich wohne in Berlin."
    assert SpeechTranscriptionService.current_version(session, version.transcription_id) == version


def test_retry_then_success_and_retranscription_keep_history(session):
    asset, profile, storage = _asset(session)
    first = SpeechTranscriptionService.transcribe(
        session,
        asset_id=asset.id,
        profile_id=profile.id,
        storage=storage,
        provider=DeterministicTranscriptionProvider(
            scenario="retry-then-success", retry_success_after=2
        ),
        language="de",
    )
    second = SpeechTranscriptionService.transcribe(
        session,
        asset_id=asset.id,
        profile_id=profile.id,
        storage=storage,
        provider=DeterministicTranscriptionProvider(
            result=TranscriptionResult(transcript="Ich wohne jetzt in Berlin.")
        ),
        language="de",
    )
    assert first.retry_count == 1
    assert second.version_number == 2
    assert first.status is TranscriptionStatus.SUPERSEDED
    assert SpeechTranscriptionService.get_version(session, first.id).transcript != second.transcript


def test_invalid_profile_and_provider_output_are_blocked(session):
    asset, profile, storage = _asset(session)
    profile.purpose = "GENERAL_CHAT"
    with pytest.raises(ValueError, match="not eligible"):
        SpeechTranscriptionService.transcribe(
            session,
            asset_id=asset.id,
            profile_id=profile.id,
            storage=storage,
            provider=DeterministicTranscriptionProvider(),
            language="de",
        )
    profile.purpose = "SPEECH_TRANSCRIPTION"
    with pytest.raises(InvalidProviderResult, match="invalid segment timing"):
        SpeechTranscriptionService.transcribe(
            session,
            asset_id=asset.id,
            profile_id=profile.id,
            storage=storage,
            provider=DeterministicTranscriptionProvider(
                result=TranscriptionResult(
                    transcript="text",
                    segments=(TranscriptionSegment("bad", -1, 2, "text"),),
                )
            ),
            language="de",
        )


def test_failure_does_not_invalidate_primary_audio(session):
    asset, profile, storage = _asset(session)
    with pytest.raises(ProviderTimeout):
        SpeechTranscriptionService.transcribe(
            session,
            asset_id=asset.id,
            profile_id=profile.id,
            storage=storage,
            provider=DeterministicTranscriptionProvider(scenario="timeout"),
            language="de",
        )
    transcription = session.query(SpeechTranscription).one()
    assert transcription.status is TranscriptionStatus.FAILED
    assert asset.status is CandidateMediaStatus.READY


def test_evidence_references_pin_exact_coordinates(session):
    asset, profile, storage = _asset(session)
    version = SpeechTranscriptionService.transcribe(
        session,
        asset_id=asset.id,
        profile_id=profile.id,
        storage=storage,
        provider=DeterministicTranscriptionProvider(),
        language="de",
    )
    SpeechTranscriptionService.validate_audio_reference(
        session, AudioEvidenceReference(asset.id, 0, 1)
    )
    SpeechTranscriptionService.validate_transcript_reference(
        session, TranscriptEvidenceReference(version.id, "s1", 0, 4)
    )
