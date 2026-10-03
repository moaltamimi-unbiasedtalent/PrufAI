import pytest

from app.models.enums import PackResponseMode
from app.runtime.service import CandidateRuntimeService
from app.speaking_audio.service import (
    AudioValidationError,
    SpeakingAudioService,
    safe_audio_filename,
    validate_audio_bytes,
)


@pytest.mark.parametrize(
    ("mime", "content"),
    [
        ("audio/webm", b"\x1a\x45\xdf\xa3" + b"audio"),
        ("audio/ogg", b"OggS" + b"audio"),
        ("audio/mp4", b"\x00\x00\x00\x18ftyp" + b"audio"),
        ("audio/wav", b"RIFFxxxxWAVE" + b"audio"),
    ],
)
def test_supported_audio_signatures_validate(mime: str, content: bytes):
    validate_audio_bytes(content, mime)


def test_audio_validation_rejects_mismatch_and_oversize():
    with pytest.raises(AudioValidationError):
        validate_audio_bytes(b"not audio", "audio/webm")
    with pytest.raises(AudioValidationError):
        validate_audio_bytes(b"OggS" + b"x", "audio/flac")
    with pytest.raises(AudioValidationError):
        validate_audio_bytes(b"", "audio/ogg")
    with pytest.raises(AudioValidationError):
        validate_audio_bytes(b"OggS" + b"x" * (25 * 1024 * 1024), "audio/ogg")


def test_audio_checksum_is_sha256_and_filename_is_not_a_storage_path():
    content = b"OggS synthetic audio"
    assert (
        SpeakingAudioService.checksum(content)
        == "750e9d06f1e582d4cc9bf209f0d6de140251bc8f5cf420e63927f5659724c7bb"
    )
    assert safe_audio_filename("../../candidate voice.webm") == ".._.._candidate_voice.webm"


def test_audio_runtime_payload_is_exact_media_asset_reference():
    item = {"content": {}}
    CandidateRuntimeService._validate_payload(
        item, PackResponseMode.AUDIO.value, {"media_asset_id": "asset-1"}
    )
    with pytest.raises(ValueError, match="invalid AUDIO response"):
        CandidateRuntimeService._validate_payload(
            item,
            PackResponseMode.AUDIO.value,
            {"media_asset_id": "asset-1", "text": "unexpected"},
        )
