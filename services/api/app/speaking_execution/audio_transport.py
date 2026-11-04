"""Bounded provider rendition; the original private evidence is never replaced."""

from __future__ import annotations

import hashlib
import io
import math
import tempfile
import wave
from fractions import Fraction
from pathlib import Path
from typing import Any

from app.config import Settings
from app.speaking_audio.media import AudioMediaError, _inspect_command
from app.speaking_execution.provider import AudioTransport, AudioTransportError

TRANSFORM_VERSION = "PCM16_PRESERVE_TIMELINE_V1"
MAX_AUDIO_BYTES = 20_000_000


def prepare_audio(content: bytes, *, source: dict[str, Any], settings: Settings) -> AudioTransport:
    """Full decode to explicit PCM16, preserving rate/channels and relative timeline.

    This is a transport rendition, not a new CandidateMediaAsset or acoustic
    judgment. PCM16 quantization is explicit; no denoise, trimming, time stretch,
    synthetic speech, downmix or sample-rate conversion is performed.
    """
    provenance = source.get("validation_provenance", {})
    try:
        sha = source.get("audio_sha256", source.get("sha256"))
        duration = source["duration_ms"]
        sample_rate, channels = provenance["sample_rate"], provenance["channels"]
        tick = Fraction(provenance["timestamp_resolution_seconds"])
        if (
            not isinstance(content, bytes)
            or not 0 < len(content) <= 25_000_000
            or len(content) != source["byte_size"]
            or hashlib.sha256(content).hexdigest() != sha
            or provenance.get("source_sha256") != sha
            or provenance.get("source_byte_size") != len(content)
            or provenance.get("complete_input") is not True
            or provenance.get("validator") != "ffprobe_and_complete_ffmpeg_audio_decode"
            or type(duration) is not int
            or not 0 < duration <= 180_000
            or provenance.get("duration_ms") != duration
            or type(sample_rate) is not int
            or not 8000 <= sample_rate <= 48000
            or type(channels) is not int
            or channels not in {1, 2}
            or not 0 < tick <= Fraction(1, 1000)
            # Only continuous timelines are supported. At most one original
            # tick plus ceil-to-ms rounding is tolerated, never a hidden gap.
            or abs(provenance["original_presentation_span_ms"] - duration) > 1
            or abs(provenance["decoded_presentation_span_ms"] - duration) > 1
        ):
            raise AudioTransportError("AUDIO_TRANSPORT_SOURCE_UNSUPPORTED")
        cap = min(
            MAX_AUDIO_BYTES - 44, math.ceil((duration + 1) * sample_rate / 1000) * channels * 2
        )
        with tempfile.TemporaryDirectory(prefix="prufai-speaking-transport-") as temporary:
            original = Path(temporary) / "original.audio"
            original.write_bytes(content)
            original.chmod(0o600)
            pcm = _inspect_command(
                settings,
                "ffmpeg",
                [
                    "-v",
                    "warning",
                    "-xerror",
                    "-err_detect",
                    "explode",
                    "-nostdin",
                    "-threads",
                    "1",
                    "-guess_layout_max",
                    "0",
                    "-copyts",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    str(original),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-sn",
                    "-dn",
                    "-map_metadata",
                    "-1",
                    "-c:a",
                    "pcm_s16le",
                    "-f",
                    "s16le",
                    "-",
                ],
                original,
                maximum_output=cap,
            )
        if not pcm or len(pcm) % (channels * 2):
            raise AudioTransportError("AUDIO_TRANSPORT_INVALID_PCM")
        frames = len(pcm) // (channels * 2)
        rendered_duration = math.ceil(Fraction(frames * 1000, sample_rate))
        if abs(rendered_duration - duration) > 1:
            raise AudioTransportError("AUDIO_TRANSPORT_TIMELINE_MISMATCH")
        output = io.BytesIO()
        with wave.open(output, "wb") as writer:
            writer.setnchannels(channels)
            writer.setsampwidth(2)
            writer.setframerate(sample_rate)
            writer.writeframes(pcm)
        wav = output.getvalue()
        if len(wav) > MAX_AUDIO_BYTES:
            raise AudioTransportError("AUDIO_TRANSPORT_TOO_LARGE")
        return AudioTransport(
            wav,
            {
                "source_sha256": sha,
                "sha256": hashlib.sha256(wav).hexdigest(),
                "byte_size": len(wav),
                "format": "wav",
                "sample_format": "pcm_s16le",
                "sample_rate": sample_rate,
                "channels": channels,
                "frames": frames,
                "duration_ms": rendered_duration,
                "transform_version": TRANSFORM_VERSION,
                "converter_version": provenance["validator_version"],
                "timeline_policy": "CONTINUOUS_RELATIVE_FIRST_SAMPLE_NO_RATE_OR_CHANNEL_CHANGE",
                "original_first_pts_seconds": provenance["original_first_pts_seconds"],
                "source_remains_primary": True,
            },
        )
    except AudioTransportError:
        raise
    except (AudioMediaError, ValueError, TypeError, KeyError, ZeroDivisionError, OSError) as exc:
        raise AudioTransportError("AUDIO_TRANSPORT_UNSUPPORTED") from exc
