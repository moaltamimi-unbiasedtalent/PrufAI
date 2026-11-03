"""Bounded inspection of original audio; this module never transcodes evidence."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings

MAX_INSPECTION_OUTPUT = 256 * 1024
FORMATS: dict[str, tuple[str, str]] = {
    "audio/webm;codecs=opus": ("webm", "opus"),
    "audio/ogg;codecs=opus": ("ogg", "opus"),
    "audio/mp4;codecs=mp4a.40.2": ("mp4", "aac"),
    "audio/wav": ("wav", "pcm_s16le"),
}


class AudioMediaError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedAudio:
    duration_ms: int
    sha256: str
    byte_size: int
    validation_provenance: dict[str, Any]


class _FrameCounter:
    """Fold decoder metadata incrementally; retain neither frames nor PCM."""

    def __init__(self) -> None:
        self.pending = bytearray()
        self.numerator = 0
        self.denominator = 0
        self.samples = 0
        self.frames = 0

    def add(self, chunk: bytes) -> None:
        self.pending.extend(chunk)
        while b"\n" in self.pending:
            line, _, remainder = self.pending.partition(b"\n")
            self.pending = bytearray(remainder)
            if len(line) > 4096:
                raise AudioMediaError("AUDIO.MEDIA_VALIDATION_OUTPUT_LIMIT")
            timebase = re.fullmatch(rb"#tb 0: (\d+)/(\d+)", line)
            if timebase:
                self.numerator, self.denominator = map(int, timebase.groups())
            elif line and not line.startswith(b"#"):
                fields = line.split(b",")
                try:
                    if len(fields) != 6 or int(fields[0]) != 0 or int(fields[3]) <= 0:
                        raise ValueError("invalid frame")
                    self.samples += int(fields[3])
                    self.frames += 1
                except ValueError as exc:
                    raise AudioMediaError("AUDIO.DECODED_DURATION_UNAVAILABLE") from exc
                if self.frames > 2_000_000:
                    raise AudioMediaError("AUDIO.MEDIA_VALIDATION_OUTPUT_LIMIT")
        if len(self.pending) > 4096:
            raise AudioMediaError("AUDIO.MEDIA_VALIDATION_OUTPUT_LIMIT")

    def result(self) -> bytes:
        if self.pending or not self.frames or not self.numerator or not self.denominator:
            raise AudioMediaError("AUDIO.DECODED_DURATION_UNAVAILABLE")
        return json.dumps(
            {
                "samples": self.samples,
                "numerator": self.numerator,
                "denominator": self.denominator,
            }
        ).encode()


def _run_bounded(
    command: list[str],
    timeout: int,
    source: Path | None = None,
    summarize_frames: bool = False,
) -> bytes:
    """Drain both pipes under one wall clock and output limit, without logging them."""
    input_file = source.open("rb") if source else None
    try:
        process = subprocess.Popen(
            command,
            stdin=input_file if input_file else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        if input_file:
            input_file.close()
        raise AudioMediaError("AUDIO.MEDIA_VALIDATOR_UNAVAILABLE") from exc
    output = bytearray()
    counter = _FrameCounter() if summarize_frames else None
    total = 0
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, True)
    selector.register(process.stderr, selectors.EVENT_READ, False)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AudioMediaError("AUDIO.MEDIA_VALIDATION_TIMEOUT")
            for key, _mask in selector.select(min(remaining, 0.25)):
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data and counter:
                    counter.add(chunk)
                    continue
                total += len(chunk)
                if total > MAX_INSPECTION_OUTPUT:
                    raise AudioMediaError("AUDIO.MEDIA_VALIDATION_OUTPUT_LIMIT")
                if key.data:
                    output.extend(chunk)
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if process.returncode != 0:
            if command[0] == "docker" and process.returncode in {125, 126, 127}:
                raise AudioMediaError("AUDIO.MEDIA_VALIDATOR_UNAVAILABLE")
            raise AudioMediaError("AUDIO.MEDIA_UNDECODABLE_OR_VALIDATOR_UNAVAILABLE")
        return counter.result() if counter else bytes(output)
    except subprocess.TimeoutExpired as exc:
        raise AudioMediaError("AUDIO.MEDIA_VALIDATION_TIMEOUT") from exc
    finally:
        selector.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        process.stdout.close()
        process.stderr.close()
        if input_file:
            input_file.close()


def _inspect_command(settings: Settings, tool: str, arguments: list[str], source: Path) -> bytes:
    container_name = "prufai-media-" + uuid.uuid4().hex
    if settings.audio_media_runtime == "docker":
        command = [
            "docker",
            "run",
            "--interactive",
            "--rm",
            "--pull=never",
            "--name",
            container_name,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=32",
            "--cpus=1",
            f"--memory={settings.audio_probe_memory_mb}m",
            "--user=65534:65534",
            "--tmpfs=/tmp:size=8m,mode=1777",
            "--tmpfs=/input:size=26m,mode=1777",
            "--entrypoint",
            "/bin/sh",
            settings.audio_media_image,
            "-c",
            'cat > /input/audio && exec "$@"',
            "audio-inspection",
            f"/usr/bin/{tool}",
            *["/input/audio" if argument == str(source) else argument for argument in arguments],
        ]
    else:
        executable = (
            settings.audio_ffprobe_executable
            if tool == "ffprobe"
            else settings.audio_ffmpeg_executable
        )
        if shutil.which(executable) is None:
            raise AudioMediaError("AUDIO.MEDIA_VALIDATOR_UNAVAILABLE")
        # Limits are set in a separate Python child before exec, avoiding
        # preexec_fn in the API's multithreaded process.
        limits = (
            "import os,resource,sys; "
            "m=int(sys.argv[1]); t=int(sys.argv[2]); "
            "resource.setrlimit(resource.RLIMIT_AS,(m,m)); "
            "resource.setrlimit(resource.RLIMIT_CPU,(t,t+1)); "
            "resource.setrlimit(resource.RLIMIT_CORE,(0,0)); "
            "os.execvp(sys.argv[3],sys.argv[3:])"
        )
        command = [
            sys.executable,
            "-c",
            limits,
            str(settings.audio_probe_memory_mb * 1024 * 1024),
            str(settings.audio_probe_timeout_seconds),
            executable,
            *arguments,
        ]
    try:
        return _run_bounded(
            command,
            settings.audio_probe_timeout_seconds,
            source if settings.audio_media_runtime == "docker" else None,
            summarize_frames=tool == "ffmpeg",
        )
    finally:
        if settings.audio_media_runtime == "docker":
            # A killed Docker client must not leave its bounded inspection
            # container alive. This name belongs only to the current invocation.
            try:
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass


def validate_recorded_media(
    content: bytes,
    declared_mime: str,
    policy: dict[str, Any],
    settings: Settings | None = None,
) -> ValidatedAudio:
    configuration = settings or get_settings()
    maximum_bytes = int(policy["max_upload_bytes"])
    maximum_seconds = int(policy["max_recording_seconds"])
    if not content or len(content) > min(maximum_bytes, 25 * 1024 * 1024):
        raise AudioMediaError("AUDIO.EMPTY_OR_OVERSIZED")
    declared_base = declared_mime.split(";", 1)[0].strip().lower()
    declared_codecs = [
        parameter.split("=", 1)[1].strip().strip('"').lower()
        for parameter in declared_mime.split(";")[1:]
        if parameter.strip().lower().startswith("codecs=")
    ]
    permitted = [
        (mime, FORMATS[mime])
        for mime in policy["supported_mime_types"]
        if mime in FORMATS
        and mime.split(";", 1)[0] == declared_base
        and (not declared_codecs or declared_codecs == [mime.split(";codecs=", 1)[-1]])
    ]
    if not permitted:
        raise AudioMediaError("AUDIO.FORMAT_NOT_PERMITTED")
    with tempfile.TemporaryDirectory(prefix="prufai-audio-inspection-") as temporary:
        source = Path(temporary) / "original.audio"
        source.write_bytes(content)
        source.chmod(0o644)
        try:
            raw = _inspect_command(
                configuration,
                "ffprobe",
                [
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-show_entries",
                    "format=format_name:stream=codec_type,codec_name,sample_rate,channels",
                    "-of",
                    "json",
                    str(source),
                ],
                source,
            )
            probe = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AudioMediaError("AUDIO.MEDIA_PROBE_INVALID") from exc
        streams = probe.get("streams", [])
        if len(streams) != 1 or streams[0].get("codec_type") != "audio":
            raise AudioMediaError("AUDIO.EXACTLY_ONE_AUDIO_STREAM_REQUIRED")
        stream = streams[0]
        codec = str(stream.get("codec_name", ""))
        containers = str(probe.get("format", {}).get("format_name", "")).split(",")
        matched = next(
            (
                (mime, container)
                for mime, (container, allowed_codec) in permitted
                if container in containers and codec == allowed_codec
            ),
            None,
        )
        if matched is None:
            raise AudioMediaError("AUDIO.CONTAINER_CODEC_MIME_MISMATCH")
        try:
            sample_rate = int(stream["sample_rate"])
            channels = int(stream["channels"])
        except (ValueError, TypeError, KeyError) as exc:
            raise AudioMediaError("AUDIO.INVALID_STREAM_METADATA") from exc
        if not 8000 <= sample_rate <= 48000 or channels not in {1, 2}:
            raise AudioMediaError("AUDIO.UNSUPPORTED_STREAM_PARAMETERS")
        decoded_frames = _inspect_command(
            configuration,
            "ffmpeg",
            [
                "-v",
                "error",
                "-xerror",
                "-err_detect",
                "explode",
                "-nostdin",
                "-threads",
                "1",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-t",
                str(maximum_seconds + 1),
                "-c:a",
                "pcm_s16le",
                "-f",
                "framecrc",
                "-",
            ],
            source,
        )
        # Packet end timestamps can omit the duration of the final decoded
        # frame. Sum actual decoded frame sample counts in the PCM time base.
        # Only frame metadata/CRC leaves the decoder; PCM and speech never do.
        try:
            counts = json.loads(decoded_frames)
            duration_ms = math.ceil(
                int(counts["samples"])
                * int(counts["numerator"])
                * 1000
                / int(counts["denominator"])
            )
        except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
            raise AudioMediaError("AUDIO.DECODED_DURATION_UNAVAILABLE") from exc
        if duration_ms <= 0 or duration_ms > maximum_seconds * 1000:
            raise AudioMediaError("AUDIO.DURATION_OUTSIDE_POLICY")
        version = _inspect_command(configuration, "ffprobe", ["-version"], source)
        version_line = version.decode("utf-8", errors="replace").splitlines()[0][:160]
    digest = hashlib.sha256(content).hexdigest()
    return ValidatedAudio(
        duration_ms=duration_ms,
        sha256=digest,
        byte_size=len(content),
        validation_provenance={
            "validator": "ffprobe_and_complete_ffmpeg_audio_decode",
            "validator_version": version_line,
            "runtime": configuration.audio_media_runtime,
            "container": matched[1],
            "codec": codec,
            "sample_rate": sample_rate,
            "channels": channels,
            "duration_source": "decoded_pcm_frame_samples",
            "duration_ms": duration_ms,
            "source_sha256": digest,
            "source_byte_size": len(content),
        },
    )
