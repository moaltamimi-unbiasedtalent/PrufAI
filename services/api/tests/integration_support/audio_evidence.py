"""Original technical PCM tone: decodable media, never speech-recognition evidence."""

from __future__ import annotations

import io
import math
import struct
import subprocess
import wave

from app.config import Settings


def technical_tone_wav(*, duration_seconds: float = 1.0, frequency_hz: int = 440) -> bytes:
    sample_rate = 16_000
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(sample_rate)
        recording.writeframes(
            b"".join(
                struct.pack(
                    "<h", int(4_000 * math.sin(2 * math.pi * frequency_hz * sample / sample_rate))
                )
                for sample in range(int(sample_rate * duration_seconds))
            )
        )
    return output.getvalue()


def technical_encoded_audio(
    settings: Settings,
    *,
    duration_seconds: float = 1.0,
    mime_type: str = "audio/webm;codecs=opus",
    timestamp_gap_seconds: float = 0.0,
    gap_after_seconds: float = 1.0,
    codec_override: str | None = None,
) -> bytes:
    """Original generated fixture only; production evidence is never transcoded."""
    if not 0 < duration_seconds <= 600 or not 0 <= timestamp_gap_seconds <= 600:
        raise ValueError("technical media fixture exceeds its bounded generation window")
    if mime_type == "audio/wav" and not timestamp_gap_seconds and not codec_override:
        return technical_tone_wav(duration_seconds=duration_seconds)
    formats = {
        "audio/webm;codecs=opus": ("libopus", ["-f", "webm"]),
        "audio/ogg;codecs=opus": ("libopus", ["-f", "ogg"]),
        "audio/mp4;codecs=mp4a.40.2": (
            "aac",
            ["-movflags", "frag_keyframe+empty_moov", "-f", "mp4"],
        ),
        "audio/mpeg": ("libmp3lame", ["-f", "mp3"]),
    }
    codec, muxer = formats[mime_type]
    command = [settings.audio_ffmpeg_executable]
    if settings.audio_media_runtime == "docker":
        command = [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=32",
            "--cpus=1",
            "--memory=512m",
            "--entrypoint",
            "/usr/bin/ffmpeg",
            settings.audio_media_image,
        ]
    command += [
        "-v",
        "error",
        "-nostdin",
        "-threads",
        "1",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=16000:duration={duration_seconds}",
    ]
    if timestamp_gap_seconds:
        command += [
            "-af",
            f"asetpts=PTS+if(gte(T\\,{gap_after_seconds})\\,{timestamp_gap_seconds}/TB\\,0)",
        ]
    command += ["-c:a", codec_override or codec, *muxer, "pipe:1"]
    completed = subprocess.run(command, capture_output=True, timeout=45)
    if completed.returncode or not 0 < len(completed.stdout) <= 25 * 1024 * 1024:
        raise RuntimeError("original technical fixture encoding failed")
    return completed.stdout
