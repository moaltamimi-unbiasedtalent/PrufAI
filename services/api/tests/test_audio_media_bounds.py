"""Fail-closed process bounds; real codecs run in the required PG/MinIO gate."""

import sys

import pytest

from app.config import Settings
from app.speaking_audio.media import AudioMediaError, _run_bounded, validate_recorded_media


def test_validator_kills_a_process_that_exceeds_the_wall_clock() -> None:
    with pytest.raises(AudioMediaError, match="VALIDATION_TIMEOUT"):
        _run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], 1)


def test_validator_rejects_unbounded_diagnostic_output() -> None:
    with pytest.raises(AudioMediaError, match="VALIDATION_OUTPUT_LIMIT"):
        _run_bounded([sys.executable, "-c", "import sys; sys.stderr.write('x'*300000)"], 3)


def test_validator_never_returns_output_from_failed_decode() -> None:
    with pytest.raises(AudioMediaError, match="UNDECODABLE_OR_VALIDATOR_UNAVAILABLE"):
        _run_bounded([sys.executable, "-c", "print('progress=end'); raise SystemExit(1)"], 3)


def test_missing_native_runtime_is_retryable_unavailability() -> None:
    with pytest.raises(AudioMediaError, match="AUDIO.MEDIA_VALIDATOR_UNAVAILABLE"):
        validate_recorded_media(
            b"bounded original fixture",
            "audio/wav",
            {
                "max_upload_bytes": 1000,
                "max_recording_seconds": 30,
                "supported_mime_types": ["audio/wav"],
            },
            Settings(
                _env_file=None,
                audio_media_runtime="native",
                audio_ffprobe_executable="prufai-deliberately-absent-ffprobe",
            ),
        )


def test_conflicting_declared_codec_is_rejected_before_media_inspection() -> None:
    with pytest.raises(AudioMediaError, match="AUDIO.FORMAT_NOT_PERMITTED"):
        validate_recorded_media(
            b"bounded original fixture",
            'audio/webm; codecs="vorbis"',
            {
                "max_upload_bytes": 1000,
                "max_recording_seconds": 30,
                "supported_mime_types": ["audio/webm;codecs=opus"],
            },
            Settings(_env_file=None),
        )
