"""Real pinned-codec acceptance; required CI must execute, never silently skip."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import pytest

from app.config import Settings
from app.speaking_audio.media import (
    AudioMediaError,
    _FrameCounter,
    _inspect_command,
    validate_recorded_media,
)
from tests.integration_support.audio_evidence import technical_encoded_audio

REQUIRED = (
    os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
    or os.getenv("PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION") == "1"
)
pytestmark = pytest.mark.skipif(
    not os.getenv("AUDIO_MEDIA_RUNTIME") and not REQUIRED,
    reason="real audio inspection requires explicitly provisioned media runtime",
)


@pytest.fixture
def media_settings() -> Settings:
    if not os.getenv("AUDIO_MEDIA_RUNTIME"):
        pytest.fail("required media inspection needs AUDIO_MEDIA_RUNTIME and provisioned tools")
    return Settings(_env_file=None)


def policy(mime: str = "audio/webm;codecs=opus", seconds: int = 30) -> dict[str, object]:
    return {
        "max_upload_bytes": 2 * 1024 * 1024,
        "max_recording_seconds": seconds,
        "supported_mime_types": [mime],
    }


@pytest.mark.parametrize(
    "mime",
    ["audio/wav", "audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4;codecs=mp4a.40.2"],
)
def test_actual_short_supported_original_is_fully_inspected(media_settings, mime):
    content = technical_encoded_audio(media_settings, mime_type=mime)
    result = validate_recorded_media(content, mime, policy(mime), media_settings)
    assert 900 <= result.duration_ms <= 1200
    assert result.sha256 == hashlib.sha256(content).hexdigest()
    proof = result.validation_provenance
    assert proof["complete_input"] is True
    assert proof["decoded_frame_count"] > 0 and proof["inspected_frame_count"] > 0
    assert 0 < proof["decoded_presentation_span_ms"] <= 30_000
    assert proof["timestamp_rounding_tolerance"] == "at_most_one_original_timebase_tick"


@pytest.mark.parametrize("mime", ["audio/wav", "audio/webm;codecs=opus"])
def test_actual_original_exactly_at_limit_remains_valid(media_settings, mime):
    content = technical_encoded_audio(media_settings, duration_seconds=30, mime_type=mime)
    result = validate_recorded_media(content, mime, policy(mime), media_settings)
    assert result.duration_ms == 30_000
    assert result.validation_provenance["decoded_presentation_span_ms"] <= 30_000


def test_actual_contiguous_audio_over_limit_is_rejected(media_settings):
    content = technical_encoded_audio(media_settings, duration_seconds=31)
    with pytest.raises(AudioMediaError, match="OUTSIDE_POLICY"):
        validate_recorded_media(content, "audio/webm;codecs=opus", policy(), media_settings)


@pytest.mark.parametrize("decoded_seconds,gap_after", [(2, 1), (40, 1), (2, 0)])
def test_actual_timestamp_gap_never_validates_an_early_prefix(
    media_settings, decoded_seconds, gap_after
):
    # 40s demonstrates excess actual samples; 2s independently isolates excess
    # presentation span. Both previously passed as a 1054ms decoded prefix.
    content = technical_encoded_audio(
        media_settings,
        duration_seconds=decoded_seconds,
        timestamp_gap_seconds=60,
        gap_after_seconds=gap_after,
    )
    with pytest.raises(AudioMediaError, match="OUTSIDE_POLICY"):
        validate_recorded_media(content, "audio/webm;codecs=opus", policy(), media_settings)


@pytest.mark.parametrize("corruption", ["malformed", "truncated"])
def test_actual_malformed_or_truncated_original_is_rejected(media_settings, corruption):
    if corruption == "malformed":
        content = b"RIFF" + b"0" * 64
    else:
        content = technical_encoded_audio(media_settings, mime_type="audio/wav")[:-200]
    with pytest.raises(AudioMediaError):
        validate_recorded_media(content, "audio/wav", policy("audio/wav"), media_settings)


def test_actual_unsupported_container_is_rejected(media_settings):
    content = technical_encoded_audio(media_settings, mime_type="audio/mpeg")
    with pytest.raises(AudioMediaError):
        validate_recorded_media(content, "audio/webm;codecs=opus", policy(), media_settings)


def test_actual_unsupported_codec_is_rejected(media_settings):
    content = technical_encoded_audio(media_settings, codec_override="libvorbis")
    with pytest.raises(AudioMediaError, match="MISMATCH"):
        validate_recorded_media(content, "audio/webm;codecs=opus", policy(), media_settings)


def test_actual_decoder_timeout_never_accepts_a_processed_prefix(media_settings):
    content = technical_encoded_audio(media_settings, mime_type="audio/wav", duration_seconds=4)
    bounded = media_settings.model_copy(update={"audio_probe_timeout_seconds": 1})
    with tempfile.TemporaryDirectory(prefix="prufai-media-timeout-test-") as directory:
        source = Path(directory) / "original.wav"
        source.write_bytes(content)
        with pytest.raises(AudioMediaError, match="VALIDATION_TIMEOUT"):
            _inspect_command(
                bounded,
                "ffmpeg",
                [
                    "-v",
                    "warning",
                    "-nostdin",
                    "-threads",
                    "1",
                    "-guess_layout_max",
                    "0",
                    "-re",
                    "-copyts",
                    "-i",
                    str(source),
                    "-map",
                    "0:a:0",
                    "-c:a",
                    "pcm_s16le",
                    "-f",
                    "framecrc",
                    "-",
                ],
                source,
                _FrameCounter(30),
            )


def test_actual_decoder_sample_cap_rejects_instead_of_returning_prefix(media_settings):
    content = technical_encoded_audio(media_settings, mime_type="audio/wav", duration_seconds=3)
    with tempfile.TemporaryDirectory(prefix="prufai-media-sample-cap-test-") as directory:
        source = Path(directory) / "original.wav"
        source.write_bytes(content)
        with pytest.raises(AudioMediaError, match="DURATION_OUTSIDE_POLICY"):
            _inspect_command(
                media_settings,
                "ffmpeg",
                [
                    "-v",
                    "warning",
                    "-nostdin",
                    "-threads",
                    "1",
                    "-guess_layout_max",
                    "0",
                    "-copyts",
                    "-i",
                    str(source),
                    "-map",
                    "0:a:0",
                    "-c:a",
                    "pcm_s16le",
                    "-f",
                    "framecrc",
                    "-",
                ],
                source,
                _FrameCounter(1),
            )
