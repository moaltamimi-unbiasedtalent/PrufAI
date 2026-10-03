import pytest

from app.speaking_audio.service import (
    AudioValidationError,
    SpeakingAudioService,
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


def test_audio_checksum_is_sha256_and_filename_is_not_a_storage_path():
    content = b"OggS synthetic audio"
    assert (
        SpeakingAudioService.checksum(content)
        == "750e9d06f1e582d4cc9bf209f0d6de140251bc8f5cf420e63927f5659724c7bb"
    )
