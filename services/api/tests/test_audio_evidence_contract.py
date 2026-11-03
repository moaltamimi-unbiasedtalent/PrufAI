"""Focused policy/boundary/model tests; not substitutes for real PG/S3/media proof."""

import asyncio
import copy
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app.authorization import TenantContext
from app.knowledge.storage import InMemoryObjectStorage, StorageError
from app.models import (
    CandidateAudioRecordingSession,
    CandidateMediaAsset,
    CandidateResponseMediaBinding,
)
from app.models.enums import CandidateMediaStatus
from app.question_bank.schemas import AudioRecordingPolicy, parse_item_content
from app.runtime.api import finalize_audio_recording
from app.speaking_audio.service import SpeakingAudioService


def policy():
    return {
        "max_recording_seconds": 30,
        "max_recording_attempts": 2,
        "max_upload_bytes": 2 * 1024 * 1024,
        "supported_mime_types": ["audio/wav"],
        "upload_grace_seconds": 120,
        "submission_behavior": "REQUIRE_ATTACHED",
    }


def test_typed_audio_policy_preserves_legacy_content_and_has_one_effective_source():
    legacy = {"kind": "ORAL_RESPONSE", "prompt": "Original synthetic oral task."}
    assert parse_item_content(legacy).audio_policy is None
    assert "audio_policy" not in legacy
    content = {**legacy, "audio_policy": policy()}
    parsed = parse_item_content(content)
    assert parsed.audio_policy.model_dump() == policy()
    assert SpeakingAudioService.policy({"content": content}) == policy()


@pytest.mark.parametrize(
    "change",
    [
        {"max_recording_seconds": 0},
        {"max_recording_seconds": True},
        {"max_recording_attempts": 0},
        {"max_upload_bytes": 25 * 1024 * 1024 + 1},
        {"upload_grace_seconds": 301},
        {"supported_mime_types": ["audio/webm"]},
        {"supported_mime_types": ["audio/mpeg"]},
        {"submission_behavior": "AUTO_SUBMIT"},
        {"executable": "do not run"},
    ],
)
def test_governed_policy_rejects_unbounded_unsupported_or_client_invented_values(change):
    with pytest.raises(ValidationError):
        AudioRecordingPolicy.model_validate({**policy(), **change})


def test_actual_recorded_evidence_never_uses_memory_storage():
    with pytest.raises(StorageError, match="durable private S3"):
        SpeakingAudioService.durable_storage(InMemoryObjectStorage())


def test_upload_stream_is_bounded_even_when_content_length_lies(monkeypatch):
    context = TenantContext(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    recording = SimpleNamespace(
        candidate_user_id=context.user_id,
        organisation_id=context.organisation_id,
        policy_snapshot={"max_upload_bytes": 8},
    )
    database = SimpleNamespace(get=lambda *_: recording, rollback=lambda: None)
    monkeypatch.setattr("app.runtime.api._audio_storage", lambda: object())

    def forbidden_finalize(*_args, **_kwargs):
        pytest.fail("oversized content reached finalization")

    monkeypatch.setattr(SpeakingAudioService, "finalize", forbidden_finalize)
    chunks = iter([b"123456", b"789012"])

    async def receive():
        return {"type": "http.request", "body": next(chunks), "more_body": True}

    request = Request({"type": "http", "headers": [(b"content-length", b"1")]}, receive=receive)
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(
            finalize_audio_recording(
                uuid.uuid4(), request, SimpleNamespace(tenant_context=context), database
            )
        )
    assert rejected.value.status_code == 400
    assert "size" in rejected.value.detail


def _recording(session):
    record = CandidateAudioRecordingSession(
        attempt_id=uuid.uuid4(),
        exam_version_item_binding_id=uuid.uuid4(),
        candidate_user_id=uuid.uuid4(),
        organisation_id=uuid.uuid4(),
        attempt_number=1,
        operation_id=uuid.uuid4(),
        status=CandidateMediaStatus.RECORDING,
        policy_snapshot=policy(),
        storage_key="unit-private-identity",
        evidence_identity={"unit_fixture": True},
    )
    session.add(record)
    session.commit()
    return record


@pytest.mark.parametrize("field", ["upload_sha256", "storage_key", "policy_snapshot"])
def test_durable_upload_receipt_cannot_be_repurposed_after_staging(session, field):
    recording = _recording(session)
    recording.upload_sha256 = "a" * 64
    recording.upload_byte_size = 12
    recording.status = CandidateMediaStatus.UPLOADING
    session.commit()
    original = copy.deepcopy(recording.policy_snapshot)
    if field == "policy_snapshot":
        recording.policy_snapshot["max_recording_seconds"] = 999
    else:
        setattr(recording, field, "different")
    with pytest.raises(ValueError, match="immutable"):
        session.commit()
    session.rollback()
    assert recording.upload_sha256 == "a" * 64
    assert recording.policy_snapshot == original


def test_ready_audio_and_response_binding_are_immutable_model_history(session):
    recording = _recording(session)
    asset = CandidateMediaAsset(
        organisation_id=recording.organisation_id,
        candidate_user_id=recording.candidate_user_id,
        attempt_id=recording.attempt_id,
        exam_version_item_binding_id=recording.exam_version_item_binding_id,
        recording_session_id=recording.id,
        content_type="audio/wav",
        byte_size=12,
        sha256="b" * 64,
        duration_ms=1000,
        storage_key="unit-finalized-object",
        status=CandidateMediaStatus.READY,
        created_at=datetime.now(timezone.utc),
        validation_provenance={"unit_model_fixture": True},
    )
    session.add(asset)
    session.commit()
    asset.validation_provenance["unit_model_fixture"] = False
    with pytest.raises(ValueError, match="immutable"):
        session.commit()
    session.rollback()
    binding = CandidateResponseMediaBinding(
        candidate_response_revision_id=uuid.uuid4(),
        candidate_media_asset_id=asset.id,
        binding_role="PRIMARY_AUDIO",
        created_at=datetime.now(timezone.utc),
    )
    session.add(binding)
    session.commit()
    binding.candidate_media_asset_id = uuid.uuid4()
    with pytest.raises(ValueError, match="immutable"):
        session.commit()
    session.rollback()
