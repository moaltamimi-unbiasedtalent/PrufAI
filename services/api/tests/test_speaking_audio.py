from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.models.enums import PackResponseMode
from app.runtime.api import _audio_response, _parse_audio_range
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


def _request_with_range(value: str | None) -> Request:
    headers = [] if value is None else [(b"range", value.encode())]
    return Request({"type": "http", "headers": headers})


def test_audio_range_parser_supports_open_ended_and_suffix_ranges():
    assert _parse_audio_range("bytes=100-", 500) == (100, 499, True)
    assert _parse_audio_range("bytes=-100", 500) == (400, 499, True)
    with pytest.raises(HTTPException):
        _parse_audio_range("bytes=500-", 500)


def test_audio_range_response_uses_bounded_storage_read():
    class RangeStorage:
        def __init__(self) -> None:
            self.full_reads = 0
            self.range_reads: list[tuple[int, int]] = []

        def size(self, key: str) -> int:
            assert key == "audio/key"
            return 1000

        def get(self, key: str) -> bytes:
            self.full_reads += 1
            return b"x" * 1000

        def read_range(self, key: str, start: int, end: int) -> bytes:
            self.range_reads.append((start, end))
            return b"x" * (end - start + 1)

    storage = RangeStorage()
    response = _audio_response(
        asset=SimpleNamespace(storage_key="audio/key", content_type="audio/webm"),
        request=_request_with_range("bytes=100-199"),
        storage=storage,
    )
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 100-199/1000"
    assert storage.range_reads == [(100, 199)]
    assert storage.full_reads == 0
